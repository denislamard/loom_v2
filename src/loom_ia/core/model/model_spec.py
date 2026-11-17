# SPDX-License-Identifier: Apache-2.0
"""Définition d'un LLM, déclarée sous ``models`` dans ``loom.yaml`` (points 9 et 10, fonctions B1 à B9).

Le ``sdk`` choisit l'adaptateur ; le fournisseur n'est qu'une affaire de
config (``base_url``, clé, capacités). La config ne contient jamais la clé,
seulement le nom de la variable qui la porte (``api_key_env``). Un agent, un
rôle ou un juge désigne un modèle par son ``id``.

Sans ``base_url``, l'adaptateur prend l'adresse officielle du fournisseur de
son SDK, jamais celle d'une variable d'environnement (``ANTHROPIC_BASE_URL``,
``OPENAI_BASE_URL``) : seule la config décide où partent les requêtes.

Exemple dans ``loom.yaml`` ::

    models:
    - id: HAIKU
      sdk: anthropic
      model: claude-haiku-5-5
      api_key_env: ANTHROPIC_API_KEY
      max_tokens: 4096
      capabilities: { vision: true, context_window: 200000 }
      pricing: { input: 1.0, output: 5.0 }
    - id: QWEN
      sdk: openai
      model: qwen3-32b
      base_url: http://localhost:8000/v1
      capabilities: { thinking: true }

Contenu : ``ModelSpec`` et ses blocs ``ModelTimeouts``, ``RetryPolicy``,
``CircuitBreaker``, ``PromptCache``, ``ModelCapabilities``.
"""

from typing import Literal, Self

from pydantic import Field, JsonValue, NonNegativeFloat, PositiveFloat, PositiveInt, model_validator

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.media import ImageFormat
from loom_ia.core.model.usage import Pricing

type Sdk = Literal["anthropic", "openai", "fake"]
"""Adaptateur d'un modèle : ``anthropic`` (aussi MiniMax), ``openai`` (aussi Together, vLLM, Ollama) ou ``fake`` (modèle simulé des tests)."""
type CacheTtl = Literal["5m", "1h"]
"""Durée de vie d'une entrée du cache de prompt (fonction B5)."""
type ModelApi = Literal["chat", "responses"]
"""API d'un modèle ``sdk: openai`` : ``chat`` (Chat Completions) ou ``responses``."""
type ImageInput = Literal["base64", "url", "file_id"]
"""Forme sous laquelle un modèle accepte une image (point 14)."""


class ModelTimeouts(DomainModel):
    """Délais d'un appel de modèle (``models[].timeouts``), en secondes ; ``None`` désactive un délai.

    Un délai dépassé est une erreur ``transient`` : il donne lieu à de
    nouvelles tentatives, puis à la bascule vers le secours.

    Attributes:
        first_token: Attente au plus du premier morceau du flux. Défaut : 60.
        idle: Attente au plus entre deux morceaux. Défaut : 60.
        total: Durée au plus de l'appel complet, flux compris. Défaut : 300.
    """

    first_token: PositiveFloat | None = 60.0
    idle: PositiveFloat | None = 60.0
    total: PositiveFloat | None = 300.0


class RetryPolicy(DomainModel):
    """Nouvelles tentatives sur les erreurs ``transient`` et ``overloaded``.

    Délai de la tentative n+1 : ``initial_delay * multiplier ** (n - 1)``, plafonné à
    ``max_delay``, puis tiré au hasard entre la moitié et la totalité de cette
    valeur. Un ``Retry-After`` du fournisseur remplace ce calcul ; s'il dépasse
    ``max_delay``, l'appel est abandonné. Chaque tentative ratée écrit un
    ``model.retried``. Se déclare sous ``models[].retry``.

    Attributes:
        max_attempts: Nombre total de tentatives, la première comprise.
            Défaut : 3.
        initial_delay: Attente avant la deuxième tentative, en secondes.
            Défaut : 1.
        multiplier: Facteur appliqué à chaque tentative suivante, 1 au moins.
            Défaut : 2.
        max_delay: Plafond d'une attente, en secondes. Défaut : 30.
    """

    max_attempts: PositiveInt = 3
    initial_delay: NonNegativeFloat = 1.0
    multiplier: float = Field(default=2.0, ge=1.0)
    max_delay: NonNegativeFloat = 30.0

    def backoff(self, attempt: int, jitter: float) -> float:
        """Attente après l'échec d'une tentative, avant la suivante.

        Args:
            attempt: Numéro de la tentative qui a échoué, à partir de 1.
            jitter: Valeur tirée au hasard dans [0, 1].

        Returns:
            L'attente, en secondes, entre la moitié et la totalité de
            ``min(max_delay, initial_delay * multiplier ** (attempt - 1))``.
        """
        base = min(self.max_delay, self.initial_delay * self.multiplier ** (attempt - 1))
        return base * (0.5 + jitter / 2)


class CircuitBreaker(DomainModel):
    """Disjoncteur d'un modèle ou d'un serveur MCP (#10, #19, backlog #011).

    Après ``failures`` échecs de suite, la cible est écartée pendant
    ``cooldown`` secondes, pour tous les runs de l'instance : un modèle passe
    directement à son secours, un serveur MCP est indisponible. Ensuite, un
    essai : réussi, le disjoncteur se referme ; raté, il se rouvre. L'ouverture
    écrit un ``circuit.opened``. Se déclare sous ``models[].circuit_breaker`` ou
    ``mcp_servers[].circuit_breaker`` ; ``null`` le retire.

    Attributes:
        failures: Échecs de suite qui ouvrent le disjoncteur. Défaut : 5.
        cooldown: Durée pendant laquelle la cible est écartée, en secondes.
            Défaut : 60.
    """

    failures: PositiveInt = 5
    cooldown: PositiveFloat = 60.0


class PromptCache(DomainModel):
    """Points de cache de prompt d'un modèle ``sdk: anthropic`` (``models[].cache``, fonction B5).

    ``system`` : sur le prompt système ; ``tools`` : sur le dernier outil ;
    ``messages`` : sur le dernier bloc de la conversation, un point qui avance
    à chaque appel. L'adaptateur les traduit en ``cache_control`` (au plus
    quatre par requête, blocs marqués ``cache_breakpoint`` compris). Le prix
    d'écriture (``pricing.cache_write``) est celui de la durée choisie.

    Les API OpenAI et leurs compatibles cachent seules : ``cache`` y est refusé.

    Attributes:
        system: Point de cache sur le prompt système. Défaut : faux.
        tools: Point de cache sur le dernier outil. Défaut : faux.
        messages: Point de cache sur le dernier bloc de la conversation, qui
            avance à chaque appel. Défaut : faux.
        ttl: Durée de vie d'une entrée : ``5m`` (défaut) ou ``1h``.
    """

    system: bool = False
    tools: bool = False
    messages: bool = False
    ttl: CacheTtl = "5m"


class ModelCapabilities(DomainModel):
    """Capacités déclarées d'un modèle (``models[].capabilities``), contrôlées au démarrage (fonction B9).

    Images (#14) : un modèle sans ``vision`` n'en reçoit qu'une mention
    textuelle. Pour un modèle avec ``vision``, chaque image est lue dans le
    stockage d'artefacts et envoyée en base64, après contrôle de son format
    (``image_formats``) et de sa taille (``max_image_bytes``).

    ``tools`` : le modèle sait appeler des outils ; exigé d'un orchestrateur
    qui en a, d'un juge (verdict par outil imposé) et de leurs secours.
    ``thinking`` : il produit un raisonnement et l'attend en retour pendant
    ses tours d'outils ; avec l'API Chat, le raisonnement de la boucle en
    cours lui est renvoyé (#7, backlog #015). ``native_json`` : il accepte un
    schéma de sortie JSON ; celui du contrat lui est transmis pour un appel
    sans outils (B9). ``forced_tool_choice`` : il accepte qu'on lui impose
    d'appeler un outil (``tool_choice: required``) ; sans elle, une requête qui
    l'impose part en ``auto`` (``engine.fallback.sendable``). Claude Opus 5.5
    et Fable 5.1 refusent l'outil imposé (400) : le verdict d'un juge leur est
    alors demandé par la consigne seule.

    Attributes:
        tools: Le modèle sait appeler des outils. Défaut : vrai.
        thinking: Il produit un raisonnement et l'attend en retour. Défaut :
            faux.
        native_json: Il accepte un schéma de sortie JSON. Défaut : faux.
        forced_tool_choice: Il accepte ``tool_choice: required``. Défaut :
            vrai.
        streaming: Faux : appel non streamé, dont la réponse est rejouée en
            un flux simulé. Défaut : vrai.
        context_window: Fenêtre en tokens ; vérifiée avant l'appel si elle
            est déclarée (fonction B7), et utilisée pour choisir un secours en
            cas de ``context_overflow``. Défaut : non déclarée.
        vision: Le modèle reçoit les images. Défaut : faux.
        image_input: Formes d'image acceptées ; loom n'envoie pour l'instant
            qu'en ``base64``, qui doit y figurer avec ``vision``. Défaut :
            ``[base64]``.
        image_formats: Formats d'image acceptés. Défaut : jpeg, png, gif,
            webp.
        max_image_bytes: Taille maximale d'une image, en octets. Défaut :
            pas de limite.
        tool_result_media: Images acceptées dans un résultat d'outil ; sinon,
            elles suivent les résultats dans un message utilisateur. Défaut :
            faux.

    Raises:
        ValueError: ``vision`` sans ``base64`` dans ``image_input``.
    """

    tools: bool = True
    thinking: bool = False
    native_json: bool = False
    forced_tool_choice: bool = True

    streaming: bool = True
    context_window: PositiveInt | None = None
    vision: bool = False
    image_input: tuple[ImageInput, ...] = ("base64",)
    image_formats: tuple[ImageFormat, ...] = ("jpeg", "png", "gif", "webp")
    max_image_bytes: PositiveInt | None = None
    tool_result_media: bool = False

    @model_validator(mode="after")
    def _check_images(self) -> Self:
        """Refuse ``vision`` sans l'envoi en base64, seul pris en charge."""
        if self.vision and "base64" not in self.image_input:
            raise ValueError(
                "image_input : seul l'envoi en base64 est pris en charge pour l'instant (url et file_id viendront avec le stockage distant) ; ajouter base64"
            )
        return self


class ModelSpec(DomainModel):
    """Un modèle déclaré sous ``models`` dans ``loom.yaml``.

    Attributes:
        id: Identifiant du modèle dans la config (``HAIKU``), par lequel un
            agent, un rôle, un juge ou une chaîne de secours le désigne.
            Recopié en ``model`` dans les événements.
        sdk: Adaptateur : ``anthropic``, ``openai`` ou ``fake``.
        api: Pour ``sdk: openai`` seulement : ``chat`` (défaut) ou
            ``responses``.
        model: Nom du modèle chez le fournisseur (``claude-haiku-5-5``),
            recopié en ``model_id`` dans les événements.
        base_url: Point d'accès ; sans lui, l'adresse officielle du
            fournisseur du SDK (jamais celle d'une variable d'environnement).
        api_key_env: Nom de la variable d'environnement qui contient la clé ;
            un client peut la rediriger vers la sienne (``secrets``).
        max_tokens: Plafond de tokens de sortie par appel ; un emplacement
            peut le surcharger. Une sortie coupée sur cette limite est une
            erreur ``truncated``.
        params: Réglages transmis tels quels au fournisseur (température,
            thinking…). Une surcharge remplace une clé entière, pas son
            contenu (fonction B6).
        timeouts: Délais de l'appel (``ModelTimeouts``).
        retry: Nouvelles tentatives (``RetryPolicy``).
        capabilities: Capacités déclarées (``ModelCapabilities``).
        pricing: Grille tarifaire, en dollars par million de tokens
            (``Pricing``). Sans tarif, les appels comptent 0 $.
        circuit_breaker: Disjoncteur (point 10) ; actif par défaut (5 échecs,
            60 s), ``null`` le retire.
        cache: Points de cache de prompt, pour ``sdk: anthropic`` seulement.

    Raises:
        ValueError: ``api`` donné avec un autre ``sdk`` qu'``openai`` ; ou
            ``cache`` donné avec ``sdk: openai``.
    """

    id: str = Field(min_length=1)
    sdk: Sdk
    api: ModelApi | None = None
    model: str = Field(min_length=1)
    base_url: str | None = None
    api_key_env: str | None = None
    max_tokens: PositiveInt | None = None
    params: dict[str, JsonValue] = Field(default_factory=dict)
    timeouts: ModelTimeouts = ModelTimeouts()
    retry: RetryPolicy = RetryPolicy()
    capabilities: ModelCapabilities = ModelCapabilities()
    pricing: Pricing = Pricing()
    circuit_breaker: CircuitBreaker | None = CircuitBreaker()
    cache: PromptCache | None = None

    @model_validator(mode="after")
    def _check_api(self) -> Self:
        """Refuse ``api`` hors de ``sdk: openai``, et ``cache`` avec ``sdk: openai``."""
        if self.api is not None and self.sdk != "openai":
            raise ValueError(f"Modèle {self.id!r} : le champ 'api' n'existe que pour sdk: openai")
        if self.cache is not None and self.sdk == "openai":
            raise ValueError(f"Modèle {self.id!r} : 'cache' pose des points de cache pour sdk: anthropic ; les API OpenAI et leurs compatibles cachent seules")
        return self

    @property
    def effective_api(self) -> ModelApi | None:
        """API utilisée : ``chat`` par défaut avec ``sdk: openai``."""
        if self.sdk != "openai":
            return None
        return self.api or "chat"

# SPDX-License-Identifier: Apache-2.0
"""Définition d'un LLM (#9, #10, B3, B6, B7).

Le ``sdk`` choisit l'adaptateur ; le fournisseur n'est qu'une affaire de
config (``base_url``, clé, capacités). La config ne contient jamais la clé,
seulement le nom de la variable qui la porte (``api_key_env``).

Sans ``base_url``, l'adaptateur prend l'adresse officielle du fournisseur de
son SDK, jamais celle d'une variable d'environnement (``ANTHROPIC_BASE_URL``,
``OPENAI_BASE_URL``) : seule la config décide où partent les requêtes.
"""

from typing import Literal, Self

from pydantic import Field, JsonValue, NonNegativeFloat, PositiveFloat, PositiveInt, model_validator

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.media import ImageFormat
from loom_ia.core.model.usage import Pricing

type Sdk = Literal["anthropic", "openai", "fake"]
type ModelApi = Literal["chat", "responses"]
# Formes sous lesquelles un modèle accepte une image (#14).
type ImageInput = Literal["base64", "url", "file_id"]


class ModelTimeouts(DomainModel):
    """Délais d'un appel, en secondes ; ``None`` désactive le délai."""

    # Avant le premier morceau du flux.
    first_token: PositiveFloat | None = 60.0
    # Entre deux morceaux.
    idle: PositiveFloat | None = 60.0
    # Appel complet, flux compris.
    total: PositiveFloat | None = 300.0


class RetryPolicy(DomainModel):
    """Nouvelles tentatives sur les erreurs ``transient`` et ``overloaded``.

    Délai de la tentative n+1 : ``initial_delay * multiplier ** (n - 1)``, plafonné à
    ``max_delay``, puis tiré au hasard entre la moitié et la totalité de cette
    valeur. Un ``Retry-After`` du fournisseur remplace ce calcul ; s'il dépasse
    ``max_delay``, l'appel est abandonné.
    """

    # Nombre total de tentatives, la première comprise.
    max_attempts: PositiveInt = 3
    initial_delay: NonNegativeFloat = 1.0
    multiplier: float = Field(default=2.0, ge=1.0)
    max_delay: NonNegativeFloat = 30.0

    def backoff(self, attempt: int, jitter: float) -> float:
        """Attente après l'échec de la tentative ``attempt`` ; ``jitter`` est tiré dans [0, 1]."""
        base = min(self.max_delay, self.initial_delay * self.multiplier ** (attempt - 1))
        return base * (0.5 + jitter / 2)


class CircuitBreaker(DomainModel):
    """Disjoncteur d'un modèle ou d'un serveur MCP (#10, #19, backlog #011).

    Après ``failures`` échecs de suite, la cible est écartée pendant
    ``cooldown`` secondes, pour tous les runs de l'instance : un modèle passe
    directement à son secours, un serveur MCP est indisponible. Ensuite, un
    essai : réussi, le disjoncteur se referme ; raté, il se rouvre.
    """

    failures: PositiveInt = 5
    cooldown: PositiveFloat = 60.0


class ModelCapabilities(DomainModel):
    """Capacités déclarées, contrôlées au démarrage (B9).

    Images (#14) : un modèle sans ``vision`` n'en reçoit qu'une mention
    textuelle. Pour un modèle avec ``vision``, chaque image est lue dans le
    stockage d'artefacts et envoyée en base64, après contrôle de son format
    (``image_formats``) et de sa taille (``max_image_bytes``).

    ``tools`` : le modèle sait appeler des outils ; exigé d'un orchestrateur
    qui en a, d'un juge (verdict par outil imposé) et de leurs secours.
    ``thinking`` : il produit un raisonnement. ``native_json`` : il accepte un
    schéma de sortie JSON (utilisé à partir de la phase 3.5b).
    """

    tools: bool = True
    thinking: bool = False
    native_json: bool = False

    # False : appel non streamé, dont la réponse est rejouée en un flux simulé.
    streaming: bool = True
    # Fenêtre en tokens ; vérifiée avant l'appel si elle est déclarée (B7).
    context_window: PositiveInt | None = None
    vision: bool = False
    # Formes acceptées par le modèle ; loom-ia n'envoie pour l'instant qu'en base64.
    image_input: tuple[ImageInput, ...] = ("base64",)
    image_formats: tuple[ImageFormat, ...] = ("jpeg", "png", "gif", "webp")
    # Taille maximale d'une image, en octets.
    max_image_bytes: PositiveInt | None = None
    # Images acceptées dans un résultat d'outil ; sinon, elles suivent les
    # résultats dans un message utilisateur.
    tool_result_media: bool = False

    @model_validator(mode="after")
    def _check_images(self) -> Self:
        if self.vision and "base64" not in self.image_input:
            raise ValueError(
                "image_input : seul l'envoi en base64 est pris en charge pour l'instant "
                "(url et file_id viendront avec le stockage distant) ; ajouter base64"
            )
        return self


class ModelSpec(DomainModel):
    id: str = Field(min_length=1)
    sdk: Sdk
    # Pour ``sdk: openai`` seulement ; ``chat`` par défaut.
    api: ModelApi | None = None
    # Nom du modèle chez le fournisseur.
    model: str = Field(min_length=1)
    # Point d'accès ; sans lui, l'adresse officielle du fournisseur du SDK.
    base_url: str | None = None
    # Variable d'environnement qui contient la clé.
    api_key_env: str | None = None
    max_tokens: PositiveInt | None = None
    # Bloc transmis tel quel au fournisseur ; une surcharge remplace une clé entière (B6).
    params: dict[str, JsonValue] = Field(default_factory=dict)
    timeouts: ModelTimeouts = ModelTimeouts()
    retry: RetryPolicy = RetryPolicy()
    capabilities: ModelCapabilities = ModelCapabilities()
    pricing: Pricing = Pricing()
    # Disjoncteur (#10) ; ``null`` le retire.
    circuit_breaker: CircuitBreaker | None = CircuitBreaker()

    @model_validator(mode="after")
    def _check_api(self) -> Self:
        if self.api is not None and self.sdk != "openai":
            raise ValueError(f"Modèle {self.id!r} : le champ 'api' n'existe que pour sdk: openai")
        return self

    @property
    def effective_api(self) -> ModelApi | None:
        """API utilisée : ``chat`` par défaut avec ``sdk: openai``."""
        if self.sdk != "openai":
            return None
        return self.api or "chat"

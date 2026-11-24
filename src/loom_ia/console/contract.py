# SPDX-License-Identifier: Apache-2.0
"""Contrat du canal entre loom et la console : enveloppe, messages, schémas (contrat 1).

Le numéro de contrat est indépendant de la version du paquet. Il couvre
l'enveloppe, la poignée de main, le format des événements exportés, les
commandes et leurs résultats ; il ne couvre pas le format interne du journal.

- Ajouter un champ facultatif ou un type d'événement, qu'une console qui ne le
  connaît pas ignore : même contrat.
- Retirer ou renommer un champ, changer le sens d'une commande : contrat
  suivant ; loom peut en servir deux à la fois pendant une transition.

Chaque message est un objet JSON, une trame texte : ``{type, id, contract,
payload}``. ``hello`` n'a pas de ``contract`` : il propose ceux que loom sait
servir (``contracts``), et ``welcome`` dit celui que la console retient.

Le contrat 1 est celui de loom 2.0.24, la référence tant que la console n'a pas
d'utilisateurs (docs/analyse-securite-hello.md, décision du 22/11) : il porte la
commande ``sessions.list`` et le message ``security``, que loom pousse juste
après ``welcome``, à chaque connexion. loom 2.0.23 parlait un contrat 1 sans eux.

loom 2.0.25 y ajoute la sécurité partagée (``security.source: console``,
analyse_referentiel_utilisateurs.md de Heddlo) : le ``hello`` annonce
``security_source`` et ``gateway_user`` ; la console envoie alors
``security.sync``, la part de l'instance dans son référentiel ; loom
l'applique et répond ``security``, qui porte ``version`` et
``part_fingerprint``. Chaque ajout ne part que vers un pair qui le connaît :
les champs du ``hello`` sont ignorés par une console plus ancienne, et ceux de
``security`` ne sont remplis que pour une console qui a envoyé une part.

Les événements partent au format des événements du REST (``GET /v1/events``,
décision D-11) : voir ``translate``. Les schémas JSON publiés
(``schemas/v1/``) sont tirés de ces modèles par ``contract_schemas`` ; un essai
de la CI vérifie que les fichiers commités sont à jour, si bien qu'un
changement de charge interne ne passe pas sans qu'on décide de son sort
(traduire, ou ouvrir le contrat suivant).
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final, Literal, Self, cast

from pydantic import AwareDatetime, Field, JsonValue, NonNegativeInt, PositiveInt, ValidationError

from loom_ia.agents.spec import AgentAccess, ApproveAccess
from loom_ia.config.models import AccessRole, Permission
from loom_ia.core.events import DURABLE_PAYLOADS, Event
from loom_ia.core.events.redaction import MARK
from loom_ia.core.model import DomainModel, RateLimit, new_id

CONTRACT: Final = 1
"""Contrat que loom propose en premier."""

CONTRACTS: Final = (1,)
"""Contrats que loom sait servir, du plus récent au plus ancien."""

SCHEMAS_DIR: Final = Path(__file__).parent / "schemas" / f"v{CONTRACT}"
"""Dossier des schémas JSON publiés du contrat."""

type MessageType = Literal[
    "hello",
    "welcome",
    "events",
    "ack",
    "session.deleted",
    "command",
    "command.result",
    "file.data",
    "error",
    "bye",
    "security",
    "security.sync",
]
"""Types de message du contrat 1."""

type CommandName = Literal[
    "run.start",
    "question.answer",
    "approval.decide",
    "run.cancel",
    "events.query",
    "approval.get",
    "file.get",
    "approval.file",
    "approval.preview",
    "sessions.list",
]
"""Commandes du contrat 1."""

type SecuritySource = Literal["file", "console"]
"""D'où vient la sécurité d'une instance : ``loom.yaml`` (``file``) ou la console (``console``)."""

type Outcome = Literal["accepted", "refused", "error"]
"""Issue d'une commande : acceptée, refusée avec un motif, ou en erreur."""

type ErrorCode = Literal["unauthorized", "no_common_contract", "unknown_type", "invalid_message", "too_large", "internal"]
"""Codes d'erreur du canal."""


class ContractError(ValueError):
    """Un message qui ne respecte pas le contrat : JSON invalide, type inconnu, charge non conforme.

    Attributes:
        code: Le code d'erreur à renvoyer (``unknown_type`` ou ``invalid_message``).
    """

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: ErrorCode = code


# --- Charges ---------------------------------------------------------------------


class HelloPayload(DomainModel):
    """``hello`` (loom → console) : qui se connecte, et ce qu'il sait servir.

    Le seul message dont la **lecture** ignore les champs inconnus (``decode``,
    décision du 22/11) : il part avant la négociation du contrat, si bien qu'un
    loom plus récent ne pourrait rien y annoncer de neuf sans faire refuser sa
    connexion par une console qui ne le connaît pas. Un champ ajouté plus tard y
    est simplement ignoré par les consoles plus anciennes ; un champ connu reste
    contrôlé comme ailleurs. Le modèle, lui, reste strict : le code qui le
    construit ne peut pas y glisser une faute de frappe. Tous les autres
    messages refusent les champs inconnus.

    Attributes:
        instance_id: Identifiant stable de l'instance.
        worker_id: Process qui ouvre cette connexion (une connexion par process).
        loom_version: Version du paquet ``loom-ia``.
        contracts: Contrats que loom sait servir, du plus récent au plus ancien.
        tenant: Client que cette console sert.
        agents: Agents de ce client.
        commands: Vrai si loom accepte des commandes (``console.commands.user``).
        export_mode: ``push`` ou ``on_demand``.
        security_source: ``console`` si la sécurité vient de la console
            (``security.source: console``) : elle attend alors ``security.sync``.
        gateway_user: Utilisateur de la passerelle (``console.commands.user``),
            que la part de l'instance doit contenir ; absent sans passerelle.
    """

    instance_id: str
    worker_id: str
    loom_version: str
    contracts: tuple[PositiveInt, ...]
    tenant: str
    agents: tuple[str, ...] = ()
    commands: bool = False
    export_mode: Literal["push", "on_demand"] = "on_demand"
    security_source: SecuritySource = "file"
    gateway_user: str | None = None


class SecurityRole(DomainModel):
    """Un rôle d'accès, tel que ``security.roles`` le déclare.

    Attributes:
        id: Identifiant du rôle.
        description: Libellé.
        permissions: Ses permissions.
        agents: Agents auxquels il est borné ; vide signifie tous.
        act_as_roles: Rôles qu'une passerelle de ce rôle peut endosser.
    """

    id: str
    description: str = ""
    permissions: tuple[Permission, ...]
    agents: tuple[str, ...] = ()
    act_as_roles: tuple[str, ...] = ()

    @classmethod
    def of(cls, role: AccessRole) -> Self:
        """Le rôle de la config, tel qu'il part, champ pour champ.

        Un champ que le rôle de la config aurait et pas le message lève au lieu
        d'être perdu (``extra="forbid"``).

        Args:
            role: Le rôle déclaré dans ``security.roles``.

        Returns:
            Le rôle du message.
        """
        return cls.model_validate(role.model_dump())

    def access_role(self) -> AccessRole:
        """Le rôle de la config, champ pour champ, avec ses contrôles (``AccessRole``).

        Returns:
            Le rôle d'accès.

        Raises:
            pydantic.ValidationError: Un champ que ``AccessRole`` n'a pas, ou une
                règle d'``AccessRole`` enfreinte.
        """
        return AccessRole.model_validate(self.model_dump())


class SecurityUser(DomainModel):
    """Un utilisateur du client de la console.

    Attributes:
        id: Identifiant, écrit au journal.
        name: Nom affiché ; absent, l'identifiant.
        roles: Ses rôles d'accès.
    """

    id: str
    name: str | None = None
    roles: tuple[str, ...]


class SecurityKey(DomainModel):
    """Une clé d'API d'un utilisateur du client, **sans son empreinte**.

    Attributes:
        id: Identifiant de la clé.
        user: Utilisateur qu'elle identifie.
        scopes: Permissions qu'elle garde ; absent : toutes celles de l'utilisateur.
        agents: Agents qu'elle ouvre ; vide signifie tous.
        expires: Fin de validité ; absente, elle ne périme pas.
    """

    id: str
    user: str
    scopes: tuple[Permission, ...] | None = None
    agents: tuple[str, ...] = ()
    expires: AwareDatetime | None = None


class SecurityAgent(DomainModel):
    """Le bloc ``access`` d'un agent du client : qui peut agir sur lui.

    Une liste absente laisse décider les permissions des rôles ; une liste
    présente restreint.

    Attributes:
        name: L'agent.
        run: Rôles qui le lancent.
        answer: Rôles qui répondent à ses questions.
        cancel: Rôles qui arrêtent ses runs.
        approve_default: Approbateurs de ses demandes, à défaut d'une liste par outil.
        approve_tools: Approbateurs par outil.
        self_approval: Faux si l'agent interdit l'auto-approbation.
    """

    name: str
    run: tuple[str, ...] | None = None
    answer: tuple[str, ...] | None = None
    cancel: tuple[str, ...] | None = None
    approve_default: tuple[str, ...] | None = None
    approve_tools: dict[str, tuple[str, ...]] = Field(default_factory=dict[str, tuple[str, ...]])
    self_approval: bool = True

    @classmethod
    def of(cls, name: str, access: AgentAccess) -> Self:
        """Le bloc ``access`` d'un agent, tel qu'il part.

        Args:
            name: L'agent.
            access: Son bloc ``access``.

        Returns:
            Le bloc du message.
        """
        return cls(
            name=name,
            run=access.run,
            answer=access.answer,
            cancel=access.cancel,
            approve_default=access.approve.default,
            approve_tools=dict(access.approve.tools),
            self_approval=access.self_approval,
        )

    def access(self) -> AgentAccess:
        """Le bloc ``access`` de l'agent, tel que la config le déclare, avec ses contrôles.

        Returns:
            Le bloc ``access``.

        Raises:
            pydantic.ValidationError: Une liste invalide (vide, doublon, identifiant).
        """
        return AgentAccess(
            run=self.run,
            answer=self.answer,
            cancel=self.cancel,
            approve=ApproveAccess(default=self.approve_default, tools=self.approve_tools),
            self_approval=self.self_approval,
        )


class SecurityGateway(DomainModel):
    """La passerelle des commandes (``console.commands``).

    Attributes:
        user: Utilisateur loom de la passerelle.
        act_as_roles: Rôles qu'elle peut endosser, ceux de ses rôles qui ont ``act_as``.
        rate: Commandes acceptées par minute, au plus.
        max_ttl: Horizon maximal de l'échéance d'une commande, en secondes.
    """

    user: str
    act_as_roles: tuple[str, ...]
    rate: PositiveInt
    max_ttl: PositiveInt


class SecurityPayload(DomainModel):
    """``security`` (loom → console) : la sécurité de l'instance pour le client de la console, telle que loom l'applique.

    En sécurité locale, loom reste le référentiel : la console la lit et n'en
    renvoie rien. En sécurité partagée, loom l'envoie après chaque part
    appliquée : ``version`` et ``part_fingerprint`` disent laquelle. Aucun
    secret n'y figure, ni empreinte de clé, ni clé de la console.

    Attributes:
        tenant: Client que la console sert.
        roles: Tous les rôles déclarés, portés ou non (un agent peut citer un rôle que personne ne porte).
        users: Les utilisateurs du client.
        api_keys: Leurs clés d'API, sans empreinte.
        agents: Les agents du client et leur bloc ``access``.
        gateway: La passerelle des commandes ; absente, les commandes sont refusées.
        fingerprint: ``sha256:`` et l'empreinte du reste du document (JSON
            canonique), sans ``version`` ni ``part_fingerprint`` : deux process
            de configs différentes en ont deux.
        version: Version de la part appliquée ; absente en sécurité locale.
        part_fingerprint: Empreinte de la part appliquée
            (``SecuritySyncPayload.fingerprint``) ; absente en sécurité locale.
    """

    tenant: str
    roles: tuple[SecurityRole, ...]
    users: tuple[SecurityUser, ...]
    api_keys: tuple[SecurityKey, ...] = ()
    agents: tuple[SecurityAgent, ...] = ()
    gateway: SecurityGateway | None = None
    fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    version: NonNegativeInt | None = None
    part_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")


class SecuritySyncKey(SecurityKey):
    """Une clé d'API de la part d'une instance, **avec son empreinte** : loom en a besoin pour la reconnaître.

    Attributes:
        hash: Empreinte de la clé (``sha256:`` et 64 chiffres hexadécimaux), jamais la clé.
        rate_limit: Débit de la clé ; absent : aucun.
    """

    hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    rate_limit: RateLimit | None = None


class SecuritySyncPayload(DomainModel):
    """``security.sync`` (console → loom) : la part de l'instance dans le référentiel de la console.

    Envoyée après ``welcome`` à un process dont le ``hello`` dit
    ``security_source: console``, puis à chaque modification du référentiel.
    loom la contrôle comme une section ``security`` de ``loom.yaml``, l'applique
    à chaud, la garde dans son cache et répond ``security`` ; refusée, il
    répond ``error`` (``about`` : l'identifiant de ce message) et garde la
    précédente.

    Attributes:
        version: Version du référentiel qui a produit la part.
        tenant: Client de l'instance : celui que la console sert.
        roles: Tous les rôles d'accès du référentiel.
        users: Les utilisateurs rattachés à l'instance.
        api_keys: Leurs clés d'API, avec leur empreinte.
        fingerprint: ``sha256:`` et l'empreinte du reste de la part, sans
            ``version`` (``part_fingerprint``) : deux parts de même contenu ont
            la même.
    """

    version: NonNegativeInt
    tenant: str
    roles: tuple[SecurityRole, ...] = ()
    users: tuple[SecurityUser, ...] = ()
    api_keys: tuple[SecuritySyncKey, ...] = ()
    fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class WelcomePayload(DomainModel):
    """``welcome`` (console → loom) : la console accepte, avec ce contrat.

    Attributes:
        contract: Contrat retenu, parmi ceux du ``hello``.
    """

    contract: PositiveInt


class EventsPayload(DomainModel):
    """``events`` (loom → console) : un lot d'événements, dans l'ordre du journal de chaque session.

    Attributes:
        batch_id: Identifiant du lot, que la console acquitte.
        events: Les événements, au format du REST (``translate.public_event``).
    """

    batch_id: str
    events: tuple[dict[str, JsonValue], ...]


class AckPayload(DomainModel):
    """``ack`` (console → loom) : le lot est enregistré de façon durable, ou la session effacée.

    Attributes:
        batch_id: Le lot acquitté, ou l'identifiant du message ``session.deleted``
            dont l'effacement est fait.
    """

    batch_id: str


class SessionDeletedPayload(DomainModel):
    """``session.deleted`` (loom → console) : la session est effacée chez loom ; la console efface sa copie (Q-10).

    Attributes:
        tenant: Le client.
        session: La session effacée.
    """

    tenant: str
    session: str


class CommandPayload(DomainModel):
    """``command`` (console → loom) : une action, au nom d'un utilisateur loom.

    Attributes:
        command_id: Identifiant unique ; une commande vue deux fois rend le même résultat.
        expires_at: Échéance ; passée, la commande est refusée.
        as_user: Utilisateur loom au nom duquel la passerelle agit.
        name: La commande.
        args: Ses arguments.
    """

    command_id: str
    expires_at: AwareDatetime
    as_user: str
    name: CommandName
    args: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])


class CommandResultPayload(DomainModel):
    """``command.result`` (loom → console) : l'issue d'une commande ; son déroulé remonte par les événements.

    Attributes:
        command_id: La commande.
        outcome: ``accepted``, ``refused`` ou ``error``.
        reason: Motif d'un refus ou d'une erreur.
        result: Ce que la commande rend (le ``run_id`` d'un run lancé…).
    """

    command_id: str
    outcome: Outcome
    reason: str | None = None
    result: JsonValue = None


class FileDataPayload(DomainModel):
    """``file.data`` (loom → console) : un morceau d'un fichier demandé par une commande.

    Le premier morceau (``part`` 0) porte aussi ``name``, ``media_type``,
    ``size`` et ``sha256``.

    Attributes:
        command_id: La commande qui l'a demandé.
        part: Rang du morceau, à partir de 0.
        last: Vrai pour le dernier morceau.
        data: Les octets du morceau, en base64.
        name: Nom du fichier.
        media_type: Type MIME.
        size: Taille totale, en octets.
        sha256: Empreinte SHA-256 du fichier entier, en hexadécimal.
    """

    command_id: str
    part: NonNegativeInt
    last: bool
    data: str
    name: str | None = None
    media_type: str | None = None
    size: NonNegativeInt | None = None
    sha256: str | None = None


class ErrorPayload(DomainModel):
    """``error`` (dans les deux sens) : un message refusé, ou une connexion refusée.

    Attributes:
        code: Le code.
        message: Ce qu'il en est, en clair.
        about: Identifiant du message en cause, s'il y en a un.
    """

    code: ErrorCode
    message: str
    about: str | None = None


class ByePayload(DomainModel):
    """``bye`` (dans les deux sens) : fermeture voulue.

    Attributes:
        reason: Pourquoi (``shutdown``, ``reload``, ``revoked``…).
    """

    reason: str = "shutdown"


type Payload = (
    HelloPayload
    | WelcomePayload
    | EventsPayload
    | AckPayload
    | SessionDeletedPayload
    | CommandPayload
    | CommandResultPayload
    | FileDataPayload
    | ErrorPayload
    | ByePayload
    | SecurityPayload
    | SecuritySyncPayload
)
"""Une charge de message."""

PAYLOADS: Final[Mapping[MessageType, type[Payload]]] = {
    "hello": HelloPayload,
    "welcome": WelcomePayload,
    "events": EventsPayload,
    "ack": AckPayload,
    "session.deleted": SessionDeletedPayload,
    "command": CommandPayload,
    "command.result": CommandResultPayload,
    "file.data": FileDataPayload,
    "error": ErrorPayload,
    "bye": ByePayload,
    "security": SecurityPayload,
    "security.sync": SecuritySyncPayload,
}
"""Le modèle de charge de chaque type de message."""

_KINDS: Final[Mapping[type[Payload], MessageType]] = {model: kind for kind, model in PAYLOADS.items()}
"""Le type de message de chaque modèle de charge."""


class Envelope(DomainModel):
    """Un message du canal : son type, son identifiant, le contrat, sa charge.

    Attributes:
        type: Type du message.
        id: Identifiant unique (UUIDv7).
        contract: Contrat négocié ; absent du ``hello``, qui propose.
        payload: La charge, propre au type.
    """

    type: MessageType
    id: str = Field(default_factory=new_id)
    contract: PositiveInt | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue])


def encode(payload: Payload, *, contract: int | None = CONTRACT, message_id: str | None = None) -> str:
    """Le message JSON qui porte cette charge.

    Args:
        payload: La charge ; son modèle donne le type du message.
        contract: Contrat de la connexion ; ``None`` pour le ``hello``.
        message_id: Identifiant à reprendre ; à défaut, un nouveau.

    Returns:
        Le texte d'une trame.
    """
    kind = _KINDS[type(payload)]
    envelope = Envelope(
        type=kind,
        contract=None if kind == "hello" else contract,
        payload=cast("dict[str, JsonValue]", payload.model_dump(mode="json", exclude_none=True)),
        **({"id": message_id} if message_id is not None else {}),
    )
    return envelope.model_dump_json(exclude_none=True)


def decode(text: str | bytes) -> tuple[Envelope, Payload]:
    """L'enveloppe d'une trame et sa charge validée.

    Args:
        text: La trame.

    Returns:
        L'enveloppe, et la charge dans son modèle.

    Raises:
        ContractError: JSON invalide (``invalid_message``), type inconnu
            (``unknown_type``) ou charge non conforme (``invalid_message``).
    """
    try:
        raw: object = json.loads(text)
    except (ValueError, UnicodeDecodeError) as error:
        raise ContractError("invalid_message", f"trame illisible : {error}") from None
    if not isinstance(raw, dict):
        raise ContractError("invalid_message", "un message est un objet JSON")
    data = cast("dict[str, Any]", raw)
    kind = data.get("type")
    if kind not in PAYLOADS:
        raise ContractError("unknown_type", f"type de message inconnu : {kind!r}")
    try:
        envelope = Envelope.model_validate(data)
        fields = envelope.payload
        if envelope.type == "hello":
            # Le hello précède la négociation : un champ qu'un loom plus récent y ajoute est ignoré (HelloPayload).
            fields = {name: value for name, value in fields.items() if name in HelloPayload.model_fields}
        payload = PAYLOADS[envelope.type].model_validate(fields)
    except ValidationError as error:
        raise ContractError("invalid_message", f"message {kind!r} non conforme : {error.errors()[0]['msg']}") from None
    return envelope, payload


# --- Schémas JSON publiés ------------------------------------------------------------


def contract_schemas() -> dict[str, dict[str, Any]]:
    """Les schémas JSON du contrat, par nom de fichier : ce que ``schemas/v1/`` doit contenir.

    - ``envelope.json`` : l'enveloppe commune ;
    - ``message.<type>.json`` : la charge de chaque type de message ;
    - ``event.json`` : l'enveloppe d'un événement exporté ;
    - ``event.<type>.json`` : la charge de chaque type d'événement. Ses champs
      de contenu y sont facultatifs : en mode ``on_demand``, ils sont retirés,
      et ``redacted`` en dit les chemins.

    Returns:
        Nom de fichier → schéma.
    """
    schemas: dict[str, dict[str, Any]] = {"envelope.json": _titled(Envelope.model_json_schema(mode="serialization"), "envelope")}
    for kind, model in PAYLOADS.items():
        schema = model.model_json_schema(mode="serialization")
        if kind == "hello":
            schema.pop("additionalProperties", None)  # lu en ignorant les champs inconnus (HelloPayload)
        schemas[f"message.{kind}.json"] = _titled(schema, f"message.{kind}")
    types = sorted(_event_type(payload) for payload in DURABLE_PAYLOADS)
    envelope = Event.model_json_schema(mode="serialization")
    envelope["properties"]["payload"] = {
        "type": "object",
        "required": ["type"],
        "properties": {"type": {"enum": types}},
        "description": "La charge, décrite par event.<type>.json.",
    }
    schemas["event.json"] = _titled(_pruned(envelope), "event")
    for payload in DURABLE_PAYLOADS:
        kind = _event_type(payload)
        schema = payload.model_json_schema(mode="serialization")
        for path in payload.content_fields:
            _optional(schema, schema, path.split("."))
        schema.setdefault("properties", {})[MARK] = {
            "type": "array",
            "items": {"type": "string"},
            "description": "Chemins des champs de contenu retirés (mode on_demand).",
        }
        schemas[f"event.{kind}.json"] = _titled(schema, f"event.{kind}")
    return schemas


def write_schemas(directory: Path = SCHEMAS_DIR) -> list[Path]:
    """Écrit les schémas du contrat dans ``directory`` ; à relancer quand un essai les dit périmés.

    ``uv run python -c "from loom_ia.console.contract import write_schemas; write_schemas()"``

    Args:
        directory: Dossier des schémas.

    Returns:
        Les fichiers écrits.
    """
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, schema in contract_schemas().items():
        path = directory / name
        path.write_text(dumped(schema), encoding="utf-8")
        written.append(path)
    return written


def dumped(schema: Mapping[str, Any]) -> str:
    """Un schéma tel qu'il s'écrit dans son fichier : JSON trié, indenté, terminé par une fin de ligne."""
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _event_type(payload: type[DomainModel]) -> str:
    """Le type d'une charge d'événement, tiré de son champ ``type``."""
    return cast("str", payload.model_fields["type"].default)


def _titled(schema: dict[str, Any], name: str) -> dict[str, Any]:
    """Le schéma, avec son identifiant de contrat."""
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": f"loom-console/v{CONTRACT}/{name}.json", **schema}


def _pruned(schema: dict[str, Any]) -> dict[str, Any]:
    """Le schéma, sans les ``$defs`` que plus rien n'y référence (celles des charges, décrites à part)."""
    definitions = cast("dict[str, Any]", schema.get("$defs", {}))
    kept: set[str] = set()
    pending = [{key: value for key, value in schema.items() if key != "$defs"}]
    while pending:
        node = pending.pop()
        for ref in _refs(node):
            name = ref.removeprefix("#/$defs/")
            if name in definitions and name not in kept:
                kept.add(name)
                pending.append(definitions[name])
    pruned = {key: value for key, value in schema.items() if key != "$defs"}
    if kept:
        pruned["$defs"] = {name: definitions[name] for name in sorted(kept)}
    return pruned


def _refs(node: object) -> list[str]:
    """Les ``$ref`` locaux d'un fragment de schéma, à toute profondeur."""
    found: list[str] = []
    if isinstance(node, dict):
        table = cast("dict[str, object]", node)
        ref = table.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            found.append(ref)
        for value in table.values():
            found.extend(_refs(value))
    elif isinstance(node, list):
        for item in cast("list[object]", node):
            found.extend(_refs(item))
    return found


def _optional(root: dict[str, Any], node: dict[str, Any], path: list[str]) -> None:
    """Rend facultatif le champ désigné par ``path`` (grammaire des ``content_fields`` : ``a.b``, ``a[].b``).

    Le schéma est une copie propre à son fichier : modifier ses ``$defs`` ne
    touche aucun autre schéma.
    """
    head, *rest = path
    name = head.removesuffix("[]")
    for target in _objects(root, node):
        if not rest:
            required = target.get("required")
            if isinstance(required, list) and name in required:
                cast("list[str]", required).remove(name)
            continue
        child = target.get("properties", {}).get(name)
        if not isinstance(child, dict):
            continue
        child_schema = cast("dict[str, Any]", child)
        if head.endswith("[]"):
            for array in _objects(root, child_schema):
                items = array.get("items")
                if isinstance(items, dict):
                    _optional(root, cast("dict[str, Any]", items), rest)
        else:
            _optional(root, child_schema, rest)


def _objects(root: dict[str, Any], node: dict[str, Any]) -> list[dict[str, Any]]:
    """Les schémas concrets derrière ``node`` : ``$ref`` suivi, branches d'``anyOf`` / ``oneOf`` dépliées."""
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        definition = root.get("$defs", {}).get(ref.removeprefix("#/$defs/"))
        return _objects(root, cast("dict[str, Any]", definition)) if isinstance(definition, dict) else []
    branches = node.get("anyOf") or node.get("oneOf")
    if isinstance(branches, list):
        found: list[dict[str, Any]] = []
        for branch in cast("list[object]", branches):
            if isinstance(branch, dict):
                found.extend(_objects(root, cast("dict[str, Any]", branch)))
        return found
    return [node]

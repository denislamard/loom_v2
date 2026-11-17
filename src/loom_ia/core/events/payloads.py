# SPDX-License-Identifier: Apache-2.0
"""Charges typées des événements durables du journal (point 22).

Chaque classe déclare son ``type`` (``<catégorie>.<action au passé>``), sa
catégorie et ses facettes : les champs de recherche que l'enveloppe recopie
pour que les stores les indexent sans connaître les payloads.

Tous les événements du journal sont déclarés ici : runs et étapes, appels de
modèle et d'outil, politiques, guards, budgets, juges, approbations,
idempotence, artefacts et sessions. Les 34 types, par catégorie :

- ``run`` : ``run.started``, ``step.started``, ``step.completed``,
  ``run.transitioned``, ``run.completed``, ``run.failed``, ``run.cancelled``,
  ``run.claimed`` ;
- ``message`` : ``message.user`` ;
- ``model`` : ``model.responded``, ``model.retried``, ``model.exchanged``,
  ``model.fell_back`` ;
- ``circuit`` : ``circuit.opened`` ;
- ``tool`` : ``tool.called``, ``tool.completed``, ``tool.source_unavailable`` ;
- ``policy`` : ``policy.decided``, ``budget.exceeded`` ;
- ``guard`` : ``guard.checked``, ``judge.evaluated`` ;
- ``approval`` : ``approval.requested``, ``approval.granted``,
  ``approval.rejected``, ``approval.expired`` ;
- ``question`` : ``question.asked``, ``question.answered``,
  ``question.expired`` ;
- ``idempotency`` : ``idempotency.recorded``, ``idempotency.reused`` ;
- ``artifact`` : ``artifact.stored`` ;
- ``session`` : ``session.snapshot``, ``session.compacted``,
  ``session.trimmed``.

Un appel de modèle fait par un rôle délégué (fonction C2) est journalisé dans le run
de l'orchestrateur, entre le ``tool.called`` et le ``tool.completed`` de
l'appel : ``call_id`` le relie à cet appel, et l'enveloppe porte le nom du
rôle.

Un sous-agent (fonction C5, point 4) tourne dans un run enfant du même journal : son
``run.started`` porte ``parent_run_id``, ``parent_call_id`` et ``depth``, et
l'enveloppe le ``root_run_id`` de l'arbre. Côté parent, le ``tool.called`` de
l'appel donne l'identifiant de l'enfant (``child_run_id``), et le
``tool.completed`` sa consommation, ajoutée à celle du parent.

Guards (point 20) : chaque contrôle d'une sortie écrit un ``guard.checked``,
réussi ou non, avant la décision qu'il motive.

Budgets (fonction J4) : une limite atteinte écrit un ``budget.exceeded``, une fois
par limite, avant le ``policy.decided`` qui arrête le run (``on_exceed: stop``).

Secours (fonction B4, point 10) : un modèle en échec après ses nouvelles tentatives, ou
écarté par son disjoncteur, passe la main au suivant de sa chaîne : un
``model.fell_back``, dans le span de l'appel, avant les tentatives du
secours. Un disjoncteur qui s'ouvre (modèle ou serveur MCP) écrit un
``circuit.opened`` dans le run dont l'échec l'a ouvert.

Juges (point 21) : l'appel du modèle d'un juge est journalisé dans le run jugé
(``model.retried``, ``model.responded`` avec ``judge``, enveloppe au nom de
``judge:<nom>``) : son coût s'ajoute au run, pas ses itérations. Son verdict
est un ``judge.evaluated`` (notes par critère), suivi du ``guard.checked``.

Politiques (point 2) : toute décision autre que ``Continue`` écrit un
``policy.decided`` avant son effet, dans le span de l'étape ou de l'appel
concerné. Une demande de réparation (``Retry``) est suivie d'un
``message.user`` de ``kind: repair`` qui porte le diagnostic.

Les fichiers ne sont jamais dans le journal : ``artifact.stored`` annonce
qu'un fichier a été rangé dans le stockage d'artefacts, et les messages ne
portent que sa référence. Un bloc d'octets (``inline_data``) y est refusé.

Sessions (phase 4.1) : les événements de catégorie ``session`` parlent de la
session, pas du run qui les écrit. Ils portent tous ``up_to_seq``, la
position jusqu'à laquelle ils remplacent l'historique, et la projection d'un
run les ignore (ils peuvent arriver après sa clôture).
"""

from datetime import datetime
from typing import Annotated, ClassVar, Final, Literal, Self, get_args

from pydantic import (
    Field,
    JsonValue,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    model_validator,
)

from loom_ia.core.model.base import DomainModel
from loom_ia.core.model.budget import BudgetLimit, BudgetScope, OnExceed, RunBudget
from loom_ia.core.model.content import ToolOutput, has_inline_data
from loom_ia.core.model.context import CallerContext
from loom_ia.core.model.ids import EventId, RunId
from loom_ia.core.model.judge import CriterionScore, JudgesMode
from loom_ia.core.model.media import ArtifactOrigin, ArtifactRecord
from loom_ia.core.model.messages import Message
from loom_ia.core.model.policy import CheckOutcome, CheckResolution, DecisionKind, HookPoint
from loom_ia.core.model.question import PendingQuestion, Question, QuestionReply
from loom_ia.core.model.run_state import CancelReason, PendingApproval, RunKind, RunStatus
from loom_ia.core.model.streaming import ModelErrorKind, StopReason
from loom_ia.core.model.tooling import ExpiryAction, ToolKind
from loom_ia.core.model.usage import Usage

type EventCategory = Literal[
    "run",
    "message",
    "model",
    "tool",
    "guard",
    "policy",
    "approval",
    "question",
    "idempotency",
    "artifact",
    "session",
    "circuit",
]
"""Catégorie d'un événement : la famille de son ``type`` (``model`` pour ``model.responded``…)."""
type EventStatus = Literal["ok", "warning", "error"]
"""Statut d'un événement, pour l'affichage et la recherche : ``ok``, ``warning`` ou ``error``."""
type FacetValue = str | int | float | bool | None
"""Valeur d'une facette : une valeur simple, que les stores indexent."""


INLINE_REFUSED: Final = "{label} : octets de fichier interdits dans le journal (référence attendue)"
"""Message d'une charge refusée parce qu'elle porte des octets de fichier ; ``{label}`` est le type d'événement."""


def _no_inline_data(message: Message | None, label: str) -> None:
    """Refuse un message qui porte des octets de fichier : le journal n'en garde que des références.

    Raises:
        ValueError: « <label> : octets de fichier interdits dans le journal
            (référence attendue) ».
    """
    if message is not None and has_inline_data(message.blocks):
        raise ValueError(INLINE_REFUSED.format(label=label))


def _with_via(facets: dict[str, FacetValue], via: str | None) -> dict[str, FacetValue]:
    """La facette ``via`` d'une décision, seulement quand une passerelle l'a portée.

    Comme ``judges`` et ``trigger`` : une facette sans valeur n'aide aucune
    recherche, et les journaux sans passerelle s'écrivent comme avant.
    """
    if via is not None:
        facets["via"] = via
    return facets


class Payload(DomainModel):
    """Base des charges d'événement : ce que porte un événement du journal, selon son type.

    Chaque sous-classe déclare son ``type`` (``<catégorie>.<action au passé>``),
    sa catégorie, ses facettes et ses champs de contenu. L'enveloppe
    (``Event``) recopie ``type``, ``category``, ``event_status`` et
    ``facets()`` pour que les stores les indexent sans connaître la charge.

    Attributes:
        category: Catégorie de l'événement (``run``, ``message``, ``model``,
            ``tool``, ``guard``, ``policy``, ``approval``, ``question``,
            ``idempotency``, ``artifact``, ``session``, ``circuit``).
        facet_fields: Champs recopiés tels quels en facettes : les champs de
            recherche de l'enveloppe. Une sous-classe peut en ajouter d'autres
            dans ``facets()``.
        content_fields: Champs qui portent du **contenu** : ce qu'un
            utilisateur a écrit, ce qu'un modèle a répondu, ce qu'un outil a
            reçu et rendu. Ils sont retirés quand la portée ``read_content``
            manque (phase 5.2a, conception §14.2), et les exports et le
            masquage les connaissent. Un chemin peut descendre d'un cran :
            ``context.metadata``, ``criteria[].reason``. Chaque charge la
            déclare, même vide : un essai l'exige, pour qu'une charge nouvelle
            ne porte pas du contenu sans le dire.
        export_content: Faux pour une charge dont le contenu ne part jamais
            vers un collecteur, même en capture ``content`` : les corps bruts
            d'un échange (phase 6.1b) sont trop gros pour un attribut, et le
            journal est leur place. Vrai par défaut.
    """

    category: ClassVar[EventCategory]
    facet_fields: ClassVar[tuple[str, ...]] = ()
    content_fields: ClassVar[tuple[str, ...]] = ()
    export_content: ClassVar[bool] = True

    @property
    def event_status(self) -> EventStatus:
        """Statut de l'événement, recopié dans l'enveloppe : ``ok`` par défaut ; une charge le redéfinit."""
        return "ok"

    def facets(self) -> dict[str, FacetValue]:
        """Facettes de l'événement : les champs de ``facet_fields``, par leur nom.

        Une charge en ajoute d'autres en redéfinissant la méthode, souvent
        seulement quand elles disent quelque chose : un journal antérieur se
        relit ainsi à l'identique.

        Returns:
            Nom de facette → valeur simple (texte, nombre, booléen ou ``None``).
        """
        values: dict[str, FacetValue] = {}
        for name in self.facet_fields:
            value: FacetValue = getattr(self, name)
            values[name] = value
        return values


# --- Run ---------------------------------------------------------------------


class RunStarted(Payload):
    """Ouverture d'un run : premier événement de chaque run, racine ou sous-run.

    Catégorie ``run``. Facettes : ``kind``, plus ``judges`` quand il ne vaut
    pas ``auto`` et ``trigger`` quand un déclencheur a ouvert le run. Contenu :
    ``context.metadata`` et ``state``.

    Un sous-agent (fonction C5, point 4) tourne dans un run enfant du même
    journal : son ``run.started`` porte ``parent_run_id``, ``parent_call_id``
    et ``depth``.

    Attributes:
        type: ``run.started``.
        kind: ``normal``, ou ``compaction`` pour le run interne qui résume
            l'historique (point 23). Défaut : ``normal``.
        context: Contexte fourni par l'appelant : client, utilisateur,
            passerelle, métadonnées (fonction A8), transmis aux outils et aux
            traces.
        parent_run_id: Run parent d'un sous-run ; ``None`` pour un run racine.
        parent_call_id: Appel d'outil du parent qui a lancé ce sous-run.
        depth: Profondeur dans l'arbre des runs : 0 pour un run racine.
        triggered_by: Run qui a déclenché celui-ci (compaction, point 23).
        trigger: Nom du déclencheur déclaré (``triggers``) qui a ouvert ce run
            (fonction H6, phase 5.4c). Une facette, et non du contenu : c'est
            de la provenance, et une clé de supervision doit pouvoir demander
            ce qu'une porte a lancé.
        judges: Juges choisis par l'appelant (point 21) : ``auto`` (ceux de la
            config), ``force`` ou ``skip``. Un sous-run hérite du choix de son
            parent. Défaut : ``auto``.
        budget: Part de budget donnée par le run parent à un sous-run
            (``budget_share``, point 4).
        state: State de la session posé par l'application à l'ouverture du
            run : ``{clé: valeur}``, ``None`` efface la clé (décisions D1 et D10
            du state de session). Écrit avant la demande, il est copié avec
            elle au rejeu. Du contenu, jamais une facette : un lecteur plus
            ancien ignore un champ ajouté, pas une facette. Omis quand il est
            vide.
    """

    category: ClassVar[EventCategory] = "run"
    facet_fields: ClassVar[tuple[str, ...]] = ("kind",)

    content_fields: ClassVar[tuple[str, ...]] = ("context.metadata", "state")
    type: Literal["run.started"] = "run.started"
    kind: RunKind = "normal"
    context: CallerContext = CallerContext()
    parent_run_id: RunId | None = None
    parent_call_id: str | None = None
    depth: NonNegativeInt = 0
    triggered_by: RunId | None = None
    trigger: str | None = None
    judges: JudgesMode = "auto"
    budget: RunBudget | None = None
    state: dict[str, JsonValue] = Field(default_factory=dict[str, JsonValue], exclude_if=lambda value: not value)

    def facets(self) -> dict[str, FacetValue]:
        """Facettes : ``kind``, plus ``judges`` s'il ne vaut pas ``auto`` et ``trigger`` s'il est donné."""
        facets = super().facets()
        if self.judges != "auto":
            # Absente sinon : les journaux antérieurs restent lisibles.
            facets["judges"] = self.judges
        if self.trigger is not None:
            facets["trigger"] = self.trigger
        return facets


type Effect = Literal["model_call", "tool_batch", "finalize", "wait_child"]
"""Ce que fait une étape : appel de l'orchestrateur, lot d'outils, réponse forcée ou attente d'un sous-run."""


class StepStarted(Payload):
    """Début d'une étape de la boucle d'un run : un appel de modèle, un lot d'outils, une clôture ou une attente.

    Catégorie ``run``. Facette : ``effect``. Aucun contenu. Chaque étape ouvre
    un span ; ``step.completed`` le ferme.

    Attributes:
        type: ``step.started``.
        step_no: Numéro de l'étape dans le run, à partir de 1.
        state: Statut du run au début de l'étape (``ready_for_model``,
            ``awaiting_tools``, ``finalizing``, ``waiting_child``…).
        effect: Ce que fait l'étape : ``model_call`` (appel de
            l'orchestrateur), ``tool_batch`` (lot d'appels d'outils),
            ``finalize`` (réponse forcée, sans outils) ou ``wait_child``
            (attente d'un sous-run).
    """

    category: ClassVar[EventCategory] = "run"
    facet_fields: ClassVar[tuple[str, ...]] = ("effect",)

    content_fields: ClassVar[tuple[str, ...]] = ()
    type: Literal["step.started"] = "step.started"
    step_no: PositiveInt
    state: RunStatus
    effect: Effect


class StepCompleted(Payload):
    """Fin d'une étape de la boucle d'un run, avec sa durée et son issue.

    Catégorie ``run``. Facettes : ``outcome`` et ``duration_ms``. Aucun
    contenu. Statut ``ok`` si l'étape a réussi, ``error`` sinon (erreur ou
    annulation).

    Attributes:
        type: ``step.completed``.
        step_no: Numéro de l'étape, celui de son ``step.started``.
        duration_ms: Durée de l'étape, en millisecondes.
        events_emitted: Événements écrits pendant l'étape. Défaut : 0.
        outcome: ``ok``, ``error`` ou ``cancelled``. Défaut : ``ok``.
    """

    category: ClassVar[EventCategory] = "run"
    facet_fields: ClassVar[tuple[str, ...]] = ("outcome", "duration_ms")

    content_fields: ClassVar[tuple[str, ...]] = ()
    type: Literal["step.completed"] = "step.completed"
    step_no: PositiveInt
    duration_ms: NonNegativeFloat
    events_emitted: NonNegativeInt = 0
    outcome: Literal["ok", "error", "cancelled"] = "ok"

    @property
    def event_status(self) -> EventStatus:
        """``ok`` si l'étape a réussi, ``error`` si elle a échoué ou a été annulée."""
        return "ok" if self.outcome == "ok" else "error"


class RunTransitioned(Payload):
    """Changement de statut d'un run dans la machine à états de la boucle (point 3).

    Catégorie ``run``. Facettes : ``from_state`` et ``to_state``. Aucun
    contenu. Statuts possibles : ``ready_for_model``, ``awaiting_tools``,
    ``waiting_child``, ``paused``, ``finalizing``, ``completed``, ``failed``,
    ``cancelled``.

    Attributes:
        type: ``run.transitioned``.
        from_state: Statut quitté.
        to_state: Statut atteint.
        step_no: Étape pendant laquelle la transition a lieu ; 0 avant la
            première. Défaut : 0.
        cause_type: Type de l'événement qui a déclenché la transition
            (``model.responded``, ``tool.completed``…), point 3.
        cause_event_id: Identifiant de cet événement.
    """

    category: ClassVar[EventCategory] = "run"
    facet_fields: ClassVar[tuple[str, ...]] = ("from_state", "to_state")

    content_fields: ClassVar[tuple[str, ...]] = ()
    type: Literal["run.transitioned"] = "run.transitioned"
    from_state: RunStatus
    to_state: RunStatus
    step_no: NonNegativeInt = 0
    cause_type: str | None = None
    cause_event_id: EventId | None = None


class RunCompleted(Payload):
    """Run terminé avec une réponse : événement terminal d'un run réussi.

    Catégorie ``run``. Facettes : ``iterations`` et ``cost_usd``, plus
    ``unverified`` et ``fallback`` quand ils sont vrais. Contenu : ``output``
    et ``data``. Statut ``warning`` si la réponse est gardée sans être
    vérifiée ou remplacée par un repli, ``ok`` sinon. Le message ne peut pas
    porter d'octets de fichier, seulement des références.

    Attributes:
        type: ``run.completed``.
        output: Réponse finale. Absente quand elle est la sortie d'un outil
            terminal (point 13), sauf si une politique ``on_output`` l'a
            remplacée.
        output_event_id: Événement (le ``tool.completed`` de l'outil
            terminal) qui porte la sortie quand elle vient d'un outil
            terminal.
        iterations: Appels de l'orchestrateur faits par le run. Défaut : 0.
        usage: Consommation de tout le run, sous-runs compris.
        cost_usd: Coût de tout le run, en dollars.
        data: Réponse structurée : l'objet JSON validé par le schéma de
            sortie de l'agent (fonction A7) ; ``None`` sans schéma.
        unverified: Vrai si la réponse est gardée bien qu'un contrat ou un
            juge la refuse (``on_failure: unverified``).
        fallback: Vrai si la réponse a été remplacée par le message de repli
            d'un contrat ou d'un juge (``on_failure: fallback``) : le run a
            abouti, mais sans vraie réponse. Omis quand il est faux, pour
            qu'un journal antérieur se relise à l'identique.
    """

    category: ClassVar[EventCategory] = "run"
    facet_fields: ClassVar[tuple[str, ...]] = ("iterations", "cost_usd")

    content_fields: ClassVar[tuple[str, ...]] = (
        "output",
        "data",
    )
    type: Literal["run.completed"] = "run.completed"
    output: Message | None = None
    output_event_id: EventId | None = None
    iterations: NonNegativeInt = 0
    usage: Usage = Usage()
    cost_usd: NonNegativeFloat = 0.0
    data: JsonValue = None
    unverified: bool = False
    fallback: bool = Field(default=False, exclude_if=lambda value: not value)

    @model_validator(mode="after")
    def _check_output(self) -> Self:
        """Refuse une réponse qui porte des octets de fichier au lieu d'une référence."""
        _no_inline_data(self.output, "run.completed")
        return self

    @property
    def event_status(self) -> EventStatus:
        """``warning`` si la réponse est gardée sans vérification ou remplacée par un repli, ``ok`` sinon."""
        return "warning" if self.unverified or self.fallback else "ok"

    def facets(self) -> dict[str, FacetValue]:
        """Facettes : ``iterations`` et ``cost_usd``, plus ``unverified`` et ``fallback`` quand ils sont vrais."""
        facets = super().facets()
        if self.unverified:
            # Absente sinon : les journaux antérieurs restent lisibles.
            facets["unverified"] = True
        if self.fallback:
            facets["fallback"] = True
        return facets


class RunClaimed(Payload):
    """Concession prise sur un run : un seul pilote à la fois (point 27).

    La durabilité vient du journal, pas de la file : une livraison en double
    est sans risque puisque l'état se reconstruit. Mais deux pilotes en même
    temps écriraient deux fois les mêmes effets. La concession les départage :
    qui la tient pilote, et elle expire si son porteur meurt. Sa durée se règle
    par ``execution.lease`` (60 s par défaut).

    Catégorie ``run``. Facette : ``worker_id``. Aucun contenu.

    Attributes:
        type: ``run.claimed``.
        worker_id: Instance qui pilote ; engendré à sa construction, pas
            configuré.
        lease_until: Échéance de la concession. Passée, un autre pilote peut
            reprendre le run.
    """

    category: ClassVar[EventCategory] = "run"
    facet_fields: ClassVar[tuple[str, ...]] = ("worker_id",)

    content_fields: ClassVar[tuple[str, ...]] = ()
    type: Literal["run.claimed"] = "run.claimed"
    worker_id: str
    lease_until: datetime


class RunCancelled(Payload):
    """Run arrêté avant sa fin, à la demande (fonction A5).

    Il est terminal : un run annulé ne se reprend pas. L'état reconstruit
    reste juste — les étapes déjà écrites sont dans le journal —, mais plus
    aucun événement n'est accepté après lui.

    Catégorie ``run``. Facette : ``reason``, plus ``via`` quand une passerelle
    a porté la demande. Aucun contenu. Statut ``warning``.

    Attributes:
        type: ``run.cancelled``.
        reason: ``requested`` (demande d'un appelant) ou ``parent`` (le run
            parent a été arrêté). Défaut : ``requested``.
        by: Auteur de la demande, quand on le connaît : ``user_id`` de
            l'appelant, identifiant du worker, run parent.
        via: Passerelle qui a porté la demande (``as_user``, décision D-5 des
            utilisateurs et rôles) ; absente sans passerelle.
        iterations: Appels de l'orchestrateur faits avant l'arrêt.
        usage: Consommation du run jusqu'à l'arrêt.
        cost_usd: Coût du run jusqu'à l'arrêt, en dollars.
        withdrawn: Demandes d'approbation encore ouvertes que l'annulation
            clôt, par ``call_id``. Sans elles, un audit qui lit le journal brut
            voyait une demande jamais tranchée (essai de la fonction N2 du
            23/10). Absent des journaux antérieurs.
    """

    category: ClassVar[EventCategory] = "run"
    facet_fields: ClassVar[tuple[str, ...]] = ("reason",)

    content_fields: ClassVar[tuple[str, ...]] = ()
    type: Literal["run.cancelled"] = "run.cancelled"
    reason: CancelReason = "requested"
    by: str | None = None
    via: str | None = Field(default=None, exclude_if=lambda value: value is None)
    iterations: NonNegativeInt = 0
    usage: Usage = Usage()
    cost_usd: NonNegativeFloat = 0.0
    withdrawn: tuple[str, ...] = ()

    @property
    def event_status(self) -> EventStatus:
        """Toujours ``warning`` : le run n'a pas abouti, sans être en échec."""
        return "warning"


class RunFailed(Payload):
    """Run terminé en échec : événement terminal d'un run qui n'a pas abouti.

    Catégorie ``run``. Facette : ``error_type``. Contenu : ``error``. Statut
    ``error``.

    Attributes:
        type: ``run.failed``.
        error_type: Nature de l'échec, pour filtrer : ``timeout`` (délai du
            run dépassé), ``Interrupted``, le type de l'événement en cause, le
            genre ou le nom d'une erreur.
        error: Message de l'erreur.
        iterations: Appels de l'orchestrateur faits avant l'échec.
        usage: Consommation du run jusqu'à l'échec.
        cost_usd: Coût du run jusqu'à l'échec, en dollars.
    """

    category: ClassVar[EventCategory] = "run"
    facet_fields: ClassVar[tuple[str, ...]] = ("error_type",)

    content_fields: ClassVar[tuple[str, ...]] = ("error",)
    type: Literal["run.failed"] = "run.failed"
    error_type: str
    error: str
    iterations: NonNegativeInt = 0
    usage: Usage = Usage()
    cost_usd: NonNegativeFloat = 0.0

    @property
    def event_status(self) -> EventStatus:
        """Toujours ``error``."""
        return "error"


# --- Messages et modèles -----------------------------------------------------


class UserMessage(Payload):
    """Message adressé au modèle orchestrateur : la demande, ou un diagnostic de réparation.

    ``request`` : la demande de l'utilisateur. ``repair`` : le diagnostic d'une
    réponse refusée par une politique (point 20) ; avec la réponse refusée, il
    est exclu de l'historique de session.

    Catégorie ``message``. Facette : ``kind``, seulement pour ``repair``.
    Contenu : ``message``. Le message ne peut pas porter d'octets de fichier :
    une pièce jointe est rangée avant lui (``artifact.stored``) et le message
    n'en garde que la référence.

    Attributes:
        type: ``message.user``.
        message: Le message, avec ses blocs.
        kind: ``request`` ou ``repair``. Défaut : ``request``.
        policy: Politique qui a demandé la réparation ; ``None`` pour une
            demande.
        tools: Vrai si l'orchestrateur garde ses outils pour répondre ou
            réparer. Défaut : vrai.
    """

    category: ClassVar[EventCategory] = "message"

    content_fields: ClassVar[tuple[str, ...]] = ("message",)
    type: Literal["message.user"] = "message.user"
    message: Message
    kind: Literal["request", "repair"] = "request"
    policy: str | None = None
    tools: bool = True

    @model_validator(mode="after")
    def _check_message(self) -> Self:
        """Refuse un message qui porte des octets de fichier au lieu d'une référence."""
        _no_inline_data(self.message, "message.user")
        return self

    def facets(self) -> dict[str, FacetValue]:
        """Facette ``kind`` seulement pour une réparation : une demande n'en a pas."""
        # Absente pour une demande : les journaux antérieurs restent lisibles.
        return {"kind": self.kind} if self.kind != "request" else {}


class ModelResponded(Payload):
    """Réponse d'un modèle à un appel : celle de l'orchestrateur, d'un rôle délégué ou d'un juge.

    Catégorie ``model``. Facettes : ``model_id``, ``provider``,
    ``stop_reason``, ``cost_usd``, ``latency_ms`` et ``tokens`` (total), plus
    ``model`` et ``judge`` quand ils sont connus. Contenu : ``message``.

    La réponse d'un rôle délégué est journalisée dans le run de
    l'orchestrateur, entre le ``tool.called`` et le ``tool.completed`` de
    l'appel (``call_id``). Celle d'un juge n'entre pas dans la conversation.

    Attributes:
        type: ``model.responded``.
        model_id: Nom du modèle chez le fournisseur (``claude-…``,
            ``gpt-…``).
        model: Identifiant du modèle dans la config (``models[].id``) :
            ``model_id`` est un nom de fournisseur, que deux déclarations
            peuvent partager. Absent des journaux antérieurs.
        provider: Fournisseur qui a servi l'appel.
        message: Message du modèle : texte, appels d'outils, raisonnement.
            Jamais d'octets de fichier.
        usage: Tokens consommés : entrée, sortie, cache, raisonnement
            (fonction J1).
        cost_usd: Coût de l'appel, en dollars, d'après la grille tarifaire du
            modèle (fonction J2).
        stop_reason: Pourquoi le modèle s'est arrêté : ``end``, ``tool_use``,
            ``max_tokens`` ou ``refusal``. Défaut : ``end``.
        latency_ms: Durée de l'appel, en millisecondes.
        attempts: Tentatives faites pour obtenir cette réponse, la dernière
            comprise. Défaut : 1.
        request_hash: Empreinte de la requête envoyée : le rejeu détecte ainsi
            une divergence (point 31).
        request_parts: Empreinte de chaque partie de la requête (modèle,
            système, outils, messages, réglages) : le rejeu dit ainsi ce qui a
            changé (phase 6.2a). Absente des journaux antérieurs, qui se
            rejouent quand même, avec un diagnostic moins fin.
        call_id: Appel d'outil servi par cette réponse (rôle délégué) ;
            ``None`` pour l'orchestrateur.
        judge: Juge qui a fait cet appel (point 21).
    """

    category: ClassVar[EventCategory] = "model"
    facet_fields: ClassVar[tuple[str, ...]] = (
        "model_id",
        "provider",
        "stop_reason",
        "cost_usd",
        "latency_ms",
    )

    content_fields: ClassVar[tuple[str, ...]] = ("message",)
    type: Literal["model.responded"] = "model.responded"
    model_id: str
    model: str | None = None
    provider: str
    message: Message
    usage: Usage = Usage()
    cost_usd: NonNegativeFloat = 0.0
    stop_reason: StopReason = "end"
    latency_ms: NonNegativeFloat = 0.0
    attempts: PositiveInt = 1
    request_hash: str
    request_parts: dict[str, str] = Field(default_factory=dict[str, str])
    call_id: str | None = None
    judge: str | None = None

    @model_validator(mode="after")
    def _check_message(self) -> Self:
        """Refuse une réponse qui porte des octets de fichier au lieu d'une référence."""
        _no_inline_data(self.message, "model.responded")
        return self

    def facets(self) -> dict[str, FacetValue]:
        """Facettes : celles de ``facet_fields`` et ``tokens`` (total), plus ``model`` et ``judge`` quand ils sont connus."""
        facets: dict[str, FacetValue] = {**super().facets(), "tokens": self.usage.total_tokens}
        if self.model is not None:
            facets["model"] = self.model
        if self.judge is not None:
            facets["judge"] = self.judge
        return facets


class ModelRetried(Payload):
    """Tentative d'appel de modèle échouée, suivie d'une nouvelle tentative (point 10).

    ``usage`` et ``cost_usd`` : ce que le fournisseur a annoncé avant l'échec
    (l'entrée d'un flux coupé, une sortie tronquée), qu'il facture quand même.
    Ils entrent dans le coût du run, le ledger, les budgets et les rapports, sans
    compter comme un appel (``max_calls``). La dernière tentative ratée, qui
    n'est suivie de rien, n'est annoncée que si elle a été facturée : ``final``,
    sans attente. Absents des journaux antérieurs, et pour un fournisseur qui ne
    dit sa consommation qu'en fin de flux (API compatibles OpenAI).

    Catégorie ``model``. Facettes : ``model_id``, ``provider``,
    ``error_kind`` et ``attempt``, plus ``model`` et ``judge`` quand ils sont
    connus. Contenu : ``error``. Statut ``warning``.

    Attributes:
        type: ``model.retried``.
        model_id: Nom du modèle chez le fournisseur (``claude-…``,
            ``gpt-…``).
        model: Identifiant du modèle dans la config (``models[].id``) :
            ``model_id`` est un nom de fournisseur, que deux déclarations
            peuvent partager. Absent des journaux antérieurs.
        provider: Fournisseur qui a servi l'appel.
        attempt: Numéro de la tentative qui a échoué, à partir de 1.
        error_kind: Genre de l'erreur : ``transient``, ``overloaded``,
            ``quota_exhausted``, ``context_overflow``, ``auth``,
            ``invalid_request``, ``content_filtered``, ``truncated`` ou
            ``unavailable``.
        error: Message de l'erreur.
        http_status: Statut HTTP de la réponse, s'il y en a eu une.
        delay_s: Attente avant la tentative suivante, en secondes.
        call_id: Appel d'outil servi par cet appel de modèle (rôle délégué).
        judge: Juge qui a fait cet appel (point 21).
        usage: Consommation facturée de la tentative ratée, quand le
            fournisseur l'a dite (fonction J1).
        cost_usd: Coût de cette consommation, en dollars.
        final: Vrai pour la dernière tentative : rien ne la suit, l'erreur
            remonte.
    """

    category: ClassVar[EventCategory] = "model"
    facet_fields: ClassVar[tuple[str, ...]] = ("model_id", "provider", "error_kind", "attempt")

    content_fields: ClassVar[tuple[str, ...]] = ("error",)
    type: Literal["model.retried"] = "model.retried"
    model_id: str
    model: str | None = None
    provider: str
    attempt: PositiveInt
    error_kind: ModelErrorKind
    error: str
    http_status: int | None = None
    delay_s: NonNegativeFloat
    call_id: str | None = None
    judge: str | None = None
    usage: Usage | None = None
    cost_usd: NonNegativeFloat | None = None
    final: bool = False

    @property
    def event_status(self) -> EventStatus:
        """Toujours ``warning`` : l'appel continue par une nouvelle tentative, ou échoue après."""
        return "warning"

    def facets(self) -> dict[str, FacetValue]:
        """Facettes : celles de ``facet_fields``, plus ``model`` et ``judge`` quand ils sont connus."""
        facets = super().facets()
        if self.model is not None:
            facets["model"] = self.model
        if self.judge is not None:
            facets["judge"] = self.judge
        return facets


class ModelExchanged(Payload):
    """Une requête HTTP au fournisseur et sa réponse, brutes (point 31, phase 6.1b).

    Écrit en opt-in (``telemetry.capture.raw_exchanges``), une fois par
    tentative d'appel — réussie ou non —, avant le ``model.retried`` ou le
    ``model.responded`` qu'elle précède, dans le même span. Les corps sont du
    texte tel qu'il a circulé, à trois retouches près, toutes dites :

    - les en-têtes d'authentification et les paramètres d'adresse qui portent
      un secret sont remplacés par ``[retiré]`` avant toute écriture ;
    - les octets d'un fichier (image en base64) sont remplacés par leur taille
      et leur empreinte : le journal ne porte pas d'octets de fichier ;
    - un corps plus long que ``capture.raw_max_bytes`` est coupé, et
      ``*_truncated`` le dit ; ``*_bytes`` et ``*_sha256`` décrivent alors le
      corps entier (après les deux premières retouches).

    Ni les corps ni les en-têtes ne partent vers un collecteur, même en
    capture ``content`` (``export_content``) : seulement le statut, la durée
    et les tailles.

    Catégorie ``model``. Facettes : ``model_id``, ``provider``, ``attempt``,
    ``status_code``, ``duration_ms``, ``request_bytes`` et
    ``response_bytes``, plus ``model``, ``synthetic`` et ``judge`` quand ils
    disent quelque chose. Contenu : en-têtes, corps et ``error``. Statut
    ``warning`` sans réponse ou avec un statut HTTP de 400 ou plus, ``ok``
    sinon.

    Attributes:
        type: ``model.exchanged``.
        model_id: Nom du modèle chez le fournisseur (``claude-…``,
            ``gpt-…``).
        model: Identifiant du modèle dans la config (``models[].id``) :
            ``model_id`` est un nom de fournisseur, que deux déclarations
            peuvent partager. Absent des journaux antérieurs.
        provider: Fournisseur qui a servi l'appel.
        attempt: Tentative de l'appel à laquelle appartient cet échange.
        method: Méthode HTTP (``POST``).
        url: Adresse appelée, paramètres secrets retirés.
        status_code: Statut HTTP ; absent si aucune réponse n'est arrivée
            (réseau, délai), et ``error`` dit pourquoi.
        duration_ms: Durée de l'échange, en millisecondes.
        request_headers: En-têtes de la requête, authentification retirée.
        request_body: Corps de la requête, retouché comme dit plus haut.
        request_bytes: Taille du corps entier de la requête, en octets.
        request_sha256: Empreinte SHA-256 du corps entier de la requête.
        request_truncated: Vrai si ``request_body`` est coupé.
        response_headers: En-têtes de la réponse.
        response_body: Corps de la réponse, retouché comme dit plus haut.
        response_bytes: Taille du corps entier de la réponse, en octets.
        response_sha256: Empreinte SHA-256 du corps entier de la réponse.
        response_truncated: Vrai si ``response_body`` est coupé.
        error: Erreur de transport, quand il n'y a pas eu de réponse.
        synthetic: Vrai pour l'échange d'un modèle simulé (``sdk: fake``),
            qui n'a pas fait d'HTTP.
        call_id: Appel d'outil servi par cet appel de modèle (rôle délégué).
        judge: Juge qui a fait cet appel (point 21).
    """

    category: ClassVar[EventCategory] = "model"
    facet_fields: ClassVar[tuple[str, ...]] = (
        "model_id",
        "provider",
        "attempt",
        "status_code",
        "duration_ms",
        "request_bytes",
        "response_bytes",
    )

    content_fields: ClassVar[tuple[str, ...]] = (
        "request_headers",
        "request_body",
        "response_headers",
        "response_body",
        "error",
    )
    export_content: ClassVar[bool] = False
    type: Literal["model.exchanged"] = "model.exchanged"
    model_id: str
    model: str | None = None
    provider: str
    attempt: PositiveInt
    method: str
    url: str
    status_code: int | None = None
    duration_ms: NonNegativeFloat = 0.0
    request_headers: dict[str, str] = Field(default_factory=dict[str, str])
    request_body: str = ""
    request_bytes: NonNegativeInt = 0
    request_sha256: str = ""
    request_truncated: bool = False
    response_headers: dict[str, str] = Field(default_factory=dict[str, str])
    response_body: str = ""
    response_bytes: NonNegativeInt = 0
    response_sha256: str = ""
    response_truncated: bool = False
    error: str | None = None
    synthetic: bool = False
    call_id: str | None = None
    judge: str | None = None

    @property
    def event_status(self) -> EventStatus:
        """``warning`` sans réponse ou avec un statut HTTP de 400 ou plus, ``ok`` sinon."""
        if self.error is not None or (self.status_code is not None and self.status_code >= 400):
            return "warning"
        return "ok"

    def facets(self) -> dict[str, FacetValue]:
        """Facettes : celles de ``facet_fields``, plus ``model``, ``synthetic`` et ``judge`` quand ils disent quelque chose."""
        facets = super().facets()
        if self.model is not None:
            facets["model"] = self.model
        if self.synthetic:
            facets["synthetic"] = True
        if self.judge is not None:
            facets["judge"] = self.judge
        return facets


type FallbackReason = ModelErrorKind | Literal["circuit_open"]
"""Motif d'une bascule : genre de l'erreur du modèle après ses tentatives, ou ``circuit_open``."""
type CircuitTarget = Literal["model", "mcp"]
"""Cible d'un disjoncteur : un modèle, ou un serveur MCP."""


class ModelFellBack(Payload):
    """Bascule vers le modèle suivant de la chaîne de secours (fonction B4, point 10).

    Le modèle qui a échoué après ses nouvelles tentatives, ou qu'écarte son
    disjoncteur (``circuit_open``), passe la main au suivant. Adhérence : le
    reste du run garde le secours pour cet emplacement. Écrit dans le span de
    l'appel, avant les tentatives du secours.

    Catégorie ``model``. Facettes : ``slot``, ``from_model``, ``to_model`` et
    ``reason``, plus ``judge`` quand il est connu. Contenu : ``error``. Statut
    ``warning``.

    Attributes:
        type: ``model.fell_back``.
        slot: Emplacement qui bascule : ``main``, le nom d'un rôle ou
            ``judge:<nom>``.
        from_model: Identifiant, dans la config, du modèle quitté.
        to_model: Identifiant, dans la config, du secours appelé.
        reason: Genre de l'erreur du modèle quitté, ou ``circuit_open`` si son
            disjoncteur l'a écarté.
        error: Message de l'erreur, ou la durée restante du disjoncteur.
        call_id: Appel d'outil servi (rôle délégué).
        judge: Juge qui a fait cet appel (point 21).
    """

    category: ClassVar[EventCategory] = "model"
    facet_fields: ClassVar[tuple[str, ...]] = ("slot", "from_model", "to_model", "reason")

    content_fields: ClassVar[tuple[str, ...]] = ("error",)
    type: Literal["model.fell_back"] = "model.fell_back"
    slot: str
    from_model: str
    to_model: str
    reason: FallbackReason
    error: str = ""
    call_id: str | None = None
    judge: str | None = None

    @property
    def event_status(self) -> EventStatus:
        """Toujours ``warning``."""
        return "warning"

    def facets(self) -> dict[str, FacetValue]:
        """Facettes : celles de ``facet_fields``, plus ``judge`` quand il est connu."""
        facets = super().facets()
        if self.judge is not None:
            facets["judge"] = self.judge
        return facets


class CircuitOpened(Payload):
    """Disjoncteur ouvert : la cible est écartée pendant ``cooldown_s`` secondes (points 10 et 19).

    Pour tous les runs de l'instance : un modèle passe directement à son
    secours (``model.fell_back``, motif ``circuit_open``), un serveur MCP est
    déclaré indisponible (``tool.source_unavailable``) sans nouvel essai.
    Écrit dans le run dont l'échec l'a ouvert.

    Catégorie ``circuit``. Facettes : ``target_kind``, ``target`` et
    ``failures``, plus ``judge`` quand il est connu. Contenu : ``error``.
    Statut ``warning``.

    Attributes:
        type: ``circuit.opened``.
        target_kind: ``model`` ou ``mcp``.
        target: Identifiant du modèle dans la config, ou nom du serveur MCP.
        failures: Échecs de suite qui l'ont ouvert ; 1 quand l'essai d'après
            la pause a raté.
        cooldown_s: Durée pendant laquelle la cible est écartée, en secondes.
        error: Dernière erreur.
        call_id: Appel d'outil servi (rôle délégué).
        judge: Juge qui a fait l'appel.
    """

    category: ClassVar[EventCategory] = "circuit"
    facet_fields: ClassVar[tuple[str, ...]] = ("target_kind", "target", "failures")

    content_fields: ClassVar[tuple[str, ...]] = ("error",)
    type: Literal["circuit.opened"] = "circuit.opened"
    target_kind: CircuitTarget
    target: str
    failures: PositiveInt
    cooldown_s: PositiveFloat
    error: str = ""
    call_id: str | None = None
    judge: str | None = None

    @property
    def event_status(self) -> EventStatus:
        """Toujours ``warning``."""
        return "warning"

    def facets(self) -> dict[str, FacetValue]:
        """Facettes : celles de ``facet_fields``, plus ``judge`` quand il est connu."""
        facets = super().facets()
        if self.judge is not None:
            facets["judge"] = self.judge
        return facets


# --- Outils ------------------------------------------------------------------


class ToolCalled(Payload):
    """Appel d'outil lancé par le moteur, avant son exécution.

    Catégorie ``tool``. Facettes : ``tool_name`` et ``tool_kind``. Contenu :
    ``arguments``. Ouvre le span de l'appel ; le ``tool.completed`` du même
    ``call_id`` le ferme. Pour un sous-agent, ``child_run_id`` désigne le run
    enfant (fonction C5).

    Attributes:
        type: ``tool.called``.
        call_id: Identifiant de l'appel, donné par le modèle.
        tool_name: Nom de l'outil, tel que le modèle le voit.
        tool_kind: ``python``, ``mcp``, ``role`` (rôle délégué), ``agent``
            (sous-agent) ou ``builtin`` (outil fourni ``loom.*``).
        arguments: Arguments tels que le modèle les a écrits, références
            ``$ref`` comprises.
        refs: Références résolues dans les arguments avant l'exécution
            (point 12).
        resumed: Vrai pour la nouvelle exécution d'un appel interrompu
            (point 18).
        child_run_id: Run enfant d'un sous-agent : repris, et non relancé,
            après une interruption (point 4).
    """

    category: ClassVar[EventCategory] = "tool"
    facet_fields: ClassVar[tuple[str, ...]] = ("tool_name", "tool_kind")

    content_fields: ClassVar[tuple[str, ...]] = ("arguments",)
    type: Literal["tool.called"] = "tool.called"
    call_id: str
    tool_name: str
    tool_kind: ToolKind
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    refs: tuple[str, ...] = ()
    resumed: bool = False
    child_run_id: RunId | None = None


class ToolCompleted(Payload):
    """Fin d'un appel d'outil : son résultat, rendu au modèle.

    Catégorie ``tool``. Facettes : ``tool_name``, ``latency_ms``, ``size`` et
    ``is_error``, plus ``offloaded`` quand le résultat a été déporté.
    Contenu : ``output``. Statut ``error`` si le résultat est une erreur,
    ``ok`` sinon. Le résultat ne porte jamais d'octets de fichier : un fichier
    produit est rangé avant (``artifact.stored``) et référencé.

    Un ``tool.completed`` peut porter, dans sa sortie, le state qu'un outil a
    posé et les écritures refusées (décisions D1 et D8 du state de session).

    Attributes:
        type: ``tool.completed``.
        call_id: Identifiant de l'appel, celui de son ``tool.called``.
        tool_name: Nom de l'outil.
        output: Résultat de l'appel : blocs de contenu, données structurées,
            ``is_error``, référence de déport.
        latency_ms: Durée de l'appel, en millisecondes.
        size: Taille du résultat sérialisé en JSON, en octets.
        usage: Consommation d'un sous-agent (tout son run), ajoutée à celle
            du parent (point 4) ; ``None`` pour un autre outil.
        cost_usd: Coût de cette consommation, en dollars.
    """

    category: ClassVar[EventCategory] = "tool"
    facet_fields: ClassVar[tuple[str, ...]] = ("tool_name", "latency_ms", "size")

    content_fields: ClassVar[tuple[str, ...]] = ("output",)
    type: Literal["tool.completed"] = "tool.completed"
    call_id: str
    tool_name: str
    output: ToolOutput
    latency_ms: NonNegativeFloat = 0.0
    size: NonNegativeInt = 0
    usage: Usage | None = None
    cost_usd: NonNegativeFloat = 0.0

    @model_validator(mode="after")
    def _check_output(self) -> Self:
        """Refuse un résultat qui porte des octets de fichier au lieu d'une référence."""
        if has_inline_data(self.output.blocks):
            raise ValueError(INLINE_REFUSED.format(label="tool.completed"))
        return self

    @property
    def event_status(self) -> EventStatus:
        """``error`` si le résultat est une erreur (``output.is_error``), ``ok`` sinon."""
        return "error" if self.output.is_error else "ok"

    def facets(self) -> dict[str, FacetValue]:
        """Facettes : celles de ``facet_fields`` et ``is_error``, plus ``offloaded`` si le résultat a été déporté."""
        facets: dict[str, FacetValue] = {**super().facets(), "is_error": self.output.is_error}
        if self.output.offloaded is not None:
            # Absente quand elle est fausse : les journaux antérieurs restent lisibles.
            facets["offloaded"] = True
        return facets


class ToolSourceUnavailable(Payload):
    """Source d'outils injoignable au début du run : ses outils sont retirés (point 19).

    Une source est un serveur MCP ou une source déclarée sous
    ``tool_sources``. Catégorie ``tool``. Facettes : ``source`` et
    ``required``. Contenu : ``error``. Statut ``error`` si la source est
    requise (le run échoue), ``warning`` sinon (le run continue sans ses
    outils).

    Attributes:
        type: ``tool.source_unavailable``.
        source: Nom de la source.
        error: Pourquoi elle est injoignable.
        required: Vrai si le run ne peut pas s'en passer : il échoue.
    """

    category: ClassVar[EventCategory] = "tool"
    facet_fields: ClassVar[tuple[str, ...]] = ("source", "required")

    content_fields: ClassVar[tuple[str, ...]] = ("error",)
    type: Literal["tool.source_unavailable"] = "tool.source_unavailable"
    source: str
    error: str
    required: bool = False

    @property
    def event_status(self) -> EventStatus:
        """``error`` si la source est requise (le run échoue), ``warning`` sinon."""
        return "error" if self.required else "warning"


# --- Politiques --------------------------------------------------------------


class PolicyDecided(Payload):
    """Décision d'une politique autre que ``Continue`` (point 2), ou d'une règle du moteur.

    Écrite avant son effet, dans le span de l'étape ou de l'appel concerné.
    Selon la décision : ``reason`` porte le motif (``Deny``, ``Stop``,
    ``Replace``), le diagnostic (``Retry``) ou l'erreur (``Fail``). Un
    ``Replace`` d'arguments garde les nouveaux arguments, repris tels quels si
    l'appel est relancé ; un ``Replace`` de la réponse finale garde la
    réponse. Un ``Replace`` de requête ou de résultat se lit dans l'événement
    suivant (``model.responded``, ``tool.completed``). Une demande de
    réparation (``Retry``) est suivie d'un ``message.user`` de ``kind:
    repair`` qui porte le diagnostic.

    ``continue`` n'est écrit que par exception : politique en erreur laissée
    passer (``on_error: allow``, ``error``), ou règle du moteur qui écarte un
    comportement déclaré (outil terminal appelé avec d'autres, point 13).

    Catégorie ``policy``. Facettes : ``policy``, ``point`` et ``decision``.
    Contenu : ``reason``, ``arguments`` et ``output``. Statut ``error`` pour
    ``fail``, ``warning`` pour une décision imposée par une erreur ou pour
    ``continue``, ``ok`` sinon.

    Attributes:
        type: ``policy.decided``.
        policy: Nom de la politique, ou de la règle du moteur.
        point: Point d'accroche : ``before_model``, ``after_model``,
            ``before_tool``, ``after_tool`` ou ``on_output``.
        decision: ``continue``, ``replace``, ``retry``, ``deny``, ``pause``,
            ``stop`` ou ``fail``.
        reason: Motif, diagnostic ou erreur, selon la décision.
        call_id: Appel d'outil concerné (``before_tool``, ``after_tool``).
        attempt: Pour ``retry`` : numéro de la réparation demandée par cette
            politique dans le run.
        tools: Pour ``retry`` : vrai si l'orchestrateur garde ses outils pour
            réparer.
        arguments: Pour ``replace`` à ``before_tool`` : arguments qui partent
            à la place de ceux du modèle.
        output: Pour ``replace`` à ``on_output`` : réponse finale retenue.
        error: Vrai si la décision est imposée par une erreur de la politique
            (exception, délai, décision non permise).
    """

    category: ClassVar[EventCategory] = "policy"
    facet_fields: ClassVar[tuple[str, ...]] = ("policy", "point", "decision")

    content_fields: ClassVar[tuple[str, ...]] = (
        "reason",
        "arguments",
        "output",
    )
    type: Literal["policy.decided"] = "policy.decided"
    policy: str
    point: HookPoint
    decision: DecisionKind
    reason: str = ""
    call_id: str | None = None
    attempt: PositiveInt | None = None
    tools: bool | None = None
    arguments: dict[str, JsonValue] | None = None
    output: Message | None = None
    error: bool = False

    @model_validator(mode="after")
    def _check_output(self) -> Self:
        """Refuse une réponse de remplacement qui porte des octets de fichier."""
        _no_inline_data(self.output, "policy.decided")
        return self

    @property
    def event_status(self) -> EventStatus:
        """``error`` pour ``fail`` ; ``warning`` pour une décision imposée par une erreur ou pour ``continue`` ; ``ok`` sinon."""
        if self.decision == "fail":
            return "error"
        if self.error or self.decision == "continue":
            return "warning"
        return "ok"


class GuardChecked(Payload):
    """Contrôle d'une sortie par un guard (contrat ou juge), réussi ou non (point 20).

    Écrit avant la décision qu'il motive (``policy.decided``). ``attempt``
    compte les contrôles de cette sortie : 1, puis 2 après une réparation…
    ``resolution`` dit la suite d'un échec : réparation demandée, ou, une fois
    les réparations épuisées, ``fail``, ``unverified`` ou ``fallback``.

    Catégorie ``guard``. Facettes : ``guard``, ``target`` et ``outcome``.
    Contenu : ``reason``. Statut ``ok`` si le contrôle a réussi ou a été
    sauté, ``error`` s'il a échoué et que le run échoue (``fail``),
    ``warning`` pour un autre échec.

    Attributes:
        type: ``guard.checked``.
        guard: Nom du guard (contrat ou juge).
        target: Sortie contrôlée : ``output`` (réponse finale),
            ``role:<nom>`` ou ``tool:<nom>``.
        outcome: ``passed``, ``failed`` ou ``skipped``.
        reason: Pourquoi le contrôle a échoué (sans recopier la valeur
            fautive, voir ``schema_problem``) ou a été sauté.
        attempt: Numéro du contrôle de cette sortie, à partir de 1.
        normalized: Vrai si le contrat a normalisé la sortie avant de la
            contrôler (``normalize`` du contrat) et que le texte a changé.
        resolution: Suite d'un échec : ``retry``, ``fail``, ``unverified`` ou
            ``fallback`` ; ``None`` si le contrôle a réussi.
        policy: Politique qui a fait le contrôle.
        call_id: Appel d'outil contrôlé, pour une cible ``tool:<nom>`` ou
            ``role:<nom>``.
    """

    category: ClassVar[EventCategory] = "guard"
    facet_fields: ClassVar[tuple[str, ...]] = ("guard", "target", "outcome")

    content_fields: ClassVar[tuple[str, ...]] = ("reason",)
    type: Literal["guard.checked"] = "guard.checked"
    guard: str
    target: str
    outcome: CheckOutcome
    reason: str = ""
    attempt: PositiveInt = 1
    normalized: bool = False
    resolution: CheckResolution | None = None
    policy: str | None = None
    call_id: str | None = None

    @property
    def event_status(self) -> EventStatus:
        """``ok`` si le contrôle a réussi ou a été sauté ; en échec, ``error`` si le run échoue (``fail``), ``warning`` sinon."""
        if self.outcome != "failed":
            return "ok"
        return "error" if self.resolution == "fail" else "warning"


class BudgetExceeded(Payload):
    """Limite d'un budget atteinte, vue avant un appel de l'orchestrateur (fonction J4).

    Écrit une fois par limite et par run ; avec ``action: stop``, il précède le
    ``policy.decided`` qui fait passer le run en ``FINALIZING``.

    Catégorie ``policy``. Facettes : ``scope``, ``limit`` et ``action``.
    Aucun contenu. Statut ``warning``.

    Attributes:
        type: ``budget.exceeded``.
        scope: Portée du budget : ``run`` ou ``session``.
        limit: Limite atteinte : ``max_cost``, ``max_tokens`` ou
            ``max_calls``.
        value: Plafond déclaré, dans l'unité de la limite (dollars, tokens ou
            appels).
        spent: Consommation au moment du contrôle, dans la même unité.
        action: ``warn`` (le run continue) ou ``stop`` (réponse forcée).
        policy: Politique de budget qui a fait le contrôle (``loom.budget``).
    """

    category: ClassVar[EventCategory] = "policy"
    facet_fields: ClassVar[tuple[str, ...]] = ("scope", "limit", "action")

    content_fields: ClassVar[tuple[str, ...]] = ()
    type: Literal["budget.exceeded"] = "budget.exceeded"
    scope: BudgetScope
    limit: BudgetLimit
    value: NonNegativeFloat
    spent: NonNegativeFloat
    action: OnExceed
    policy: str | None = None

    @property
    def event_status(self) -> EventStatus:
        """Toujours ``warning``."""
        return "warning"

    @property
    def key(self) -> str:
        """Limite concernée, sous la forme ``<portée>.<limite>`` : ``run.max_cost``, ``session.max_tokens``, etc."""
        return f"{self.scope}.{self.limit}"


class JudgeEvaluated(Payload):
    """Verdict d'un juge sur une sortie : une note par critère (point 21, fonction E3).

    L'appel du modèle du juge est journalisé dans le run jugé
    (``model.retried``, ``model.responded`` avec ``judge``, enveloppe au nom
    de ``judge:<nom>``) : son coût s'ajoute au run, pas ses itérations. Le
    verdict est écrit après cet appel, avant le ``guard.checked`` qui en tire
    la suite. ``blocked`` : un critère bloquant est sous son seuil, et la
    sortie est refusée.

    Catégorie ``guard``. Facettes : ``judge``, ``target``, ``model_id``,
    ``passed`` et ``blocked``, plus ``model`` quand il est connu. Contenu :
    ``criteria[].reason``.

    Attributes:
        type: ``judge.evaluated``.
        judge: Nom du juge.
        target: Sortie jugée : ``output`` (réponse finale) ou ``role:<nom>``.
        model_id: Nom, chez le fournisseur, du modèle du juge.
        model: Identifiant du modèle dans la config (``models[].id``).
            Absent des journaux antérieurs.
        criteria: Une note par critère, au moins une : nom, note, seuil,
            bloquant ou non, justification.
        passed: Vrai si tous les critères atteignent leur seuil.
        blocked: Vrai si un critère bloquant est sous son seuil.
        attempt: Numéro du contrôle de cette sortie, à partir de 1.
        policy: Politique qui a fait le contrôle.
        call_id: Appel du rôle jugé, pour une cible ``role:<nom>``.

    Raises:
        ValueError: ``passed`` ou ``blocked`` ne s'accorde pas avec les notes
            (« judge.evaluated : 'passed' incohérent avec les notes »).
    """

    category: ClassVar[EventCategory] = "guard"
    facet_fields: ClassVar[tuple[str, ...]] = ("judge", "target", "model_id", "passed", "blocked")

    content_fields: ClassVar[tuple[str, ...]] = ("criteria[].reason",)
    type: Literal["judge.evaluated"] = "judge.evaluated"
    judge: str
    target: str
    model_id: str
    model: str | None = None
    criteria: tuple[CriterionScore, ...] = Field(min_length=1)
    passed: bool
    blocked: bool
    attempt: PositiveInt = 1
    policy: str | None = None
    call_id: str | None = None

    @model_validator(mode="after")
    def _check_verdict(self) -> Self:
        """Vérifie que ``passed`` et ``blocked`` s'accordent avec les notes des critères."""
        if self.passed != all(c.passed for c in self.criteria):
            raise ValueError("judge.evaluated : 'passed' incohérent avec les notes")
        if self.blocked != any(c.blocking and not c.passed for c in self.criteria):
            raise ValueError("judge.evaluated : 'blocked' incohérent avec les notes")
        return self

    @property
    def event_status(self) -> EventStatus:
        """``ok`` si le juge a validé la sortie (``passed``), ``warning`` sinon."""
        return "ok" if self.passed else "warning"

    def facets(self) -> dict[str, FacetValue]:
        """Facettes : celles de ``facet_fields``, plus ``model`` quand il est connu."""
        facets = super().facets()
        if self.model is not None:
            facets["model"] = self.model
        return facets


# --- Approbations ------------------------------------------------------------


class ApprovalRequested(Payload):
    """Appel d'outil suspendu en attendant une décision humaine (point 17, fonction D10).

    Écrit par l'exécuteur, après les appels du lot qui ne demandent rien : le
    run passe alors en ``PAUSED``. Ce que l'approbateur doit voir est ici —
    l'outil, ses arguments, le motif —, car c'est la seule chose qu'il lira.

    ``expire_at`` fait foi : passé cette date, la demande est expirée, qu'un
    travail différé ait tourné ou non. Une file perdue dans un redémarrage ne
    peut donc pas laisser une demande approuvable indéfiniment.

    Catégorie ``approval``. Facette : ``tool_name``. Contenu : ``arguments``
    et ``reason``. Statut ``warning``.

    Attributes:
        type: ``approval.requested``.
        call_id: Appel suspendu.
        tool_name: Nom de l'outil.
        arguments: Arguments tels qu'ils seront exécutés : références déjà
            résolues.
        reason: Pourquoi l'approbation est demandée : la déclaration de
            l'outil (« la config exige une approbation pour chaque appel de
            cet outil »), ou le motif de la politique qui l'a exigée.
        policy: Politique qui l'a exigée ; absente pour un outil en
            ``approval: always``.
        approvers: Rôles d'accès qui peuvent trancher, figés ici
            (``docs/loom_role_user.md`` §5.3) ; ``None`` : tout rôle qui a
            ``approve`` sur l'agent. N'est écrit que s'il restreint.
        self_approval: Faux si l'agent interdit l'auto-approbation
            (``access.self_approval: false``, décision D-7b des utilisateurs
            et rôles). N'est écrit que s'il est faux. Le ``scope`` des
            journaux d'avant 2.0.17, que personne ne contrôlait, est ignoré à
            la lecture, comme tout champ inconnu.
        expire_at: Échéance de la demande (``approval.expires_in`` de
            l'agent, 24 h par défaut) ; ``None`` : sans échéance.
    """

    category: ClassVar[EventCategory] = "approval"
    facet_fields: ClassVar[tuple[str, ...]] = ("tool_name",)

    content_fields: ClassVar[tuple[str, ...]] = (
        "arguments",
        "reason",
    )
    type: Literal["approval.requested"] = "approval.requested"
    call_id: str
    tool_name: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    reason: str = ""
    policy: str | None = None
    approvers: tuple[str, ...] | None = Field(default=None, exclude_if=lambda value: value is None)
    self_approval: bool = Field(default=True, exclude_if=lambda value: value)
    expire_at: datetime | None = None

    @property
    def event_status(self) -> EventStatus:
        """Toujours ``warning`` : le run attend une décision humaine."""
        return "warning"

    def as_pending(self, run_id: RunId | None = None, agent: str | None = None) -> PendingApproval:
        """La demande telle qu'elle attend, dans l'état du run ou devant l'approbateur.

        Args:
            run_id: Run qui l'a écrite. L'événement ne le porte pas dans sa
                charge (il est dans l'enveloppe) : qui le lit le donne.
            agent: Agent de ce run, pour la même raison.

        Returns:
            La demande en attente : outil, arguments, motif, approbateurs,
            auto-approbation permise ou non, échéance.
        """
        return PendingApproval(
            call_id=self.call_id,
            tool_name=self.tool_name,
            arguments=self.arguments,
            reason=self.reason,
            run_id=run_id,
            agent=agent,
            policy=self.policy,
            approvers=self.approvers,
            self_approval=self.self_approval,
            expire_at=self.expire_at,
        )


class ApprovalGranted(Payload):
    """Appel autorisé par un approbateur : il s'exécute, éventuellement avec d'autres arguments (point 17).

    Catégorie ``approval``. Facettes : ``tool_name`` et ``by``, plus ``via``
    quand une passerelle a porté la décision. Contenu : ``arguments`` et
    ``reason``.

    Attributes:
        type: ``approval.granted``.
        call_id: Appel autorisé.
        tool_name: Nom de l'outil.
        by: Identité de l'approbateur : clé d'API, utilisateur, ou nom rendu
            par un approbateur en ligne. L'audit du point 17 tient dans ce
            champ.
        reason: Motif donné par l'approbateur.
        arguments: Arguments corrigés par l'approbateur (permission
            ``amend``) ; absents, ceux de la demande valent.
        via: Passerelle qui a porté la décision (``as_user``, décision D-5
            des utilisateurs et rôles) ; écrite seulement avec une passerelle.
        roles: Rôles d'accès qui ont permis la décision, figés pour l'audit :
            la config peut changer ensuite. Écrits seulement s'ils sont connus.
        self_approved: Vrai si le décideur est celui qui a lancé le run
            (``self_approve``, décision D-7 des utilisateurs et rôles). Écrit
            seulement s'il est vrai.
    """

    category: ClassVar[EventCategory] = "approval"
    facet_fields: ClassVar[tuple[str, ...]] = ("tool_name", "by")

    content_fields: ClassVar[tuple[str, ...]] = (
        "arguments",
        "reason",
    )
    type: Literal["approval.granted"] = "approval.granted"
    call_id: str
    tool_name: str
    by: str | None = None
    reason: str = ""
    arguments: dict[str, JsonValue] | None = None
    via: str | None = Field(default=None, exclude_if=lambda value: value is None)
    roles: tuple[str, ...] = Field(default=(), exclude_if=lambda value: not value)
    self_approved: bool = Field(default=False, exclude_if=lambda value: not value)

    def facets(self) -> dict[str, FacetValue]:
        """Facettes : ``tool_name`` et ``by``, plus ``via`` quand une passerelle a porté la décision."""
        return _with_via(super().facets(), self.via)


class ApprovalRejected(Payload):
    """Appel refusé par un approbateur : le motif part au modèle comme résultat d'erreur (point 17).

    Catégorie ``approval``. Facettes : ``tool_name`` et ``by``, plus ``via``
    quand une passerelle a porté la décision. Contenu : ``reason``. Statut
    ``warning``.

    Attributes:
        type: ``approval.rejected``.
        call_id: Appel refusé.
        tool_name: Nom de l'outil.
        by: Identité de qui a refusé.
        reason: Motif du refus, rendu au modèle.
        via: Passerelle qui a porté la décision (``as_user``, décision D-5
            des utilisateurs et rôles) ; écrite seulement avec une passerelle.
        roles: Rôles d'accès qui ont permis la décision, figés pour l'audit :
            la config peut changer ensuite. Écrits seulement s'ils sont connus.
        self_approved: Vrai si le décideur est celui qui a lancé le run
            (``self_approve``, décision D-7 des utilisateurs et rôles). Écrit
            seulement s'il est vrai.
    """

    category: ClassVar[EventCategory] = "approval"
    facet_fields: ClassVar[tuple[str, ...]] = ("tool_name", "by")

    content_fields: ClassVar[tuple[str, ...]] = ("reason",)
    type: Literal["approval.rejected"] = "approval.rejected"
    call_id: str
    tool_name: str
    by: str | None = None
    reason: str = ""
    via: str | None = Field(default=None, exclude_if=lambda value: value is None)
    roles: tuple[str, ...] = Field(default=(), exclude_if=lambda value: not value)
    self_approved: bool = Field(default=False, exclude_if=lambda value: not value)

    def facets(self) -> dict[str, FacetValue]:
        """Facettes : ``tool_name`` et ``by``, plus ``via`` quand une passerelle a porté la décision."""
        return _with_via(super().facets(), self.via)

    @property
    def event_status(self) -> EventStatus:
        """Toujours ``warning``."""
        return "warning"


class ApprovalExpired(Payload):
    """Demande d'approbation laissée sans réponse jusqu'à son ``expire_at`` (point 17).

    ``action`` dit ce qu'il advient du run : ``deny``, l'appel est refusé et
    le modèle continue ; ``fail``, le run échoue. C'est un « non » prudent par
    défaut : personne n'a dit oui. L'action vient d'``approval.on_expiry`` de
    l'agent.

    Catégorie ``approval``. Facettes : ``tool_name`` et ``action``. Aucun
    contenu. Statut ``warning``.

    Attributes:
        type: ``approval.expired``.
        call_id: Appel dont la demande expire.
        tool_name: Nom de l'outil.
        action: ``deny`` ou ``fail``. Défaut : ``deny``.
        expire_at: Échéance qui était celle de la demande.
    """

    category: ClassVar[EventCategory] = "approval"
    facet_fields: ClassVar[tuple[str, ...]] = ("tool_name", "action")

    content_fields: ClassVar[tuple[str, ...]] = ()
    type: Literal["approval.expired"] = "approval.expired"
    call_id: str
    tool_name: str
    action: ExpiryAction = "deny"
    expire_at: datetime

    @property
    def event_status(self) -> EventStatus:
        """Toujours ``warning``."""
        return "warning"


# --- Questions à l'utilisateur ------------------------------------------------


class QuestionAsked(Payload):
    """Le modèle pose une question à l'utilisateur (outil ``loom.ask``) : le run attend sa réponse.

    Écrite par l'exécuteur après les appels du lot, comme une demande
    d'approbation : le run passe alors en ``PAUSED``. ``expire_at`` fait foi.

    Catégorie ``question``. Facette : ``tool_name``. Contenu : ``questions``.
    Statut ``warning``.

    Attributes:
        type: ``question.asked``.
        call_id: Appel de ``loom.ask`` qui pose la question.
        tool_name: Nom de l'outil (``ask``).
        questions: Les questions, une ou plusieurs : texte, titre court,
            options proposées, choix multiple ou non.
        expire_at: Échéance (``expires_in`` de ``loom.ask``) ; ``None`` : la
            question attend indéfiniment.
    """

    category: ClassVar[EventCategory] = "question"
    facet_fields: ClassVar[tuple[str, ...]] = ("tool_name",)
    content_fields: ClassVar[tuple[str, ...]] = ("questions",)
    type: Literal["question.asked"] = "question.asked"
    call_id: str
    tool_name: str
    questions: tuple[Question, ...]
    expire_at: datetime | None = None

    @property
    def event_status(self) -> EventStatus:
        """Toujours ``warning`` : le run attend une réponse de l'utilisateur."""
        return "warning"

    def as_pending(self, run_id: RunId | None = None, agent: str | None = None) -> PendingQuestion:
        """La question telle qu'elle attend, dans l'état du run ou devant l'utilisateur.

        Args:
            run_id: Run qui l'a écrite ; l'enveloppe le porte, pas la charge.
            agent: Agent de ce run.

        Returns:
            La question en attente, avec son échéance.
        """
        return PendingQuestion(
            call_id=self.call_id,
            questions=self.questions,
            run_id=run_id,
            agent=agent,
            expire_at=self.expire_at,
        )


class QuestionAnswered(Payload):
    """L'utilisateur a répondu : sa réponse part au modèle comme résultat de l'appel.

    Un message tapé dans la session pendant que le run attend devient aussi
    la réponse (``text``).

    Catégorie ``question``. Facettes : ``tool_name`` et ``by``, plus ``via``
    quand une passerelle a porté la réponse. Contenu : ``answers`` et
    ``text``.

    Attributes:
        type: ``question.answered``.
        call_id: Appel de ``loom.ask`` auquel répondre.
        tool_name: Nom de l'outil.
        answers: Options choisies, une liste par question, dans l'ordre des
            questions.
        text: Réponse libre.
        by: Identité de qui a répondu.
        via: Passerelle qui a porté la réponse (``as_user``, décision D-5 des
            utilisateurs et rôles) ; absente sans passerelle.
    """

    category: ClassVar[EventCategory] = "question"
    facet_fields: ClassVar[tuple[str, ...]] = ("tool_name", "by")
    content_fields: ClassVar[tuple[str, ...]] = ("answers", "text")
    type: Literal["question.answered"] = "question.answered"
    call_id: str
    tool_name: str
    answers: tuple[tuple[str, ...], ...] | None = None
    text: str | None = None
    by: str | None = None
    via: str | None = Field(default=None, exclude_if=lambda value: value is None)

    def reply(self) -> QuestionReply:
        """La réponse telle que la lit le moteur, pour la rendre au modèle comme résultat de l'appel."""
        return QuestionReply(answers=self.answers, text=self.text, by=self.by, via=self.via)


class QuestionExpired(Payload):
    """Question restée sans réponse jusqu'à son ``expire_at`` : le modèle l'apprend et le run reprend.

    Catégorie ``question``. Facette : ``tool_name``. Aucun contenu. Statut
    ``warning``.

    Attributes:
        type: ``question.expired``.
        call_id: Appel de ``loom.ask`` resté sans réponse.
        tool_name: Nom de l'outil.
        expire_at: Échéance qui était celle de la question.
    """

    category: ClassVar[EventCategory] = "question"
    facet_fields: ClassVar[tuple[str, ...]] = ("tool_name",)
    content_fields: ClassVar[tuple[str, ...]] = ()
    type: Literal["question.expired"] = "question.expired"
    call_id: str
    tool_name: str
    expire_at: datetime

    @property
    def event_status(self) -> EventStatus:
        """Toujours ``warning``."""
        return "warning"


# --- Idempotence -------------------------------------------------------------


class IdempotencyRecorded(Payload):
    """Effet d'un outil mémorisé sous sa clé, pour ne pas le refaire (points 18 et 49).

    Écrit par le magasin ``journal``, qui n'a pas d'autre stockage que le
    journal du run : la trace est durable dès que le journal l'est. Une clé
    réservée puis non tenue n'écrit rien — c'est la clé **technique** d'un
    appel, et le journal du run suffit à la porter.

    Sa catégorie lui est propre : rangé dans ``tool``, cet événement
    deviendrait la cause d'une transition (``drive`` la choisit parmi les
    catégories ``model`` et ``tool``), alors qu'il n'est l'effet de rien — il
    en garde la trace.

    Catégorie ``idempotency``. Facettes : ``tool_name`` et ``key``. Contenu :
    ``result``.

    Attributes:
        type: ``idempotency.recorded``.
        key: Clé d'idempotence de l'appel.
        call_id: Appel dont l'effet est mémorisé.
        tool_name: Nom de l'outil.
        result: Ce que l'outil a rendu, tel qu'il sera rendu au rejeu de
            l'appel.
    """

    category: ClassVar[EventCategory] = "idempotency"
    facet_fields: ClassVar[tuple[str, ...]] = ("tool_name", "key")

    content_fields: ClassVar[tuple[str, ...]] = ("result",)
    type: Literal["idempotency.recorded"] = "idempotency.recorded"
    key: str
    call_id: str
    tool_name: str
    result: JsonValue = None


class IdempotencyReused(Payload):
    """Appel qui n'a rien fait : son effet était déjà mémorisé sous sa clé (points 18 et 49).

    Écrit par le moteur, quel que soit le magasin — c'est l'outil décoré qui
    le lui signale, puisque lui seul connaît sa clé. Sans cet événement, un
    run dont l'effet a été dédoublonné ne le raconte pas : on y lit un appel,
    puis un résultat venu d'ailleurs, et rien qui l'explique. Le magasin
    ``journal`` laissait au moins son ``idempotency.recorded`` ; un magasin
    partagé, lui, ne laisse rien dans le journal du run.

    Il ne porte pas le résultat : celui-ci est dans le ``tool.completed`` de
    l'appel, et le redire ne dirait rien de plus.

    Catégorie ``idempotency``. Facettes : ``tool_name`` et ``key``. Aucun
    contenu.

    Attributes:
        type: ``idempotency.reused``.
        key: Clé d'idempotence sous laquelle l'effet était mémorisé.
        call_id: Appel qui n'a rien refait.
        tool_name: Nom de l'outil.
    """

    category: ClassVar[EventCategory] = "idempotency"
    facet_fields: ClassVar[tuple[str, ...]] = ("tool_name", "key")

    content_fields: ClassVar[tuple[str, ...]] = ()
    type: Literal["idempotency.reused"] = "idempotency.reused"
    key: str
    call_id: str
    tool_name: str


# --- Artefacts ---------------------------------------------------------------


class ArtifactStored(Payload):
    """Fichier rangé dans le stockage d'artefacts (fonction G2, point 16).

    Les fichiers ne sont jamais dans le journal : cet événement annonce qu'un
    fichier a été rangé, et les messages ne portent que sa référence. Trois
    origines : pièce jointe de la demande (écrit avant ``message.user``),
    fichier produit par un outil, ou résultat déporté (écrits avant le
    ``tool.completed`` de l'appel, dans son span).

    Catégorie ``artifact``. Facettes : ``origin``, ``media_type`` et
    ``size``. Contenu : ``name``.

    Attributes:
        type: ``artifact.stored``.
        uri: Référence du fichier dans le stockage (``artifact://…``).
        media_type: Type MIME du fichier.
        size: Taille, en octets.
        name: Nom du fichier, tel que donné par l'utilisateur ou l'outil.
        origin: ``attachment``, ``tool_output`` ou ``offload``.
        call_id: Appel d'outil qui l'a produit (sortie ou déport).
    """

    category: ClassVar[EventCategory] = "artifact"
    facet_fields: ClassVar[tuple[str, ...]] = ("origin", "media_type", "size")

    content_fields: ClassVar[tuple[str, ...]] = ("name",)
    type: Literal["artifact.stored"] = "artifact.stored"
    uri: str
    media_type: str
    size: NonNegativeInt
    name: str | None = None
    origin: ArtifactOrigin
    call_id: str | None = None

    @property
    def record(self) -> ArtifactRecord:
        """La fiche du fichier (``ArtifactRecord``), telle que la listent ``RunResult.produced`` et les fichiers d'une session."""
        return ArtifactRecord(
            uri=self.uri,
            media_type=self.media_type,
            size=self.size,
            name=self.name,
            origin=self.origin,
            call_id=self.call_id,
        )


# --- Session -----------------------------------------------------------------


class SessionSnapshot(Payload):
    """Historique de la session matérialisé à la fin d'un run (point 22).

    Le journal reste la source : le snapshot n'est qu'une vue déjà calculée,
    pour ne pas relire toute la session à chaque run. ``messages`` est
    exactement ce que rend la projection d'historique pour les événements
    jusqu'à ``up_to_seq``, résumé de compaction compris. Un lecteur qui
    l'ignore obtient le même historique, plus lentement. Sa fréquence se règle
    par ``sessions.snapshot_every`` (50 événements par défaut).

    ``up_to_seq`` s'arrête avant le premier événement d'un run encore en
    cours : sinon la relecture reprendrait un run au milieu.

    Catégorie ``session`` : l'événement parle de la session, pas du run qui
    l'écrit. Facettes : ``up_to_seq``, ``messages`` (leur nombre) et
    ``tokens``. Contenu : ``messages``.

    Attributes:
        type: ``session.snapshot``.
        up_to_seq: Dernier ``seq`` couvert par le snapshot.
        messages: L'historique, sans octets de fichier.
        tokens: Estimation de la taille de l'historique, pour les seuils de
            compaction (point 23).
    """

    category: ClassVar[EventCategory] = "session"
    facet_fields: ClassVar[tuple[str, ...]] = ("up_to_seq", "messages", "tokens")

    content_fields: ClassVar[tuple[str, ...]] = ("messages",)
    type: Literal["session.snapshot"] = "session.snapshot"
    up_to_seq: NonNegativeInt
    messages: tuple[Message, ...] = ()
    tokens: NonNegativeInt = 0

    @model_validator(mode="after")
    def _check_messages(self) -> Self:
        """Refuse un historique qui porte des octets de fichier au lieu de références."""
        for message in self.messages:
            _no_inline_data(message, "session.snapshot")
        return self

    def facets(self) -> dict[str, FacetValue]:
        """Facettes : ``up_to_seq``, ``messages`` (leur nombre, pas leur contenu) et ``tokens``."""
        return {
            "up_to_seq": self.up_to_seq,
            "messages": len(self.messages),
            "tokens": self.tokens,
        }


type Fidelity = Literal["ok", "warning", "skipped"]
"""Issue du contrôle de fidélité d'un résumé de compaction."""


class SessionCompacted(Payload):
    """Segment ancien de la session remplacé par un résumé (point 23, fonction F3).

    La compaction ne réécrit rien : elle ajoute ce marqueur, et l'historique
    repart du résumé pour tout ce qui précède ``up_to_seq``. Les ``kept``
    derniers tours, eux, restent intacts et sont rejoués. Le résumé est
    produit par un run interne (``kind: compaction``) avec le modèle de
    ``sessions.compaction``.

    Catégorie ``session``. Facettes : ``up_to_seq``, ``kept``,
    ``tokens_before``, ``tokens_after`` et ``fidelity``. Contenu :
    ``summary``. Statut ``warning`` si le contrôle de fidélité a trouvé des
    repères manquants, ``ok`` sinon.

    Attributes:
        type: ``session.compacted``.
        up_to_seq: Dernier ``seq`` remplacé par le résumé.
        summary: Le résumé, non vide.
        kept: Tours laissés intacts après le résumé (``keep_last``).
        tokens_before: Taille estimée de l'historique avant la compaction.
        tokens_after: Taille estimée après.
        cost_usd: Coût du résumé, en dollars.
        fidelity: Contrôle déterministe des repères du segment (références
            comme ``D-2026-042``, adresses e-mail, nombres d'au moins trois
            chiffres ; ``guards/fidelity.py``) : ``ok``, ``warning`` s'il en
            manque encore dans le résumé après une nouvelle tentative, ou
            ``skipped`` si le contrôle n'a pas été fait.
    """

    category: ClassVar[EventCategory] = "session"
    facet_fields: ClassVar[tuple[str, ...]] = (
        "up_to_seq",
        "kept",
        "tokens_before",
        "tokens_after",
        "fidelity",
    )

    content_fields: ClassVar[tuple[str, ...]] = ("summary",)
    type: Literal["session.compacted"] = "session.compacted"
    up_to_seq: NonNegativeInt
    summary: str = Field(min_length=1)
    kept: NonNegativeInt = 0
    tokens_before: NonNegativeInt = 0
    tokens_after: NonNegativeInt = 0
    cost_usd: NonNegativeFloat = 0.0
    fidelity: Fidelity = "ok"

    @property
    def event_status(self) -> EventStatus:
        """``warning`` si le contrôle de fidélité a trouvé des repères manquants, ``ok`` sinon."""
        return "warning" if self.fidelity == "warning" else "ok"


class SessionTrimmed(Payload):
    """Tours les plus anciens retirés faute de résumé (filet de sécurité, point 23).

    Écrit quand l'historique dépasse la fenêtre du modèle et que la
    compaction n'a pas pu résumer : l'historique repart après ``up_to_seq``,
    sans rien à la place.

    Catégorie ``session``. Facettes : ``up_to_seq`` et ``dropped``. Aucun
    contenu. Statut ``warning``.

    Attributes:
        type: ``session.trimmed``.
        up_to_seq: Dernier ``seq`` retiré de l'historique.
        dropped: Messages d'historique retirés.
        reason: Pourquoi le résumé n'a pas pu se faire.
    """

    category: ClassVar[EventCategory] = "session"
    facet_fields: ClassVar[tuple[str, ...]] = ("up_to_seq", "dropped")

    content_fields: ClassVar[tuple[str, ...]] = ()
    type: Literal["session.trimmed"] = "session.trimmed"
    up_to_seq: NonNegativeInt
    dropped: NonNegativeInt = 0
    reason: str = ""

    @property
    def event_status(self) -> EventStatus:
        """Toujours ``warning`` : de l'historique a été perdu."""
        return "warning"


type DurablePayload = Annotated[
    RunStarted
    | StepStarted
    | StepCompleted
    | RunTransitioned
    | RunCompleted
    | RunFailed
    | RunCancelled
    | RunClaimed
    | UserMessage
    | ModelResponded
    | ModelRetried
    | ModelExchanged
    | ModelFellBack
    | CircuitOpened
    | ToolCalled
    | ToolCompleted
    | ToolSourceUnavailable
    | PolicyDecided
    | GuardChecked
    | JudgeEvaluated
    | BudgetExceeded
    | ApprovalRequested
    | ApprovalGranted
    | ApprovalRejected
    | ApprovalExpired
    | QuestionAsked
    | QuestionAnswered
    | QuestionExpired
    | IdempotencyRecorded
    | IdempotencyReused
    | ArtifactStored
    | SessionSnapshot
    | SessionCompacted
    | SessionTrimmed,
    Field(discriminator="type"),
]
"""Union des charges qu'un événement écrit peut porter, discriminée par ``type``."""

# Tirés de l'union, et non recopiés : une liste tenue à la main dérive — elle
# avait perdu `run.cancelled` et `run.claimed` (phase 4.2).
DURABLE_PAYLOADS: tuple[type[Payload], ...] = get_args(get_args(DurablePayload.__value__)[0])
"""Toutes les classes de charges durables, dans l'ordre de ``DurablePayload``."""

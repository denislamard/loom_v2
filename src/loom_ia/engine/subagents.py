# SPDX-License-Identifier: Apache-2.0
"""Sous-agents : un agent appelé comme un outil, dans un run enfant (fonction C5, point 4).

L'orchestrateur appelle un sous-agent avec un seul argument, ``message`` : la
demande qu'il lui adresse, comme à n'importe quel agent. Le sous-agent tourne
avec sa propre boucle, ses outils, ses modèles et ses limites, dans un **run
enfant** :

- même journal que le parent (même session), par le même écrivain ;
- ``root_run_id`` du parent, ``parent_run_id``, ``parent_call_id``, et une
  profondeur de plus ; contexte de l'appelant hérité ;
- ni l'historique de la session ni celui du parent : seulement le message.

Seule la réponse finale de l'enfant revient au parent, comme résultat
d'outil ; un échec de l'enfant devient un résultat d'erreur. Sa consommation
(tout son run) s'ajoute à celle du parent.

Reprise : l'identifiant de l'enfant est choisi avant l'appel et écrit dans le
``tool.called``. Si le parent est interrompu, la reprise retrouve l'enfant et
le fait avancer au lieu d'en lancer un autre ; s'il avait fini, sa réponse
est reprise telle quelle.

Profondeur : un sous-agent n'est proposé que si la profondeur du run appelant
est inférieure au ``max_depth`` de son agent. Au-delà, il est masqué ; une
chaîne d'agents qui s'appellent en boucle reste donc bornée.

Annulation : l'enfant tourne dans la tâche de l'appel ; annuler le parent
l'annule aussi. Rien n'est écrit : parent et enfant restent reprenables.
Clore le parent — délai dépassé, ``cancel``, échec d'une politique, d'un modèle
ou d'un outil — est autre chose : plus rien ne reprendra l'enfant, qui est alors
annulé avec lui (``run.cancelled``, ``reason: parent``), avant le parent et dans
la même écriture, au lieu de rester ouvert au journal.

Config : ``agents.<nom>.subagents`` de l'agent appelant (nom de l'outil,
agent, description, ``budget_share``) et son ``max_depth``.
"""

from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, replace
from typing import Final

from pydantic import JsonValue

from loom_ia.core.model import (
    ArtifactRefBlock,
    JsonBlock,
    Message,
    OutputBlock,
    PendingCall,
    RunBudget,
    RunId,
    RunState,
    RunStatus,
    SideEffects,
    TextBlock,
    ToolOutput,
    ToolSpec,
    new_run_id,
)
from loom_ia.core.ports import ToolContext
from loom_ia.engine.delegated import (
    Consumption,
    DelegatedPayload,
    DelegatedTool,
    RunView,
    Waiting,
)
from loom_ia.engine.loop import ParentRun, RunContext, begin_run, drive
from loom_ia.engine.writer import SessionWriter

AGENT_HINT: Final = "Sous-agent : il ne reçoit que `message`, sans la conversation ni les résultats précédents ; mets-y tout ce dont il a besoin."
"""Mention ajoutée à la description d'un sous-agent, montrée au modèle."""

MESSAGE_SCHEMA: Final[dict[str, JsonValue]] = {
    "type": "object",
    "properties": {
        "message": {
            "type": "string",
            "minLength": 1,
            "description": "La demande adressée au sous-agent, complète",
        }
    },
    "required": ["message"],
    "additionalProperties": False,
}
"""Schéma des arguments d'un sous-agent : un seul champ, ``message``, chaîne non vide."""

type AgentResolver = Callable[[str], RunContext]
"""Contexte d'exécution d'un agent de la config, par son nom."""


@dataclass(frozen=True, kw_only=True)
class SubAgentDefinition:
    """Un sous-agent tel que le moteur l'appelle.

    Attributes:
        name: Nom de l'outil vu par l'orchestrateur.
        agent: Agent de la config qui fait le travail.
        description: Description de l'outil ; ``AGENT_HINT`` y est ajouté.
        max_depth: ``max_depth`` de l'agent appelant : profondeur au-delà de
            laquelle le sous-agent est masqué.
        budget_share: Part de ce qui reste au budget du run appelant, donnée à
            l'enfant (fonction J4) ; ``None`` : pas de part, l'enfant n'a que
            ses propres limites.
        parent_budget: Limites du run de l'agent appelant (sa config) ; s'y
            ajoute sa propre part reçue.
        side_effects: Effets du sous-agent, ceux des outils de son agent ; ils
            décident de l'ordre des appels d'un même tour. ``irreversible`` à
            défaut de déclaration.
    """

    name: str
    agent: str
    description: str
    max_depth: int = 1
    budget_share: float | None = None
    parent_budget: RunBudget | None = None
    side_effects: SideEffects = "irreversible"


_WAITING: Final = frozenset({RunStatus.PAUSED, RunStatus.WAITING_CHILD})
"""États dans lesquels un enfant attend quelqu'un : son parent attend avec lui."""


class AgentTool(DelegatedTool):
    """Outil qui confie la demande à un autre agent, dans un run enfant.

    L'outil est déclaré idempotent : un appel interrompu reprend l'enfant au
    lieu de le relancer.

    Args:
        definition: Le sous-agent.
        agents: Donne le contexte d'exécution d'un agent de la config.

    Attributes:
        definition: Le sous-agent.
    """

    def __init__(self, definition: SubAgentDefinition, agents: AgentResolver) -> None:
        """Construit la description de l'outil."""
        self.definition = definition
        self._agents = agents
        description = definition.description
        self._spec = ToolSpec(
            name=definition.name,
            description=f"{description}\n\n{AGENT_HINT}" if description else AGENT_HINT,
            input_schema=MESSAGE_SCHEMA,
            kind="agent",
            # Reprendre l'enfant plutôt que le relancer : l'appel peut toujours
            # être repris. Ses effets sont ceux des outils de l'enfant, tels que
            # la référence les déclare.
            side_effects=definition.side_effects,
            idempotent=True,
        )

    @property
    def spec(self) -> ToolSpec:
        """Description de l'outil : celle du sous-agent, ``AGENT_HINT`` et le schéma ``message``."""
        return self._spec

    def __repr__(self) -> str:
        return f"AgentTool({self.definition.name!r}, agent {self.definition.agent!r})"

    def available(self, run: RunView) -> bool:
        """Vrai si la profondeur du run appelant est inférieure à ``max_depth``."""
        return run.state.depth < self.definition.max_depth

    def child_run_id(self, call: PendingCall) -> RunId:
        """L'enfant déjà choisi pour cet appel (reprise), sinon un nouvel identifiant."""
        return call.child_run_id or new_run_id()

    def _budget(self, parent: RunState) -> tuple[bool, RunBudget | None]:
        """Part de budget de l'enfant (None sans part), et vrai si le parent n'a plus rien."""
        share = self.definition.budget_share
        if share is None:
            return False, None
        limits = (self.definition.parent_budget or RunBudget()).tightest(parent.budget)
        if not limits.limited:
            return False, None
        given = limits.share(share, parent.spent)
        return given is None, given

    async def run(
        self, arguments: dict[str, JsonValue], context: ToolContext, run: RunView
    ) -> AsyncGenerator[DelegatedPayload | Consumption | ToolOutput | Waiting]:
        """Lance l'enfant s'il n'existe pas encore, puis le fait avancer jusqu'à son arrêt.

        L'enfant écrit dans le journal du parent, sans diffusion en direct.
        S'il n'a pas encore d'événement, il est commencé avec le message, le
        contexte du parent et sa part de budget.

        Args:
            arguments: ``message``, la demande adressée au sous-agent.
            context: Contexte de l'appel ; ``call_id`` désigne l'enfant dans
                ``run.children``.
            run: La vue du run parent.

        Yields:
            ``Waiting`` si l'enfant attend une décision humaine (fonction
            H4, point 17) ; sinon sa consommation, puis le résultat : sa
            réponse finale avec les fichiers produits par ses outils, ou une
            erreur (« Le sous-agent <nom> a échoué (<type>) : … », « … n'a rien
            répondu », « Budget du run atteint : … »).
        """
        name = self.definition.name
        child_id = run.children[context.call_id]
        parent = run.state
        tenant, session = parent.context.tenant_id, parent.session_id
        agent = self._agents(self.definition.agent)
        writer = run.writer or await SessionWriter.open(agent.store, tenant, session)
        # L'enfant écrit dans le journal du parent, et ne diffuse rien en direct.
        ctx = replace(agent, store=writer.store, on_chunk=None)
        if not await ctx.store.read(tenant, session, run_id=child_id):
            exhausted, budget = self._budget(parent)
            if exhausted:
                yield ToolOutput.error(f"Budget du run atteint : le sous-agent {name} n'est pas lancé.")
                return
            await begin_run(
                ctx,
                Message.user(str(arguments["message"])),
                session_id=session,
                context=parent.context,
                run_id=child_id,
                parent=ParentRun(
                    run_id=parent.run_id,
                    root_run_id=parent.root_run_id,
                    call_id=context.call_id,
                    depth=parent.depth,
                    span_id=run.spans.get(context.call_id),
                    judges=parent.judges,
                    budget=budget,
                ),
                writer=writer,
            )
        final = await drive(ctx, child_id, session_id=session, tenant_id=tenant, writer=writer)
        if final.status in _WAITING:
            # L'enfant attend une décision humaine : l'appel n'a pas de
            # résultat, et le parent l'attend avec lui (fonction H4, point 17).
            yield Waiting(call_id=context.call_id, run_id=child_id)
            return
        yield Consumption(usage=final.usage, cost_usd=final.cost_usd)
        yield _output(name, final)


def _output(name: str, final: RunState) -> ToolOutput:
    """Résultat de l'appel : la réponse finale de l'enfant, ou l'erreur qui l'a arrêté."""
    match final.status:
        case RunStatus.COMPLETED if final.output is not None:
            blocks = tuple(_shown(block) for block in final.output.blocks)
            kept: tuple[OutputBlock, ...] = tuple(b for b in blocks if b is not None)
            produced = [a for a in final.artifacts if a.origin == "tool_output"]
            # Les fichiers produits par les outils de l'enfant suivent sa réponse :
            # sans eux, le parent n'apprendrait leur existence que si l'enfant les
            # nommait, et ne pourrait ni les voir ni les livrer.
            shown = {b.uri for b in kept if isinstance(b, ArtifactRefBlock)}
            files = tuple(ArtifactRefBlock(uri=a.uri, media_type=a.media_type, size=a.size, name=a.name) for a in produced if a.uri not in shown)
            output = ToolOutput(
                blocks=(*kept, *files),
                artifacts=tuple(a.uri for a in produced),
            )
            if not output.as_text.strip() and not any(isinstance(b, ArtifactRefBlock) for b in output.blocks):
                return ToolOutput.error(f"Le sous-agent {name} n'a rien répondu.")
            return output
        case RunStatus.FAILED:
            return ToolOutput.error(f"Le sous-agent {name} a échoué ({final.error_type}) : {final.error}")
        case status:
            return ToolOutput.error(f"Le sous-agent {name} s'est arrêté dans l'état {status}.")


def _shown(block: object) -> OutputBlock | None:
    if isinstance(block, TextBlock | JsonBlock | ArtifactRefBlock):
        return block
    return None

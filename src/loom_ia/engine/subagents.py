# SPDX-License-Identifier: Apache-2.0
"""Sous-agents : un agent appelé comme un outil, dans un run enfant (C5, #4).

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
    TextBlock,
    ToolOutput,
    ToolSpec,
    new_run_id,
)
from loom_ia.core.ports import ToolContext
from loom_ia.engine.delegated import Consumption, DelegatedPayload, DelegatedTool, RunView
from loom_ia.engine.loop import ParentRun, RunContext, begin_run, drive
from loom_ia.engine.writer import SessionWriter

# Mention ajoutée à la description d'un sous-agent.
AGENT_HINT: Final = (
    "Sous-agent : il ne reçoit que `message`, sans la conversation ni les résultats "
    "précédents ; mets-y tout ce dont il a besoin."
)

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

# Contexte d'exécution d'un agent de la config, par son nom.
type AgentResolver = Callable[[str], RunContext]


@dataclass(frozen=True, kw_only=True)
class SubAgentDefinition:
    """Un sous-agent tel que le moteur l'appelle."""

    # Nom de l'outil vu par l'orchestrateur.
    name: str
    # Agent de la config qui fait le travail.
    agent: str
    description: str
    # ``max_depth`` de l'agent appelant : profondeur au-delà de laquelle il est masqué.
    max_depth: int = 1
    # Part de ce qui reste au budget du run appelant, donnée à l'enfant (J4).
    budget_share: float | None = None
    # Limites du run de l'agent appelant (sa config) ; s'y ajoute sa propre part reçue.
    parent_budget: RunBudget | None = None


class AgentTool(DelegatedTool):
    """Outil qui confie la demande à un autre agent, dans un run enfant."""

    def __init__(self, definition: SubAgentDefinition, agents: AgentResolver) -> None:
        self.definition = definition
        self._agents = agents
        description = definition.description
        self._spec = ToolSpec(
            name=definition.name,
            description=f"{description}\n\n{AGENT_HINT}" if description else AGENT_HINT,
            input_schema=MESSAGE_SCHEMA,
            kind="agent",
            # Reprendre l'enfant plutôt que le relancer : l'appel peut toujours
            # être repris. Ses effets sont ceux des outils de l'enfant.
            side_effects="irreversible",
            idempotent=True,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def __repr__(self) -> str:
        return f"AgentTool({self.definition.name!r}, agent {self.definition.agent!r})"

    def available(self, run: RunView) -> bool:
        return run.state.depth < self.definition.max_depth

    def child_run_id(self, call: PendingCall) -> RunId:
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
    ) -> AsyncGenerator[DelegatedPayload | Consumption | ToolOutput]:
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
                yield ToolOutput.error(
                    f"Budget du run atteint : le sous-agent {name} n'est pas lancé."
                )
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
        yield Consumption(usage=final.usage, cost_usd=final.cost_usd)
        yield _output(name, final)


def _output(name: str, final: RunState) -> ToolOutput:
    """Résultat de l'appel : la réponse finale de l'enfant, ou l'erreur qui l'a arrêté."""
    match final.status:
        case RunStatus.COMPLETED if final.output is not None:
            blocks = tuple(_shown(block) for block in final.output.blocks)
            kept: tuple[OutputBlock, ...] = tuple(b for b in blocks if b is not None)
            output = ToolOutput(
                blocks=kept,
                artifacts=tuple(a.uri for a in final.artifacts if a.origin == "tool_output"),
            )
            if not output.as_text.strip() and not any(
                isinstance(b, ArtifactRefBlock) for b in kept
            ):
                return ToolOutput.error(f"Le sous-agent {name} n'a rien répondu.")
            return output
        case RunStatus.FAILED:
            return ToolOutput.error(
                f"Le sous-agent {name} a échoué ({final.error_type}) : {final.error}"
            )
        case status:
            return ToolOutput.error(f"Le sous-agent {name} s'est arrêté dans l'état {status}.")


def _shown(block: object) -> OutputBlock | None:
    if isinstance(block, TextBlock | JsonBlock | ArtifactRefBlock):
        return block
    return None

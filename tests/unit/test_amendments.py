# SPDX-License-Identifier: Apache-2.0
"""Mention des appels réécrits avant exécution : approbateur ou politique ``before_tool``.

Constaté au test H4 du 18/10 : l'approbateur avait remplacé le message d'une
relance ; Haiku, lisant l'appel réécrit sans savoir qu'il ne l'avait pas écrit,
s'est excusé d'avoir « inventé » la signature « Denis ».
"""

from pydantic import JsonValue

from loom_ia.adapters.stores import InMemoryEventStore
from loom_ia.core.events import ToolCalled, ToolCompleted
from loom_ia.core.model import (
    CONTINUE,
    Amendment,
    Approved,
    BeforeTool,
    Decision,
    Message,
    ModelRequest,
    ModelSpec,
    PendingApproval,
    Replace,
    RetryPolicy,
    RunState,
    RunStatus,
    ToolOutput,
    ToolResultBlock,
)
from loom_ia.engine import BoundPolicy, Policies, RunContext, ToolExecutor, begin_run, drive
from loom_ia.engine.amendments import amendment_note, note_amendments
from loom_ia.policies import FunctionPolicy, policy
from loom_ia.testing import ScriptedModel, tool_call_message
from loom_ia.tools import ConfiguredTool, tool

SPEC = ModelSpec(id="FAKE", sdk="fake", model="fake-1", retry=RetryPolicy(initial_delay=0))
EXECUTED = "c'est exactement ce qui a été exécuté. Tes arguments d'origine n'ont pas été utilisés."


@tool
def envoyer(client: str, message: str) -> str:
    """Envoie une relance."""
    return f"Relance envoyée à {client} ({len(message)} caractères)"


SENSIBLE = ConfiguredTool(tool=envoyer, spec=envoyer.spec.model_copy(update={"approval": "always"}))
ARGUMENTS: dict[str, JsonValue] = {"client": "Dupont", "message": "Bonjour Madame, Monsieur"}
APPEL: tuple[str, str, dict[str, JsonValue]] = ("c1", "envoyer", ARGUMENTS)


def context(
    store: InMemoryEventStore, model: ScriptedModel, tool_: object, **options: object
) -> RunContext:
    defaults: dict[str, object] = {
        "tools": ToolExecutor([tool_]),  # pyright: ignore[reportArgumentType]
        "system": "Tu relances.",
        "model_spec": SPEC,
        "max_iterations": 3,
        "policies": Policies(()),
    }
    return RunContext(
        agent="demo",
        store=store,
        model=model,
        **(defaults | options),  # pyright: ignore[reportArgumentType]
    )


async def run(ctx: RunContext) -> RunState:
    return await drive(ctx, (await begin_run(ctx, "Relance Dupont.")).run_id)


def result_text(request: ModelRequest) -> str:
    [block] = [b for m in request.messages for b in m.blocks if isinstance(b, ToolResultBlock)]
    return block.output.as_text


# --- Texte de la mention -----------------------------------------------------------------


def test_the_note_names_who_changed_which_fields_and_why() -> None:
    by_denis = Amendment(
        call_id="c1", origin="approval", fields=("message",), by="denis", reason="trop formel"
    )
    assert amendment_note(by_denis) == (
        "[Note du système : denis a remplacé les arguments de ton appel avant exécution "
        "(champs : message ; motif : trop formel). L'appel affiché ci-dessus porte déjà "
        f"les arguments de denis : {EXECUTED}]"
    )
    anonymous = Amendment(call_id="c1", origin="approval", fields=("client", "message"))
    assert amendment_note(anonymous) == (
        "[Note du système : les arguments de ton appel ont été remplacés à l'approbation "
        "avant exécution (champs : client, message). L'appel affiché ci-dessus porte déjà "
        f"les arguments corrigés : {EXECUTED}]"
    )
    again = EXECUTED.removesuffix(".") + ", et un nouvel appel avec eux serait remplacé de même."
    by_policy = Amendment(call_id="c1", origin="policy", fields=("remise",))
    assert amendment_note(by_policy) == (
        "[Note du système : une politique a remplacé les arguments de ton appel avant "
        "exécution (champs : remise). L'appel affiché ci-dessus porte déjà les arguments "
        f"remplacés : {again}]"
    )
    # Test E7 du 19/10 : la politique nommée, avec son motif.
    named = Amendment(
        call_id="c1", origin="policy", fields=("taux_tva",), by="plafond_ttc", reason="abrogé"
    )
    assert amendment_note(named) == (
        "[Note du système : la politique plafond_ttc a remplacé les arguments de ton appel "
        "avant exécution (champs : taux_tva ; motif : abrogé). L'appel affiché ci-dessus porte "
        f"déjà les arguments remplacés : {again}]"
    )
    two = Amendment(call_id="c1", origin="policy", fields=("a",), by="p1, p2", reason="x ; y")
    assert amendment_note(two).startswith(
        "[Note du système : les politiques p1, p2 ont remplacé les arguments de ton appel "
        "avant exécution (champs : a ; motif : x ; y)."
    )


def test_only_the_amended_results_get_a_note() -> None:
    plain = Message(
        role="tool",
        blocks=(ToolResultBlock(call_id="c0", output=ToolOutput.text("lu")),),
    )
    amended = Message(
        role="tool",
        blocks=(ToolResultBlock(call_id="c1", output=ToolOutput.text("envoyé")),),
    )
    note = Amendment(call_id="c1", origin="policy", fields=("message",))
    first, second = note_amendments((plain, amended), (note,))
    assert first is plain
    block = second.blocks[0]
    assert isinstance(block, ToolResultBlock)
    assert block.output.as_text == f"{amendment_note(note)}\nenvoyé"
    assert note_amendments((plain,), ()) == (plain,)


# --- Dans la boucle ----------------------------------------------------------------------


async def test_an_approver_correction_is_announced_to_the_model() -> None:
    async def approver(pending: PendingApproval) -> Approved:
        corrige: dict[str, JsonValue] = {**pending.arguments, "message": "Bonjour, Denis"}
        return Approved(by="denis", reason="trop formel", arguments=corrige)

    store = InMemoryEventStore()
    model = ScriptedModel(tool_call_message(APPEL), Message.assistant("Envoyé."))
    state = await run(context(store, model, SENSIBLE, approver=approver))

    assert state.status is RunStatus.COMPLETED
    assert state.amendments == (
        Amendment(
            call_id="c1", origin="approval", fields=("message",), by="denis", reason="trop formel"
        ),
    )
    assert result_text(model.requests[1]) == (
        "[Note du système : denis a remplacé les arguments de ton appel avant exécution "
        "(champs : message ; motif : trop formel). L'appel affiché ci-dessus porte déjà "
        f"les arguments de denis : {EXECUTED}]\n"
        "Relance envoyée à Dupont (14 caractères)"
    )
    # Le journal ne bouge pas : le résultat y reste tel que l'outil l'a rendu.
    events = await store.read(state.context.tenant_id, state.session_id)
    [done] = [e.payload for e in events if isinstance(e.payload, ToolCompleted)]
    assert done.output.as_text == "Relance envoyée à Dupont (14 caractères)"
    [called] = [e.payload for e in events if isinstance(e.payload, ToolCalled)]
    assert called.arguments == APPEL[2]


async def test_an_approval_without_change_adds_no_note() -> None:
    async def approver(pending: PendingApproval) -> Approved:
        return Approved(by="denis", arguments=dict(pending.arguments))

    model = ScriptedModel(tool_call_message(APPEL), Message.assistant("Envoyé."))
    state = await run(context(InMemoryEventStore(), model, SENSIBLE, approver=approver))

    assert state.amendments == ()
    assert result_text(model.requests[1]) == "Relance envoyée à Dupont (24 caractères)"


async def test_a_policy_replacement_is_announced_without_its_values() -> None:
    @policy(points=["before_tool"], decisions=["replace"])
    def signe(subject: BeforeTool) -> Decision:
        if subject.spec.name == "envoyer":
            return Replace({**subject.arguments, "message": "Bonjour, Denis"}, reason="signé")
        return CONTINUE

    model = ScriptedModel(tool_call_message(APPEL), Message.assistant("Envoyé."))
    ctx = context(InMemoryEventStore(), model, envoyer, policies=_bind(signe))
    state = await run(ctx)

    assert state.amendments == (
        Amendment(call_id="c1", origin="policy", fields=("message",), by="signe", reason="signé"),
    )
    text = result_text(model.requests[1])
    # La politique et son motif sont dits (test E7 du 19/10), jamais l'ancienne valeur.
    assert text == (
        "[Note du système : la politique signe a remplacé les arguments de ton appel avant "
        "exécution (champs : message ; motif : signé). L'appel affiché ci-dessus porte déjà "
        "les arguments remplacés : c'est exactement ce qui a été exécuté. Tes arguments "
        "d'origine n'ont pas été utilisés, et un nouvel appel avec eux serait remplacé de "
        "même.]\nRelance envoyée à Dupont (14 caractères)"
    )
    assert "Madame" not in text


def _bind(*policies: FunctionPolicy) -> Policies:
    return Policies(
        BoundPolicy(policy=p, name=p.name, points=p.points, max_attempts=1) for p in policies
    )

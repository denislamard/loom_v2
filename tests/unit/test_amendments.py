# SPDX-License-Identifier: Apache-2.0
"""Mention des appels réécrits avant exécution : approbateur ou politique ``before_tool``.

Constaté au test H4 du 18/10 : l'approbateur avait remplacé le message d'une
relance ; Haiku, lisant l'appel réécrit sans savoir qu'il ne l'avait pas écrit,
s'est excusé d'avoir « inventé » la signature « Denis ». Au test H4 du 20/10, la
note placée dans le résultat de l'outil n'a pas été crue (« une modification
faite par « denis » que je ne peux pas vérifier ») : elle suit désormais les
résultats du tour, dans un message à elle.
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
    TextBlock,
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
DECIDED = (
    " C'est une décision de denis, qui valide les appels de cette application : ne propose "
    "pas de revenir à tes arguments d'origine ; si elle diffère de ce que l'utilisateur "
    "avait demandé, dis-lui que denis a corrigé l'appel."
)


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


def note_after_results(request: ModelRequest) -> str | None:
    """Texte du message qui suit les résultats d'outils, s'il y en a un."""
    roles = [m.role for m in request.messages]
    after = roles.index("tool") + 1
    following = request.messages[after] if after < len(roles) else None
    if following is None or following.role != "user":
        return None
    text = following.text
    return text if text.startswith("[Note du système") else None


# --- Texte de la mention -----------------------------------------------------------------


def test_the_note_names_who_changed_which_fields_and_why() -> None:
    by_denis = Amendment(
        call_id="c1", origin="approval", fields=("message",), by="denis", reason="trop formel"
    )
    assert amendment_note(by_denis) == (
        "[Note du système : denis a remplacé les arguments de ton appel avant exécution "
        "(champs : message ; motif : trop formel). L'appel affiché plus haut porte déjà "
        f"les arguments de denis : {EXECUTED}{DECIDED}]"
    )
    assert amendment_note(by_denis, "envoyer").startswith(
        "[Note du système : denis a remplacé les arguments de ton appel à envoyer avant "
    )
    anonymous = Amendment(call_id="c1", origin="approval", fields=("client", "message"))
    assert amendment_note(anonymous) == (
        "[Note du système : les arguments de ton appel ont été remplacés à l'approbation "
        "avant exécution (champs : client, message). L'appel affiché plus haut porte déjà "
        f"les arguments corrigés : {EXECUTED} C'est une décision de l'approbateur, qui "
        "valide les appels de cette application : ne propose pas de revenir à tes arguments "
        "d'origine ; si elle diffère de ce que l'utilisateur avait demandé, dis-lui que "
        "l'appel a été corrigé à l'approbation.]"
    )
    again = EXECUTED.removesuffix(".") + ", et un nouvel appel avec eux serait remplacé de même."
    by_policy = Amendment(call_id="c1", origin="policy", fields=("remise",))
    assert amendment_note(by_policy) == (
        "[Note du système : une politique a remplacé les arguments de ton appel avant "
        "exécution (champs : remise). L'appel affiché plus haut porte déjà les arguments "
        f"remplacés : {again}]"
    )
    # Test E7 du 19/10 : la politique nommée, avec son motif.
    named = Amendment(
        call_id="c1", origin="policy", fields=("taux_tva",), by="plafond_ttc", reason="abrogé"
    )
    assert amendment_note(named) == (
        "[Note du système : la politique plafond_ttc a remplacé les arguments de ton appel "
        "avant exécution (champs : taux_tva ; motif : abrogé). L'appel affiché plus haut porte "
        f"déjà les arguments remplacés : {again}]"
    )
    two = Amendment(call_id="c1", origin="policy", fields=("a",), by="p1, p2", reason="x ; y")
    assert amendment_note(two).startswith(
        "[Note du système : les politiques p1, p2 ont remplacé les arguments de ton appel "
        "avant exécution (champs : a ; motif : x ; y)."
    )


def test_the_note_follows_the_results_of_its_turn() -> None:
    asked = tool_call_message(("c0", "lire", {}), ("c1", "envoyer", ARGUMENTS))
    plain = Message(
        role="tool",
        blocks=(ToolResultBlock(call_id="c0", output=ToolOutput.text("lu")),),
    )
    amended = Message(
        role="tool",
        blocks=(ToolResultBlock(call_id="c1", output=ToolOutput.text("envoyé")),),
    )
    later = Message.assistant("Envoyé.")
    by_policy = Amendment(call_id="c1", origin="policy", fields=("message",), by="signe")
    by_denis = Amendment(call_id="c1", origin="approval", fields=("client",), by="denis")

    noted = note_amendments((asked, plain, amended, later), (by_policy, by_denis))

    # Les résultats ne sont pas touchés ; la note vient après le dernier du tour.
    assert noted[:3] == (asked, plain, amended)
    assert noted[3] == Message(
        role="user",
        blocks=(
            TextBlock(
                text=f"{amendment_note(by_policy, 'envoyer')}\n"
                f"{amendment_note(by_denis, 'envoyer')}"
            ),
        ),
    )
    assert noted[4] is later
    assert note_amendments((plain,), ()) == (plain,)
    assert note_amendments((asked, plain), (by_policy,)) == (asked, plain)


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
    assert result_text(model.requests[1]) == "Relance envoyée à Dupont (14 caractères)"
    assert note_after_results(model.requests[1]) == (
        "[Note du système : denis a remplacé les arguments de ton appel à envoyer avant "
        "exécution (champs : message ; motif : trop formel). L'appel affiché plus haut porte "
        f"déjà les arguments de denis : {EXECUTED}{DECIDED}]"
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
    assert note_after_results(model.requests[1]) is None


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
    assert result_text(model.requests[1]) == "Relance envoyée à Dupont (14 caractères)"
    text = note_after_results(model.requests[1])
    # La politique et son motif sont dits (test E7 du 19/10), jamais l'ancienne valeur.
    assert text == (
        "[Note du système : la politique signe a remplacé les arguments de ton appel à "
        "envoyer avant exécution (champs : message ; motif : signé). L'appel affiché plus "
        "haut porte déjà les arguments remplacés : c'est exactement ce qui a été exécuté. "
        "Tes arguments d'origine n'ont pas été utilisés, et un nouvel appel avec eux serait "
        "remplacé de même.]"
    )
    assert "Madame" not in text


def _bind(*policies: FunctionPolicy) -> Policies:
    return Policies(
        BoundPolicy(policy=p, name=p.name, points=p.points, max_attempts=1) for p in policies
    )

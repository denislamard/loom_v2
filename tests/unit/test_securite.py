# SPDX-License-Identifier: Apache-2.0
"""Clés d'API complètes et masquage du contenu (J5.2a, N3, #39, §14.2).

Une clé dit trois choses : au nom de **qui** elle agit (5.1a), **ce qu'elle
peut faire** (ses portées), et **jusqu'à quand**. Ce que ces essais cherchent,
c'est la fuite par la lecture : une clé de supervision doit voir passer les
runs, leurs coûts et leurs verdicts sans lire la correspondance d'un artisan
avec ses clients.

Le journal, lui, garde tout : le masquage est une affaire d'accès, pas de
stockage — un run ne se rejoue pas sans ce que le modèle a lu et répondu.
"""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ANSWER, QUESTION, ConfigFactory

from loom_ia.access import JudgeVerdict, Loom, RunResult
from loom_ia.access.cli import main
from loom_ia.config import ConfigError, load_config
from loom_ia.config.keys import fingerprint, new_api_key
from loom_ia.config.models import ApiKey
from loom_ia.core.events import (
    DURABLE_PAYLOADS,
    DurablePayload,
    Event,
    JudgeEvaluated,
    Payload,
    RunClaimed,
    RunCompleted,
    RunStarted,
    ToolCalled,
    UserMessage,
    redacted,
)
from loom_ia.core.model import (
    ArtifactRecord,
    CallerContext,
    CriterionScore,
    Message,
    RunId,
    RunStatus,
    SessionId,
)
from loom_ia.testing import RunJournal

JOURNAL: dict[str, Any] = {"events": {"backend": "jsonl", "path": "data"}}


def empreinte(cle: str) -> str:
    return fingerprint(cle)


def wrote(payload: DurablePayload) -> Event:
    """Un événement écrit, pour n'éprouver que le masquage de sa charge."""
    return RunJournal().scope.draft(payload).to_event(1)


# --- Chaque charge déclare ce qu'elle porte de contenu ------------------------


def test_every_payload_declares_its_content_fields() -> None:
    """La garantie de la phase : on ne peut pas ajouter du contenu sans le dire.

    Déclarer ``content_fields`` **dans la classe**, même vide, est ce qui
    empêche une charge nouvelle de porter du contenu que le masquage ignore.
    """
    muettes = [p.__name__ for p in DURABLE_PAYLOADS if "content_fields" not in vars(p)]
    assert muettes == []


def test_the_declared_paths_all_exist() -> None:
    """Un chemin déclaré désigne un champ réel : une faute de frappe ne masque rien."""
    for payload in DURABLE_PAYLOADS:
        for path in payload.content_fields:
            racine = path.partition(".")[0].removesuffix("[]")
            assert racine in payload.model_fields, f"{payload.__name__} : {path!r}"


def test_the_base_payload_declares_nothing() -> None:
    assert Payload.content_fields == ()


# --- Ce que le masquage retire, et ce qu'il laisse ----------------------------


def test_a_masked_event_keeps_its_envelope_and_loses_its_message() -> None:
    event = wrote(UserMessage(message=Message.user("Relance Mme Martin au 06 12 34 56 78")))
    masque = redacted(event)
    assert masque["seq"] == 1
    assert masque["type"] == "message.user"
    assert masque["tenant_id"] == event.tenant_id
    charge = masque["payload"]
    assert isinstance(charge, dict)
    assert "message" not in charge
    assert charge["redacted"] == ["message"]
    assert charge["kind"] == "request"
    assert "06 12 34 56 78" not in json.dumps(masque, ensure_ascii=False)


def test_masking_does_not_touch_the_original_event() -> None:
    event = wrote(UserMessage(message=Message.user("Secret")))
    redacted(event)
    assert event.payload.message.text == "Secret"  # type: ignore[union-attr]


def test_a_payload_without_content_is_untouched() -> None:
    masque = redacted(wrote(RunClaimed(worker_id="w1", lease_until=datetime.now(UTC))))
    charge = masque["payload"]
    assert isinstance(charge, dict)
    assert "redacted" not in charge and charge["worker_id"] == "w1"


def test_an_absent_field_is_not_named_as_removed() -> None:
    # `output` et `data` sont vides : il n'y a rien à retirer, donc rien à dire.
    masque = redacted(wrote(RunCompleted(iterations=2)))
    charge = masque["payload"]
    assert isinstance(charge, dict)
    assert "redacted" not in charge


def test_arguments_of_a_tool_call_are_removed_but_not_its_name() -> None:
    call = ToolCalled(
        call_id="c1",
        tool_name="envoyer_email",
        tool_kind="python",
        arguments={"destinataire": "mme.martin@example.com"},
    )
    charge = redacted(wrote(call))["payload"]
    assert isinstance(charge, dict)
    assert charge["tool_name"] == "envoyer_email"
    assert "arguments" not in charge and charge["redacted"] == ["arguments"]


def test_a_nested_path_is_followed(demo: ConfigFactory) -> None:
    # `context.metadata` porte ce que l'appelant y met ; `user_id` est une
    # identité, donc de l'audit, et il reste.
    context = CallerContext(user_id="denis", metadata={"dossier": "D-2026-042"})
    charge = redacted(wrote(RunStarted(context=context)))["payload"]
    assert isinstance(charge, dict)
    vu = charge["context"]
    assert isinstance(vu, dict)
    assert vu["user_id"] == "denis"
    assert "metadata" not in vu
    assert charge["redacted"] == ["context.metadata"]


def test_every_criterion_loses_its_reason_and_keeps_its_score() -> None:
    """Chaque élément de la liste est traité : pas d'arrêt au premier."""
    verdict = JudgeEvaluated(
        judge="rediger_relance",
        target="role:rediger_relance",
        model_id="fake-judge",
        criteria=(
            CriterionScore(
                name="fidele",
                score=1.0,
                min_score=0.8,
                blocking=True,
                reason="Montant repris du devis.",
            ),
            CriterionScore(
                name="ton", score=0.9, min_score=0.6, blocking=False, reason="Ton cordial."
            ),
        ),
        passed=True,
        blocked=False,
    )
    charge = redacted(wrote(verdict))["payload"]
    assert isinstance(charge, dict)
    notes = charge["criteria"]
    assert isinstance(notes, list)
    assert [c["name"] for c in notes] == ["fidele", "ton"]  # type: ignore[index]
    assert [c["score"] for c in notes] == [1.0, 0.9]  # type: ignore[index]
    assert all("reason" not in c for c in notes)  # type: ignore[operator]
    assert charge["passed"] is True


# --- Une clé a une fin de validité --------------------------------------------


def test_a_key_without_a_date_never_expires() -> None:
    key = ApiKey(id="app", hash=empreinte(new_api_key()))
    assert key.expires is None and not key.expired()


def test_a_key_expires_at_its_date() -> None:
    hier = datetime.now(UTC) - timedelta(days=1)
    demain = datetime.now(UTC) + timedelta(days=1)
    assert ApiKey(id="a", hash=empreinte(new_api_key()), expires=hier).expired()
    assert not ApiKey(id="b", hash=empreinte(new_api_key()), expires=demain).expired()


def test_an_expired_key_does_not_stop_the_config_from_loading(demo: ConfigFactory) -> None:
    # Un service qui redémarre la nuit ne doit pas tomber pour une clé périmée.
    cle = new_api_key()
    security = {
        "api_keys": [{"id": "vieille", "hash": empreinte(cle), "expires": "2020-01-01T00:00:00Z"}]
    }
    config = load_config(demo(security=security))
    assert config.security.api_keys[0].expired()


def test_an_expiry_without_a_timezone_is_refused(demo: ConfigFactory) -> None:
    security = {
        "api_keys": [{"id": "app", "hash": empreinte(new_api_key()), "expires": "2027-01-01"}]
    }
    with pytest.raises(ConfigError):
        load_config(demo(security=security))


# --- REST : ce que chaque clé obtient -----------------------------------------


def cles(**kinds: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Déclare une clé par entrée et rend la config de sécurité et les jetons."""
    jetons = {name: new_api_key() for name in kinds}
    keys = [{"id": name, "hash": empreinte(jetons[name]), **spec} for name, spec in kinds.items()]
    return {"api_keys": keys}, jetons


def porteur(jeton: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jeton}"}


async def test_an_expired_key_is_recognised_then_refused(demo: ConfigFactory) -> None:
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    pytest.importorskip("httpx2", reason="client HTTP de test absent")
    import httpx2

    from loom_ia.access.http import create_app

    security, jetons = cles(
        vivante={"scopes": ["run", "read"]},
        morte={"scopes": ["run", "read"], "expires": "2020-01-01T00:00:00Z"},
    )
    async with Loom.from_config(demo(security=security)) as loom:
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            vivante = await http.get("/v1/agents", headers=porteur(jetons["vivante"]))
            assert vivante.status_code == 200
            morte = await http.get("/v1/agents", headers=porteur(jetons["morte"]))
    assert morte.status_code == 401
    # Reconnue, puis refusée : le message dit quoi corriger.
    assert "expirée" in morte.json()["detail"] and "morte" in morte.json()["detail"]
    assert morte.headers["www-authenticate"] == "Bearer"


async def test_a_read_without_read_content_sees_the_run_without_its_text(
    demo: ConfigFactory,
) -> None:
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    pytest.importorskip("httpx2", reason="client HTTP de test absent")
    import httpx2

    from loom_ia.access.http import create_app

    security, jetons = cles(
        complete={"scopes": ["run", "read", "read_content"]},
        supervision={"scopes": ["run", "read"]},
    )
    path = demo(storage=JOURNAL, security=security)
    async with Loom.from_config(path) as loom:
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            # Ce qu'une clé lance, elle le reçoit : la réponse d'un POST n'est
            # jamais masquée, même sans `read_content`.
            lance = await http.post(
                "/v1/agents/demo/runs",
                json={"message": QUESTION},
                headers=porteur(jetons["supervision"]),
            )
            assert lance.status_code == 201 and lance.json()["text"] == ANSWER
            run_id = lance.json()["run_id"]

            relu = await http.get(f"/v1/runs/{run_id}", headers=porteur(jetons["supervision"]))
            complet = await http.get(f"/v1/runs/{run_id}", headers=porteur(jetons["complete"]))

    masque, entier = relu.json(), complet.json()
    assert entier["text"] == ANSWER and entier["output"] is not None
    # La relecture, elle, est masquée : ce qui reste est ce qu'une supervision
    # a besoin de voir.
    assert masque["text"] == "" and masque["output"] is None and masque["data"] is None
    assert masque["status"] == "completed" and masque["agent"] == "demo"
    assert masque["iterations"] == entier["iterations"]
    assert masque["cost_usd"] == entier["cost_usd"]
    assert masque["report"] == entier["report"]


async def test_the_export_of_a_session_is_masked(demo: ConfigFactory) -> None:
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    pytest.importorskip("httpx2", reason="client HTTP de test absent")
    import httpx2

    from loom_ia.access.http import create_app

    security, jetons = cles(
        complete={"scopes": ["run", "read", "read_content"]},
        supervision={"scopes": ["run", "read"]},
    )
    path = demo(storage=JOURNAL, security=security)
    session = SessionId("atelier")
    async with Loom.from_config(path) as loom:
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            await http.post(
                "/v1/agents/demo/runs",
                json={"message": QUESTION, "session_id": session},
                headers=porteur(jetons["complete"]),
            )
            masque = await http.get(
                f"/v1/sessions/{session}/events", headers=porteur(jetons["supervision"])
            )
            entier = await http.get(
                f"/v1/sessions/{session}/events", headers=porteur(jetons["complete"])
            )

    assert masque.status_code == 200
    lignes = [json.loads(line) for line in masque.text.splitlines()]
    complets = [json.loads(ligne) for ligne in entier.text.splitlines()]
    assert [e["type"] for e in lignes] == [e["type"] for e in complets]
    # La question et la réponse ne sont nulle part dans l'export masqué.
    assert QUESTION not in masque.text and ANSWER not in masque.text
    assert QUESTION in entier.text and ANSWER in entier.text
    # Les appels d'outil gardent leur nom, perdent leurs arguments.
    appels = [e for e in lignes if e["type"] == "tool.called"]
    assert appels and all(a["payload"]["tool_name"] == "calculer" for a in appels)
    assert all("arguments" not in a["payload"] for a in appels)
    assert all(a["payload"]["redacted"] == ["arguments"] for a in appels)
    # Les coûts, eux, restent lisibles.
    appels_modele = [e for e in lignes if e["type"] == "model.responded"]
    assert appels_modele and all("cost_usd" in e["payload"] for e in appels_modele)


async def test_the_sse_stream_is_masked(demo: ConfigFactory) -> None:
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    pytest.importorskip("httpx2", reason="client HTTP de test absent")
    import httpx2

    from loom_ia.access.http import create_app

    security, jetons = cles(supervision={"scopes": ["run", "read"]})
    path = demo(storage=JOURNAL, security=security)
    async with Loom.from_config(path) as loom:
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            lance = await http.post(
                "/v1/agents/demo/runs",
                json={"message": QUESTION},
                headers=porteur(jetons["supervision"]),
            )
            run_id = lance.json()["run_id"]
            async with http.stream(
                "GET",
                f"/v1/runs/{run_id}/events",
                headers=porteur(jetons["supervision"]),
                timeout=5,
            ) as flux:
                assert flux.status_code == 200
                corps = "".join([bloc async for bloc in flux.aiter_text()])
    assert "message.user" in corps and "run.completed" in corps
    assert QUESTION not in corps and ANSWER not in corps
    assert '"redacted"' in corps


async def test_pending_approvals_lose_their_arguments(atelier: ConfigFactory) -> None:
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    pytest.importorskip("httpx2", reason="client HTTP de test absent")
    import httpx2

    from loom_ia.access.http import create_app

    security, jetons = cles(
        complete={"scopes": ["run", "read", "read_content", "approve"]},
        aveugle={"scopes": ["run", "read", "approve"]},
    )
    session = SessionId("atelier")
    async with Loom.from_config(atelier(security=security)) as loom:
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:
            lance = await http.post(
                "/v1/agents/demo/runs",
                json={"message": "Relance Mme Martin", "session_id": session},
                headers=porteur(jetons["complete"]),
            )
            assert lance.json()["status"] == "paused"
            vue = await http.get(f"/v1/sessions/{session}", headers=porteur(jetons["aveugle"]))
            entiere = await http.get(f"/v1/sessions/{session}", headers=porteur(jetons["complete"]))

    attend = vue.json()["pending_approvals"]
    tout = entiere.json()["pending_approvals"]
    assert [a["tool_name"] for a in attend] == ["envoyer_email"]
    # Approuver demande de lire : sans `read_content`, on tranche à l'aveugle.
    assert attend[0]["arguments"] == {} and attend[0]["reason"] == ""
    assert tout[0]["arguments"] == {"destinataire": "mme.martin@example.com"}


# --- Le résultat d'un run, masqué au niveau du modèle -------------------------


def test_a_masked_result_keeps_its_scores_and_loses_its_prose() -> None:
    verdict = JudgeVerdict(
        run_id=RunId("r1"),
        agent="demo",
        judge="rediger_relance",
        target="role:rediger_relance",
        model_id="fake-judge",
        passed=False,
        blocked=True,
        criteria=(
            CriterionScore(
                name="fidele",
                score=0.2,
                min_score=0.8,
                blocking=True,
                reason="Réduction de 10 % absente du devis D-2026-042.",
            ),
        ),
    )
    result = RunResult(
        run_id=RunId("r1"),
        session_id=SessionId("s1"),
        agent="demo",
        status=RunStatus.FAILED,
        text="Bonjour Madame Martin…",
        error_type="guard.judge",
        error="Réduction inventée",
        data={"objet": "Votre devis"},
        artifacts=(
            ArtifactRecord(
                uri="artifact://default/s1/abc.png",
                media_type="image/png",
                size=12,
                name="devis-martin.png",
                origin="tool_output",
            ),
        ),
        verdicts=(verdict,),
    )
    masque = result.masked()
    # Ce qui reste : de quoi superviser.
    assert masque.status is RunStatus.FAILED
    assert masque.error_type == "guard.judge"
    assert masque.verdicts[0].blocked and masque.verdicts[0].criteria[0].score == 0.2
    # Ce qui part : la correspondance, et jusqu'au nom du fichier.
    assert masque.text == "" and masque.data is None and masque.error is None
    assert masque.verdicts[0].criteria[0].reason == ""
    assert masque.artifacts[0].name is None
    assert masque.artifacts[0].size == 12
    # L'original n'a pas bougé.
    assert result.text.startswith("Bonjour") and result.verdicts[0].criteria[0].reason


# --- Ligne de commande --------------------------------------------------------


def test_keys_create_prints_the_whole_block(capsys: pytest.CaptureFixture[str]) -> None:
    arguments = [
        "keys",
        "create",
        "app-dupont",
        "--tenant",
        "dupont-plomberie",
        "--scope",
        "run",
        "--scope",
        "read",
        "--agent",
        "relance",
        "--rate-limit",
        "60",
        "--expires",
        "90j",
    ]
    assert main(arguments) == 0
    out = capsys.readouterr().out
    assert "Clé        : lk_" in out
    assert "      tenant: dupont-plomberie" in out
    assert "      scopes: [run, read]" in out
    assert "      agents: [relance]" in out
    assert "      rate_limit: {per_minute: 60}" in out
    ligne = next(ligne for ligne in out.splitlines() if "expires:" in ligne)
    quand = datetime.fromisoformat(ligne.split("expires:")[1].strip().replace("Z", "+00:00"))
    assert timedelta(days=89) < quand - datetime.now(UTC) <= timedelta(days=90)


def test_keys_create_accepts_a_date_and_refuses_the_rest(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["keys", "create", "app", "--expires", "2027-01-01"]) == 0
    assert "expires: 2027-01-01T00:00:00Z" in capsys.readouterr().out
    assert main(["keys", "create", "app", "--expires", "bientôt"]) == 2
    assert "--expires" in capsys.readouterr().err


def test_keys_create_warns_when_approve_cannot_read(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["keys", "create", "app", "--scope", "run", "--scope", "approve"]) == 0
    assert "approuver sans pouvoir lire" in capsys.readouterr().err


def test_validate_shows_each_key_and_flags_an_expired_one(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    bientot = datetime.now(UTC) + timedelta(days=3)
    security = {
        "api_keys": [
            {
                "id": "app",
                "hash": empreinte(new_api_key()),
                "tenant": "dupont-plomberie",
                "scopes": ["run", "read", "read_content"],
                "agents": ["demo"],
                "rate_limit": {"per_minute": 60},
            },
            {
                "id": "vieille",
                "hash": empreinte(new_api_key()),
                "expires": "2020-01-01T00:00:00Z",
            },
            {
                "id": "courte",
                "hash": empreinte(new_api_key()),
                "expires": bientot.isoformat(),
            },
            {"id": "aveugle", "hash": empreinte(new_api_key()), "scopes": ["read", "approve"]},
        ]
    }
    config = demo(tenants=[{"id": "dupont-plomberie"}, {"id": "default"}], security=security)
    assert main(["--config", str(config), "validate"]) == 0
    out = capsys.readouterr().out
    assert "Clés d'API : app, vieille, courte, aveugle" in out
    assert "app : client dupont-plomberie, portées run, read, read_content" in out
    assert "agents demo" in out and "débit 60/min" in out
    assert "vieille : client default, portées run, read, EXPIRÉE" in out
    assert "courte : client default, portées run, read, expire dans 2 j" in out
    assert "aveugle peut approuver sans lire : ajouter 'read_content'" in out

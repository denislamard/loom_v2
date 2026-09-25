# SPDX-License-Identifier: Apache-2.0
"""Rétention : effacer les sessions dormantes, et rien d'autre (#30, J5.5c).

Une session est choisie sur sa **dernière écriture**, et sur rien d'autre :
aucun contenu n'est lu, donc un journal scellé dont la clé a disparu s'efface
aussi — c'est là que la place serait perdue pour de bon. Conséquence assumée,
vérifiée ici : une session qui portait un run inachevé part comme les autres
si elle s'est taue plus longtemps que la borne.

Rien ne s'efface tout seul : l'essai à blanc est le défaut, et `loom retention`
ne supprime qu'avec `--yes`.
"""

import json
from base64 import b64encode
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from conftest import QUESTION, ConfigFactory

from loom_ia.access import Loom
from loom_ia.access.cli import main
from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.core.model import DEFAULT_TENANT, Message, SessionId, TenantId, ToolOutput
from loom_ia.core.ports import MissingKey
from loom_ia.testing import RunJournal, tool_call_message

MARTIN = TenantId("martin-chauffage")
DUPONT = TenantId("dupont-plomberie")
VIEILLE = SessionId("vieille")
RECENTE = SessionId("recente")
CLE_ENV = "LOOM_JOURNAL_KEY"
CLE = b64encode(b"R" * 32).decode()


def journal(tmp_path: Path) -> dict[str, Any]:
    return {"events": {"backend": "jsonl", "path": str(tmp_path / "events")}}


def deux_clients() -> list[dict[str, Any]]:
    return [{"id": str(MARTIN)}, {"id": str(DUPONT)}]


async def ecrit(
    store: JsonlEventStore,
    session: SessionId,
    *,
    tenant: TenantId = DEFAULT_TENANT,
    inachevee: bool = False,
) -> int:
    """Écrit un run dans une session ; ``inachevee`` s'arrête avant sa fin."""
    scribe = RunJournal(session_id=session, tenant_id=tenant)
    scribe.start("Combien font 2 + 2 ?")
    if not inachevee:
        scribe.model_turn(tool_call_message(("c1", "calculer", {"expr": "2+2"})))
        scribe.tool_results({"c1": ToolOutput.text("4")})
        scribe.model_turn(Message.assistant("4"))
        scribe.complete()
    drafts = scribe.take()
    await store.append(drafts, expected_seq=0)
    return len(drafts)


def dans(jours: int) -> datetime:
    """Un moment futur : plus simple et plus juste qu'une horloge truquée."""
    return datetime.now(UTC) + timedelta(days=jours)


# --- La borne ------------------------------------------------------------------


async def test_without_a_bound_nothing_is_swept(demo: ConfigFactory, tmp_path: Path) -> None:
    config = load_config(demo(storage=journal(tmp_path)))
    store = JsonlEventStore(tmp_path / "events")
    await ecrit(store, VIEILLE)
    await store.aclose()
    async with Loom(config) as loom:
        # Même dans mille ans : sans règle, rien ne s'efface, et le rapport le
        # dit au lieu de laisser croire qu'il n'y avait rien à faire.
        report = await loom.apply_retention(now=dans(1000), dry_run=False)
    assert report.swept == ()
    assert report.days == {DEFAULT_TENANT: None}
    assert report.scanned == 0
    assert (tmp_path / "events" / DEFAULT_TENANT / f"{VIEILLE}.jsonl").exists()


async def test_the_bound_is_read_on_the_last_write(demo: ConfigFactory, tmp_path: Path) -> None:
    config = load_config(demo(storage={**journal(tmp_path), "retention": {"events_days": 30}}))
    store = JsonlEventStore(tmp_path / "events")
    longueur = await ecrit(store, VIEILLE)
    await ecrit(store, RECENTE)
    await store.aclose()
    async with Loom(config) as loom:
        # 40 jours plus tard, les deux sessions ont dépassé la borne ; 10 jours
        # plus tard, aucune. C'est la date qui décide, pas le contenu.
        dans_dix = await loom.apply_retention(now=dans(10))
        dans_quarante = await loom.apply_retention(now=dans(40))
    assert dans_dix.swept == ()
    assert (dans_dix.scanned, dans_dix.kept) == (2, 2)
    assert {session.session_id for session in dans_quarante.swept} == {VIEILLE, RECENTE}
    assert dans_quarante.kept == 0
    assert [session.events for session in dans_quarante.swept] == [longueur, longueur]


async def test_a_dry_run_says_what_would_go_and_touches_nothing(
    demo: ConfigFactory, tmp_path: Path
) -> None:
    config = load_config(demo(storage={**journal(tmp_path), "retention": {"events_days": 1}}))
    store = JsonlEventStore(tmp_path / "events")
    longueur = await ecrit(store, VIEILLE)
    await store.aclose()
    async with Loom(config) as loom:
        report = await loom.apply_retention(now=dans(2))
        assert report.dry_run
        # Le nombre d'événements est la longueur du journal, que la marque
        # donne ; les fichiers et les clés ne se comptent qu'en supprimant, donc
        # ils restent inconnus plutôt que faussement nuls.
        assert [(s.events, s.artifacts, s.keys) for s in report.swept] == [(longueur, None, None)]
        assert report.events == longueur
        assert len(await loom.sessions()) == 1
    assert (tmp_path / "events" / DEFAULT_TENANT / f"{VIEILLE}.jsonl").exists()


async def test_a_real_sweep_removes_the_journal_and_counts_what_went(
    demo: ConfigFactory, tmp_path: Path
) -> None:
    config = load_config(demo(storage={**journal(tmp_path), "retention": {"events_days": 1}}))
    store = JsonlEventStore(tmp_path / "events")
    longueur = await ecrit(store, VIEILLE)
    await store.aclose()
    async with Loom(config) as loom:
        report = await loom.apply_retention(now=dans(2), dry_run=False)
        assert not report.dry_run
        assert [(s.events, s.artifacts, s.keys) for s in report.swept] == [(longueur, 0, 0)]
        assert await loom.sessions() == []
    assert not (tmp_path / "events" / DEFAULT_TENANT / f"{VIEILLE}.jsonl").exists()


async def test_a_swept_session_takes_its_files_with_it(demo: ConfigFactory, tmp_path: Path) -> None:
    config = load_config(
        demo(
            storage={
                **journal(tmp_path),
                "artifacts": {"backend": "local", "path": str(tmp_path / "files")},
                "retention": {"events_days": 1},
            },
            # Un caractère : le moindre résultat d'outil part au stockage de
            # fichiers. Ce qu'on éprouve ici n'est pas le seuil (2.3) mais que
            # le fichier s'en va avec sa session.
            execution={"tools": {"offload_over": 1}},
        )
    )
    async with Loom(config) as loom:
        result = await loom.run("demo", QUESTION, session_id=VIEILLE)
        assert result.status == "completed"
        fichiers = [path for path in (tmp_path / "files").rglob("*") if path.is_file()]
        assert fichiers, "le run devait déporter un résultat"
        report = await loom.apply_retention(now=dans(2), dry_run=False)
    assert [session.artifacts for session in report.swept] == [len(fichiers)]
    assert not [path for path in (tmp_path / "files").rglob("*") if path.is_file()]


async def test_an_unfinished_session_goes_like_the_others(
    demo: ConfigFactory, tmp_path: Path
) -> None:
    config = load_config(demo(storage={**journal(tmp_path), "retention": {"events_days": 1}}))
    store = JsonlEventStore(tmp_path / "events")
    await ecrit(store, VIEILLE, inachevee=True)
    await store.aclose()
    async with Loom(config) as loom:
        report = await loom.apply_retention(now=dans(2), dry_run=False)
    # Conséquence assumée : la rétention ne lit pas le journal, donc elle ne
    # sait pas qu'un run n'a jamais fini. Une borne plus courte que le délai
    # d'une approbation détruit des approbations en attente.
    assert [session.session_id for session in report.swept] == [VIEILLE]


# --- Par client ----------------------------------------------------------------


async def test_a_client_can_have_its_own_bound(demo: ConfigFactory, tmp_path: Path) -> None:
    config = load_config(
        demo(
            storage={**journal(tmp_path), "retention": {"events_days": 30}},
            tenants=[{"id": str(MARTIN), "retention": {"events_days": 2}}, {"id": str(DUPONT)}],
        )
    )
    store = JsonlEventStore(tmp_path / "events")
    await ecrit(store, VIEILLE, tenant=MARTIN)
    await ecrit(store, VIEILLE, tenant=DUPONT)
    await store.aclose()
    async with Loom(config) as loom:
        report = await loom.apply_retention(now=dans(10), dry_run=False)
    assert report.days == {MARTIN: 2, DUPONT: 30}
    # Dix jours : Martin a dépassé ses deux jours, Dupont pas ses trente.
    assert [(s.tenant_id, s.session_id) for s in report.swept] == [(MARTIN, VIEILLE)]
    assert (tmp_path / "events" / DUPONT / f"{VIEILLE}.jsonl").exists()


async def test_a_client_can_cancel_the_common_rule(demo: ConfigFactory, tmp_path: Path) -> None:
    config = load_config(
        demo(
            storage={**journal(tmp_path), "retention": {"events_days": 1}},
            tenants=[
                {"id": str(MARTIN), "retention": {"events_days": None}},
                {"id": str(DUPONT)},
            ],
        )
    )
    store = JsonlEventStore(tmp_path / "events")
    await ecrit(store, VIEILLE, tenant=MARTIN)
    await ecrit(store, VIEILLE, tenant=DUPONT)
    await store.aclose()
    async with Loom(config) as loom:
        report = await loom.apply_retention(now=dans(100), dry_run=False)
    assert report.days == {MARTIN: None, DUPONT: 1}
    assert [s.tenant_id for s in report.swept] == [DUPONT]
    assert (tmp_path / "events" / MARTIN / f"{VIEILLE}.jsonl").exists()


async def test_one_client_can_be_swept_alone(demo: ConfigFactory, tmp_path: Path) -> None:
    config = load_config(
        demo(
            storage={**journal(tmp_path), "retention": {"events_days": 1}},
            tenants=deux_clients(),
        )
    )
    store = JsonlEventStore(tmp_path / "events")
    await ecrit(store, VIEILLE, tenant=MARTIN)
    await ecrit(store, VIEILLE, tenant=DUPONT)
    await store.aclose()
    async with Loom(config) as loom:
        report = await loom.apply_retention(tenant_id=MARTIN, now=dans(2), dry_run=False)
    assert report.days == {MARTIN: 1}
    assert [s.tenant_id for s in report.swept] == [MARTIN]
    assert (tmp_path / "events" / DUPONT / f"{VIEILLE}.jsonl").exists()


# --- Ce que le sceau change, et ne change pas (5.5b) ---------------------------


async def test_a_sealed_journal_is_swept_without_its_key(
    demo: ConfigFactory, tmp_path: Path
) -> None:
    pytest.importorskip("cryptography", reason="extra 'crypto' absent")
    config = load_config(
        demo(
            storage={
                **journal(tmp_path),
                "encryption": {"keys": [CLE_ENV]},
                "retention": {"events_days": 1},
            }
        )
    )
    async with Loom(config, environ={CLE_ENV: CLE}) as loom:
        assert (await loom.run("demo", QUESTION, session_id=VIEILLE)).status == "completed"
    # La clé effacée, le contenu est perdu — mais la place, non : c'est tout
    # l'intérêt d'une borne qui se lit sur la marque et jamais sur le contenu.
    async with Loom(config, environ={}) as efface:
        with pytest.raises(MissingKey):
            await efface.export_session(VIEILLE)
        report = await efface.apply_retention(now=dans(2), dry_run=False)
    assert [session.session_id for session in report.swept] == [VIEILLE]
    assert not (tmp_path / "events" / DEFAULT_TENANT / f"{VIEILLE}.jsonl").exists()


# --- La configuration ----------------------------------------------------------


def test_a_bound_of_zero_days_is_refused(demo: ConfigFactory, tmp_path: Path) -> None:
    # Zéro jour voudrait dire « efface tout, maintenant », y compris la session
    # qu'un run est en train d'écrire.
    with pytest.raises(ConfigError):
        load_config(demo(storage={**journal(tmp_path), "retention": {"events_days": 0}}))


def test_an_unknown_retention_key_is_refused(demo: ConfigFactory, tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_config(demo(storage={**journal(tmp_path), "retention": {"sessions_days": 30}}))


def test_the_bound_of_a_client_is_read_by_the_config(demo: ConfigFactory, tmp_path: Path) -> None:
    config = load_config(
        demo(
            storage={**journal(tmp_path), "retention": {"events_days": 30}},
            tenants=[{"id": str(MARTIN), "retention": {"events_days": 7}}, {"id": str(DUPONT)}],
        )
    )
    assert config.retention_days(MARTIN) == 7
    assert config.retention_days(DUPONT) == 30
    assert config.retention_days(TenantId("inconnu")) == 30


# --- La commande ---------------------------------------------------------------


def test_the_command_touches_nothing_without_yes(
    demo: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = demo(storage={**journal(tmp_path), "retention": {"events_days": 1}})
    _vieillie(tmp_path, VIEILLE)
    assert main(["--config", str(path), "retention"]) == 0
    out = capsys.readouterr().out
    assert "seraient effacées" in out
    assert "rien n'a été supprimé" in out.lower()
    assert (tmp_path / "events" / DEFAULT_TENANT / f"{VIEILLE}.jsonl").exists()


def test_the_command_sweeps_with_yes(
    demo: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = demo(storage={**journal(tmp_path), "retention": {"events_days": 1}})
    _vieillie(tmp_path, VIEILLE)
    assert main(["--config", str(path), "retention", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "effacée" in out
    assert not (tmp_path / "events" / DEFAULT_TENANT / f"{VIEILLE}.jsonl").exists()


def test_the_command_can_print_its_report_in_json(
    demo: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = demo(storage={**journal(tmp_path), "retention": {"events_days": 1}})
    _vieillie(tmp_path, VIEILLE)
    assert main(["--config", str(path), "retention", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["dry_run"] is True
    assert report["days"] == {DEFAULT_TENANT: 1}
    assert [session["session_id"] for session in report["swept"]] == [VIEILLE]


def test_validate_prints_the_active_rule(
    demo: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sans = demo(storage=journal(tmp_path))
    assert main(["--config", str(sans), "validate"]) == 0
    assert "Rétention  : aucune (rien ne s'efface)" in capsys.readouterr().out
    avec = demo(
        storage={**journal(tmp_path), "retention": {"events_days": 30}},
        tenants=[{"id": str(MARTIN), "retention": {"events_days": 7}}, {"id": str(DUPONT)}],
    )
    assert main(["--config", str(avec), "validate"]) == 0
    out = capsys.readouterr().out
    assert "sessions effacées après 30 jour(s)" in out
    assert f"{MARTIN}: 7 jour(s)" in out
    assert "loom retention" in out


def _vieillie(tmp_path: Path, session: SessionId, *, tenant: TenantId = DEFAULT_TENANT) -> None:
    """Un journal dont la dernière écriture est datée d'il y a longtemps.

    La commande n'a pas d'horloge à truquer : c'est le journal qui est vieux,
    comme il le serait pour de vrai.
    """
    root = tmp_path / "events" / tenant
    root.mkdir(parents=True, exist_ok=True)
    scribe = RunJournal(session_id=session, tenant_id=tenant)
    scribe.start("Combien font 2 + 2 ?")
    scribe.complete()
    vieux = datetime.now(UTC) - timedelta(days=90)
    lignes = [
        draft.to_event(seq).model_copy(update={"ts": vieux}).model_dump_json()
        for seq, draft in enumerate(scribe.take(), start=1)
    ]
    (root / f"{session}.jsonl").write_text("\n".join(lignes) + "\n", encoding="utf-8")

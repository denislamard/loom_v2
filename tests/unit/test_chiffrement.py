# SPDX-License-Identifier: Apache-2.0
"""Sceau des contenus au repos, clé par client, crypto-shredding (#30, J5.5b).

Le journal garde tout, toujours — mais scellé, il ne le garde lisible que
pour qui a la clé du client. Effacer cette clé rend son contenu perdu, ce qui
est l'effet voulu : c'est le *crypto-shredding*, en complément de la
suppression physique.

Ce que ces essais vérifient, et qui n'est vrai que si le code le fait : le
contenu n'est nulle part en clair ; l'enveloppe, elle, reste en clair, donc
tout ce qui se filtrait se filtre encore ; un journal sans sa clé se **liste
et se supprime** quand même ; une charge ne se déplace pas ; une clé ne
s'emprunte pas d'un client à l'autre.
"""

import json
from base64 import b64encode
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import QUESTION, ConfigFactory, demo_agent

from loom_ia.access import Loom
from loom_ia.adapters.artifacts import InMemoryArtifactStore, SealingArtifactStore
from loom_ia.adapters.artifacts.sealing import MAGIC
from loom_ia.adapters.stores import JsonlEventStore, SealingCodec
from loom_ia.adapters.stores.codec import SEALED
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import DURABLE_PAYLOADS, EventDraft
from loom_ia.core.events.query import EventQuery
from loom_ia.core.model import DEFAULT_TENANT, Message, SessionId, TenantId, ToolOutput
from loom_ia.core.ports import Cipher, MissingKey, SealBroken, SealError
from loom_ia.core.projections import fold
from loom_ia.runtime import create_artifact_store, create_event_store, encryption_warnings
from loom_ia.testing import RunJournal, tool_call_message

pytest.importorskip("cryptography", reason="extra 'crypto' absent")

from loom_ia.adapters.crypto import AesGcmCipher, SecretKeyring, decode_key, fingerprint

SESSION = SessionId("c-42")
MARTIN = TenantId("martin")
DUPONT = TenantId("dupont")
# Ce que le contenu d'un run porte, et qui ne doit apparaître nulle part en
# clair une fois le journal scellé.
SECRET_TEXT = "Combien font 2 + 2 ?"

KEY_ENV = "LOOM_JOURNAL_KEY"
KEY_A = b64encode(b"A" * 32).decode()
KEY_B = b64encode(b"B" * 32).decode()
KEY_C = b64encode(b"C" * 32).decode()

SEAL: dict[str, Any] = {"keys": [KEY_ENV]}


class FakeSecrets:
    """``SecretProvider`` de table : ce que chaque client peut lire."""

    def __init__(self, tables: dict[TenantId, dict[str, str]], common: dict[str, str]) -> None:
        self._tables = tables
        self._common = common

    def secrets(self, tenant_id: TenantId) -> dict[str, str]:
        return {**self._common, **self._tables.get(tenant_id, {})}


def keyring(*, common: str | None = KEY_A, **tables: str) -> SecretKeyring:
    """Trousseau sur un secret unique, redirigé par client quand il faut."""
    per_tenant = {TenantId(name): {KEY_ENV: value} for name, value in tables.items()}
    return SecretKeyring(
        FakeSecrets(per_tenant, {KEY_ENV: common} if common is not None else {}), [KEY_ENV]
    )


def run_drafts(session: SessionId = SESSION, tenant: TenantId = DEFAULT_TENANT) -> list[EventDraft]:
    journal = RunJournal(session_id=session, tenant_id=tenant)
    journal.start(SECRET_TEXT)
    journal.model_turn(tool_call_message(("c1", "calculer", {"expr": "2+2"})))
    journal.tool_results({"c1": ToolOutput.text("4")})
    journal.model_turn(Message.assistant("4"))
    journal.complete()
    return journal.take()


def sealed_store(tmp_path: Path, ring: SecretKeyring) -> JsonlEventStore:
    return JsonlEventStore(tmp_path / "journal", codec=SealingCodec(ring))


def lines(tmp_path: Path, tenant: TenantId = DEFAULT_TENANT) -> list[dict[str, Any]]:
    """Le journal tel qu'il est rangé sur le disque, ligne par ligne."""
    path = tmp_path / "journal" / tenant / f"{SESSION}.jsonl"
    raw = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in raw]


# --- Ce qui est fermé, ce qui reste en clair -----------------------------------


async def test_a_sealed_journal_reads_back_exactly(tmp_path: Path) -> None:
    drafts = run_drafts()
    store = sealed_store(tmp_path, keyring())
    written = await store.append(drafts, expected_seq=0)
    # Le journal relu est celui qu'on a écrit : le sceau n'est pas une perte.
    reread = await store.read(DEFAULT_TENANT, SESSION)
    assert reread == written
    assert fold(reread, drafts[0].run_id).output == Message.assistant("4")
    await store.aclose()


async def test_the_content_is_nowhere_in_the_clear(tmp_path: Path) -> None:
    store = sealed_store(tmp_path, keyring())
    await store.append(run_drafts(), expected_seq=0)
    await store.aclose()
    path = tmp_path / "journal" / DEFAULT_TENANT / f"{SESSION}.jsonl"
    raw = path.read_text(encoding="utf-8")
    assert SECRET_TEXT not in raw
    # Les arguments et le résultat de l'outil sont du contenu ; le **nom** de
    # l'outil est une facette, donc en clair et voulu tel quel (5.4c) — c'est
    # ce qui permet de chercher ses appels dans un journal scellé.
    assert "2+2" not in raw
    assert "calculer" in raw
    # La charge est remplacée par un sceau, et rien d'autre n'en subsiste.
    for line in lines(tmp_path):
        assert set(line["payload"]) == {SEALED, "key_id"}
        assert line["payload"]["key_id"] == fingerprint(decode_key(KEY_A, what="essai"))


async def test_the_envelope_stays_in_the_clear_so_a_query_still_filters(tmp_path: Path) -> None:
    store = sealed_store(tmp_path, keyring())
    await store.append(run_drafts(), expected_seq=0)
    rangees = lines(tmp_path)
    # Ce que les stores SQL indexent : types, statuts, facettes, identifiants.
    types = {line["type"] for line in rangees}
    assert rangees[0]["type"] == "run.started"
    assert {"tool.called", "tool.completed", "run.completed"} <= types
    assert all(line["status"] == "ok" for line in rangees)
    assert any(line["facets"].get("tool_name") == "calculer" for line in rangees)
    assert {line["tenant_id"] for line in rangees} == {DEFAULT_TENANT}
    # Donc une requête filtre comme sur un journal en clair.
    found = await store.query(EventQuery(tenant_id=DEFAULT_TENANT, tool_name="calculer"))
    assert [event.type for event in found] == ["tool.called", "tool.completed"]
    await store.aclose()


async def test_a_clear_line_stays_readable_once_the_seal_is_declared(tmp_path: Path) -> None:
    drafts = run_drafts()
    clear = JsonlEventStore(tmp_path / "journal")
    await clear.append(drafts[:2], expected_seq=0)
    await clear.aclose()
    # Déclarer le sceau ne réécrit pas ce qui existe : l'ancien se relit tel
    # quel, et la suite est fermée.
    store = sealed_store(tmp_path, keyring())
    await store.append(drafts[2:], expected_seq=2)
    assert len(await store.read(DEFAULT_TENANT, SESSION)) == len(drafts)
    rangees = lines(tmp_path)
    assert "type" in rangees[0]["payload"]
    assert SEALED in rangees[-1]["payload"]
    await store.aclose()


# --- Sans la clé ---------------------------------------------------------------


async def test_without_the_key_the_payload_stays_closed(tmp_path: Path) -> None:
    await _write_sealed(tmp_path)
    shredded = sealed_store(tmp_path, keyring(common=None))
    with pytest.raises(MissingKey, match="aucune clé de sceau") as error:
        await shredded.read(DEFAULT_TENANT, SESSION)
    # Le trousseau dit qu'il n'a rien ; la ligne dit quelle clé l'a fermée. Un
    # opérateur qui en garde plusieurs a besoin des deux pour savoir laquelle
    # remettre.
    assert fingerprint(decode_key(KEY_A, what="essai")) in str(error.value)
    await shredded.aclose()


async def test_a_shredded_journal_can_still_be_listed_and_deleted(tmp_path: Path) -> None:
    drafts = await _write_sealed(tmp_path)
    shredded = sealed_store(tmp_path, keyring(common=None))
    # C'est ce que la suppression RGPD exige : voir ce qu'il reste, et le
    # retirer, sans pouvoir le lire.
    sessions = await shredded.sessions(DEFAULT_TENANT)
    assert [record.session_id for record in sessions] == [SESSION]
    assert sessions[0].last_seq == len(drafts)
    assert await shredded.last_seq(DEFAULT_TENANT, SESSION) == len(drafts)
    assert await shredded.delete(DEFAULT_TENANT, SESSION) == len(drafts)
    assert await shredded.sessions(DEFAULT_TENANT) == []
    await shredded.aclose()


async def test_another_key_does_not_open_the_seal(tmp_path: Path) -> None:
    await _write_sealed(tmp_path)
    other = sealed_store(tmp_path, keyring(common=KEY_B))
    with pytest.raises(MissingKey) as error:
        await other.read(DEFAULT_TENANT, SESSION)
    # Le message nomme l'empreinte attendue : elle dit laquelle manque.
    assert fingerprint(decode_key(KEY_A, what="essai")) in str(error.value)
    await other.aclose()


async def test_a_new_key_seals_while_the_old_one_still_opens(tmp_path: Path) -> None:
    drafts = await _write_sealed(tmp_path)
    tournee = SecretKeyring(
        FakeSecrets({}, {"NEUVE": KEY_B, "ANCIENNE": KEY_A}), ["NEUVE", "ANCIENNE"]
    )
    store = JsonlEventStore(tmp_path / "journal", codec=SealingCodec(tournee))
    # L'ancienne ouvre ce qu'elle a fermé, la neuve ferme la suite.
    assert len(await store.read(DEFAULT_TENANT, SESSION)) == len(drafts)
    await store.append(run_drafts()[:1], expected_seq=len(drafts))
    empreintes = {line["payload"]["key_id"] for line in lines(tmp_path)}
    assert empreintes == {
        fingerprint(decode_key(KEY_A, what="essai")),
        fingerprint(decode_key(KEY_B, what="essai")),
    }
    await store.aclose()


async def _write_sealed(tmp_path: Path, tenant: TenantId = DEFAULT_TENANT) -> list[EventDraft]:
    drafts = run_drafts(tenant=tenant)
    store = sealed_store(tmp_path, keyring())
    await store.append(drafts, expected_seq=0)
    await store.aclose()
    return drafts


# --- Une charge ne se déplace pas (l'AAD) -------------------------------------


async def test_a_sealed_payload_moved_elsewhere_does_not_open(tmp_path: Path) -> None:
    await _write_sealed(tmp_path)
    path = tmp_path / "journal" / DEFAULT_TENANT / f"{SESSION}.jsonl"
    rangees = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    # Le premier événement, à une autre place. Rien d'autre ne change, et c'est
    # le cas que **seul** l'AAD peut voir : l'enveloppe recopie de la charge son
    # type, son statut et ses facettes, et les vérifie à la relecture, mais rien
    # dans une charge ne dit à quel rang elle a été écrite.
    deplacee = {**rangees[0], "seq": 7}
    path.write_text(json.dumps(deplacee) + "\n", encoding="utf-8")
    store = sealed_store(tmp_path, keyring())
    with pytest.raises(SealBroken, match="ne s'ouvre pas"):
        await store.read(DEFAULT_TENANT, SESSION)
    await store.aclose()


async def test_a_sealed_payload_of_another_session_does_not_open(tmp_path: Path) -> None:
    await _write_sealed(tmp_path)
    path = tmp_path / "journal" / DEFAULT_TENANT / f"{SESSION}.jsonl"
    premiere = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    # Le même client, un autre journal : le sceau y est aussi étranger.
    ailleurs = tmp_path / "journal" / DEFAULT_TENANT / "c-99.jsonl"
    ailleurs.write_text(
        json.dumps({**premiere, "session_id": "c-99"}) + "\n",
        encoding="utf-8",
    )
    store = sealed_store(tmp_path, keyring())
    with pytest.raises(SealBroken):
        await store.read(DEFAULT_TENANT, SessionId("c-99"))
    await store.aclose()


async def test_a_tenant_never_opens_another_tenants_journal(tmp_path: Path) -> None:
    ring = keyring(common=None, martin=KEY_A, dupont=KEY_B)
    store = JsonlEventStore(tmp_path / "journal", codec=SealingCodec(ring))
    await store.append(run_drafts(tenant=MARTIN), expected_seq=0)
    martin = tmp_path / "journal" / MARTIN / f"{SESSION}.jsonl"
    dupont = tmp_path / "journal" / DUPONT
    dupont.mkdir(parents=True)
    # La ligne de Martin recopiée chez Dupont, client corrigé : sa clé n'ouvre
    # rien, et l'empreinte nommée n'est pas la sienne.
    volee = json.loads(martin.read_text(encoding="utf-8").splitlines()[0])
    volee["tenant_id"] = DUPONT
    (dupont / f"{SESSION}.jsonl").write_text(json.dumps(volee) + "\n", encoding="utf-8")
    with pytest.raises(MissingKey):
        await store.read(DUPONT, SESSION)
    await store.aclose()


# --- Le trousseau -------------------------------------------------------------


def test_a_redirection_to_a_missing_variable_is_not_the_common_key() -> None:
    # Règle de 5.1a : ce qu'un client redirige vers rien vaut vide, jamais le
    # secret commun. C'est ce qui fait de l'effacement d'une variable un
    # effacement, et non un emprunt.
    ring = keyring(common=KEY_A, martin="")
    assert ring.ciphers(DEFAULT_TENANT)[0].key_id == fingerprint(b"A" * 32)
    with pytest.raises(MissingKey, match="aucune clé de sceau"):
        ring.ciphers(MARTIN)


def test_a_key_that_is_not_thirty_two_bytes_is_refused() -> None:
    with pytest.raises(SealError, match="16 octets"):
        decode_key(b64encode(b"A" * 16).decode(), what="essai")
    with pytest.raises(SealError, match="base64"):
        decode_key("pas du base64 !", what="essai")


def test_base64_is_accepted_padded_or_not_standard_or_urlsafe() -> None:
    key = bytes(range(32))
    standard = b64encode(key).decode()
    from base64 import urlsafe_b64encode

    urlsafe = urlsafe_b64encode(key).decode()
    assert decode_key(standard, what="essai") == key
    assert decode_key(urlsafe, what="essai") == key
    assert decode_key(standard.rstrip("="), what="essai") == key


def test_the_fingerprint_says_which_key_without_saying_the_key() -> None:
    key = b"A" * 32
    empreinte = fingerprint(key)
    assert empreinte == AesGcmCipher(key).key_id
    assert empreinte != fingerprint(b"B" * 32)
    # Ni la clé ni son condensé direct : une empreinte se cite dans un log.
    assert b64encode(key).decode() not in empreinte
    assert len(empreinte) == 12


def test_a_seal_is_bound_to_its_aad() -> None:
    cipher: Cipher = AesGcmCipher(b"A" * 32)
    closed = cipher.seal(b"bonjour", b"ici")
    assert cipher.unseal(closed, b"ici") == b"bonjour"
    with pytest.raises(SealBroken):
        cipher.unseal(closed, b"ailleurs")
    # Deux fermetures des mêmes octets ne se ressemblent pas : le nonce change.
    assert cipher.seal(b"bonjour", b"ici") != closed


# --- Les fichiers -------------------------------------------------------------


async def test_a_sealed_file_comes_back_whole_and_is_stored_closed() -> None:
    inner = InMemoryArtifactStore()
    store = SealingArtifactStore(inner, keyring())
    uri = f"artifact://{DEFAULT_TENANT}/{SESSION}/devis.txt"
    await store.put(uri, SECRET_TEXT.encode())
    assert await store.get(uri) == SECRET_TEXT.encode()
    rangee = await inner.get(uri)
    assert rangee.startswith(MAGIC)
    assert SECRET_TEXT.encode() not in rangee
    await store.aclose()


async def test_a_file_stored_before_the_seal_stays_readable() -> None:
    inner = InMemoryArtifactStore()
    uri = f"artifact://{DEFAULT_TENANT}/{SESSION}/devis.txt"
    await inner.put(uri, b"en clair")
    store = SealingArtifactStore(inner, keyring())
    assert await store.get(uri) == b"en clair"
    await store.aclose()


async def test_a_file_copied_under_another_uri_does_not_open() -> None:
    inner = InMemoryArtifactStore()
    store = SealingArtifactStore(inner, keyring())
    uri = f"artifact://{DEFAULT_TENANT}/{SESSION}/devis.txt"
    await store.put(uri, SECRET_TEXT.encode())
    # Mêmes octets, autre nom : l'URI est authentifiée, donc le sceau refuse.
    ailleurs = f"artifact://{DEFAULT_TENANT}/{SESSION}/copie.txt"
    await inner.put(ailleurs, await inner.get(uri))
    with pytest.raises(SealBroken, match=r"copie\.txt"):
        await store.get(ailleurs)
    await store.aclose()


async def test_a_file_of_another_tenant_needs_that_tenants_key() -> None:
    inner = InMemoryArtifactStore()
    store = SealingArtifactStore(inner, keyring(common=None, martin=KEY_A))
    await store.put(f"artifact://{MARTIN}/{SESSION}/devis.txt", SECRET_TEXT.encode())
    with pytest.raises(MissingKey):
        await store.put(f"artifact://{DUPONT}/{SESSION}/devis.txt", b"x")
    await store.aclose()


# --- La configuration ---------------------------------------------------------


def test_encryption_is_declared_at_the_root_with_secret_names(demo: ConfigFactory) -> None:
    config = load_config(
        demo(storage={"events": {"backend": "jsonl", "path": "data"}, "encryption": SEAL})
    )
    declared = config.storage.encryption
    assert declared is not None
    assert declared.keys == (KEY_ENV,)


def test_a_memory_journal_cannot_be_sealed(demo: ConfigFactory) -> None:
    with pytest.raises(ConfigError, match="journal 'memory'"):
        load_config(demo(storage={"events": {"backend": "memory"}, "encryption": SEAL}))


def test_a_tenant_does_not_override_the_seal(demo: ConfigFactory) -> None:
    with pytest.raises(ConfigError, match="ne se surcharge pas"):
        load_config(
            demo(
                storage={"events": {"backend": "jsonl", "path": "data"}, "encryption": SEAL},
                tenants=[
                    {
                        "id": "martin",
                        "storage": {
                            "events": {"backend": "jsonl", "path": "martin"},
                            "encryption": SEAL,
                        },
                    }
                ],
            )
        )


def test_the_same_secret_declared_twice_is_refused(demo: ConfigFactory) -> None:
    with pytest.raises(ConfigError, match="deux fois"):
        load_config(
            demo(
                storage={
                    "events": {"backend": "jsonl", "path": "data"},
                    "encryption": {"keys": [KEY_ENV, KEY_ENV]},
                }
            )
        )


def test_no_declared_key_is_refused(demo: ConfigFactory) -> None:
    with pytest.raises(ConfigError):
        load_config(
            demo(storage={"events": {"backend": "jsonl", "path": "data"}, "encryption": {}})
        )


# --- Le montage ---------------------------------------------------------------


async def test_the_declared_seal_reaches_the_journal_and_the_files(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(KEY_ENV, KEY_A)
    storage: dict[str, Any] = {
        "events": {"backend": "jsonl", "path": str(tmp_path / "events")},
        "artifacts": {"backend": "local", "path": str(tmp_path / "files")},
    }
    config = load_config(demo(storage={**storage, "encryption": SEAL}))
    # Sans cela, tout ce qui précède éprouverait un codec que personne ne
    # monte : c'est le montage qui fait du sceau une propriété du déploiement,
    # et ce que l'essai regarde est ce qui est rangé, pas le type de l'objet.
    store = create_event_store(config)
    await store.append(run_drafts(), expected_seq=0)
    await store.aclose()
    journal = (tmp_path / "events" / DEFAULT_TENANT / f"{SESSION}.jsonl").read_text("utf-8")
    assert SECRET_TEXT not in journal
    assert SEALED in journal
    files = create_artifact_store(config)
    uri = f"artifact://{DEFAULT_TENANT}/{SESSION}/devis.txt"
    await files.put(uri, SECRET_TEXT.encode())
    assert await files.get(uri) == SECRET_TEXT.encode()
    await files.aclose()
    rangee = next((tmp_path / "files").rglob("*.txt")).read_bytes()
    assert rangee.startswith(MAGIC)
    assert SECRET_TEXT.encode() not in rangee
    # Sans le bloc, rien n'est scellé : c'est le journal de toujours.
    clear = load_config(demo(storage=storage))
    assert not isinstance(create_artifact_store(clear), SealingArtifactStore)


async def test_a_run_writes_nothing_readable_without_the_key(
    demo: ConfigFactory, tmp_path: Path
) -> None:
    path = demo(
        storage={
            "events": {"backend": "jsonl", "path": str(tmp_path / "events")},
            "artifacts": {"backend": "local", "path": str(tmp_path / "files")},
            "encryption": SEAL,
        }
    )
    config = load_config(path)
    async with Loom(config, environ={KEY_ENV: KEY_A}) as loom:
        result = await loom.run("demo", QUESTION)
        assert result.status == "completed"
        # Avec la clé, l'instance lit son journal comme avant.
        state = await loom.state(result.run_id)
        assert state is not None and state.output is not None
    rangee = (tmp_path / "events" / DEFAULT_TENANT / f"{result.run_id}.jsonl").read_text("utf-8")
    assert QUESTION not in rangee
    assert "12*7+3" not in rangee
    # Le même journal, la clé effacée : plus rien à en tirer.
    async with Loom(config, environ={}) as shredded:
        with pytest.raises(MissingKey):
            await shredded.state(result.run_id)
        assert [record.session_id for record in await shredded.sessions()] == [result.run_id]


async def test_a_client_without_a_key_cannot_run(demo: ConfigFactory, tmp_path: Path) -> None:
    path = demo(
        storage={
            "events": {"backend": "jsonl", "path": str(tmp_path / "events")},
            "encryption": SEAL,
        },
        tenants=[{"id": "martin", "secrets": {KEY_ENV: "MARTIN_KEY"}}, {"id": "dupont"}],
    )
    config = load_config(path)
    # Dupont lit la variable commune, Martin la sienne — qui n'existe pas.
    async with Loom(config, environ={KEY_ENV: KEY_A}) as loom:
        assert (await loom.run("demo", QUESTION, tenant=DUPONT)).status == "completed"
        with pytest.raises(MissingKey, match="martin"):
            await loom.run("demo", QUESTION, tenant=MARTIN)


# --- Ce que le chargement dit --------------------------------------------------


def test_a_client_without_a_key_is_said_at_load(demo: ConfigFactory, tmp_path: Path) -> None:
    config = load_config(
        demo(
            storage={
                "events": {"backend": "jsonl", "path": str(tmp_path / "events")},
                "encryption": SEAL,
            },
            tenants=[{"id": "martin", "secrets": {KEY_ENV: "MARTIN_KEY"}}, {"id": "dupont"}],
        )
    )
    warnings = encryption_warnings(config, keyring(common=KEY_A, martin=""))
    assert len(warnings) == 1
    assert "martin" in warnings[0]


def test_two_clients_sharing_a_key_are_said_at_load(demo: ConfigFactory, tmp_path: Path) -> None:
    config = load_config(
        demo(
            storage={
                "events": {"backend": "jsonl", "path": str(tmp_path / "events")},
                "encryption": SEAL,
            },
            tenants=[{"id": "martin"}, {"id": "dupont"}],
        )
    )
    # La même clé pour les deux : l'effacer effacerait les deux journaux.
    partagee = encryption_warnings(config, keyring(common=KEY_A))
    assert len(partagee) == 1
    assert "même clé" in partagee[0]
    # Une clé par client : rien à dire.
    assert encryption_warnings(config, keyring(common=None, martin=KEY_A, dupont=KEY_B)) == []


def test_without_a_keyring_there_is_nothing_to_say(demo: ConfigFactory) -> None:
    assert encryption_warnings(load_config(demo()), None) == []


# --- Le garde-fou --------------------------------------------------------------


def test_no_durable_payload_carries_a_field_named_sealed() -> None:
    """Le champ ``sealed`` distingue une charge fermée d'une charge en clair.

    Le jour où une charge en porterait un, une ligne en clair passerait pour
    un sceau, et le codec essaierait de l'ouvrir.
    """
    porteuses = [payload for payload in DURABLE_PAYLOADS if SEALED in payload.model_fields]
    assert porteuses == []


# --- Les autres journaux -------------------------------------------------------


@pytest.mark.parametrize(
    "backend",
    ["sqlite", pytest.param("postgres", marks=pytest.mark.integration)],
)
async def test_sql_journals_seal_their_payloads(
    backend: str, tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    ring = keyring()
    drafts = run_drafts()
    if backend == "sqlite":
        pytest.importorskip("aiosqlite")
        from loom_ia.adapters.stores.sqlite import SqliteEventStore

        store = SqliteEventStore(tmp_path / "journal.sqlite3", codec=SealingCodec(ring))
        rows = _sqlite_rows
    else:
        dsn = str(request.getfixturevalue("postgres_dsn"))
        from loom_ia.adapters.stores.postgres import PostgresEventStore

        store = PostgresEventStore(dsn, codec=SealingCodec(ring))
        rows = None
    written = await store.append(drafts, expected_seq=0)
    assert await store.read(DEFAULT_TENANT, SESSION) == written
    # Les colonnes indexées restent en clair, la colonne ``event`` non.
    found = await store.query(EventQuery(tenant_id=DEFAULT_TENANT, tool_name="calculer"))
    assert [event.type for event in found] == ["tool.called", "tool.completed"]
    if rows is not None:
        stored = rows(tmp_path / "journal.sqlite3")
        assert stored and all(SECRET_TEXT not in row for row in stored)
        assert all(SEALED in row for row in stored)
    await store.aclose()


def _sqlite_rows(path: Path) -> Sequence[str]:
    import sqlite3
    from contextlib import closing

    # ``closing`` et non le gestionnaire de contexte de la connexion : celui-ci
    # ne referme que la transaction, et la connexion laissée au ramasse-miettes
    # se plaint pendant l'essai suivant.
    with closing(sqlite3.connect(path)) as connection:
        return [str(row[0]) for row in connection.execute("SELECT event FROM events")]


# --- Ce que l'API répond ------------------------------------------------------


async def test_a_sealed_read_answers_424_and_names_the_key(
    demo: ConfigFactory, tmp_path: Path
) -> None:
    pytest.importorskip("fastapi")
    from httpx2 import ASGITransport, AsyncClient

    from loom_ia.access.http import create_app

    path = demo(
        storage={
            "events": {"backend": "jsonl", "path": str(tmp_path / "events")},
            "encryption": SEAL,
        },
        agents=[demo_agent(expose={"rest": True})],
    )
    config = load_config(path)
    async with Loom(config, environ={KEY_ENV: KEY_A}) as loom:
        run = await loom.run("demo", QUESTION)
    async with Loom(config, environ={}) as shredded:
        app = create_app(shredded)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://essai") as client:
            # Une demande juste sur un journal dont la clé a disparu : ce n'est
            # pas une panne du service, c'est une dépendance qui manque.
            answer = await client.get("/v1/events", params={"session_id": run.run_id})
            assert answer.status_code == 424
            assert "clé" in answer.json()["detail"]


def test_a_config_written_by_hand_reads_back(tmp_path: Path) -> None:
    """La forme écrite dans les docs et les exemples, telle quelle."""
    raw = """
    version: 1
    storage:
      events: {backend: jsonl, path: data/events}
      encryption:
        keys: [LOOM_JOURNAL_KEY]
    tenants:
      - id: martin
        secrets: {LOOM_JOURNAL_KEY: MARTIN_JOURNAL_KEY}
    models: []
    """
    (tmp_path / "agents").mkdir()
    (tmp_path / "loom.yaml").write_text(yaml.safe_dump(yaml.safe_load(raw)), encoding="utf-8")
    config = load_config(tmp_path / "loom.yaml")
    assert config.storage.encryption is not None
    assert config.tenant_spec(MARTIN) is not None

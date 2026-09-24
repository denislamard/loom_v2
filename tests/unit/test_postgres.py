# SPDX-License-Identifier: Apache-2.0
"""Stockage Postgres : le SQL, la config, le câblage et la CLI (J5.3a).

Ce qui se vérifie sans base. Le journal et le magasin d'idempotence eux-mêmes
sont éprouvés par les suites de contrat (``test_event_stores.py``,
``test_idempotence.py``), qui tournent sur un vrai Postgres quand
``LOOM_TEST_POSTGRES`` en désigne un ; ce que Postgres seul peut montrer — la
politique de lignes, les droits du rôle — est dans
``tests/integration/test_postgres.py``.
"""

from importlib.util import find_spec
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import ConfigFactory

from loom_ia.access.cli import main
from loom_ia.adapters.postgres.sql import (
    DEFAULT_ROLE,
    EVENTS_TABLE,
    IDEMPOTENCY_TABLE,
    check_name,
    ddl,
)
from loom_ia.config import ConfigError, load_config
from loom_ia.config.models import (
    DEFAULT_DB_ROLE,
    DSN_BACKENDS,
    DURABLE_BACKENDS,
    SHARED_IDEMPOTENCY,
    EventsStorage,
    IdempotencyStorage,
    StorageConfig,
)
from loom_ia.runtime import create_event_store, create_idempotency_store, postgres_ddl

# Ce qui demande le pilote : le SQL, la config et la CLI s'en passent.
sans_extra = pytest.mark.skipif(find_spec("asyncpg") is None, reason="extra 'postgres' absent")
# L'inverse : ce qui ne se voit que sans l'extra, dans la passe noyau seul.
avec_extra = pytest.mark.skipif(find_spec("asyncpg") is not None, reason="extra 'postgres' présent")

DSN = "postgresql://loom_owner:secret@127.0.0.1:5432/loom_test"
VARIABLE = "LOOM_PG_DSN_ESSAI"

PG_EVENTS: dict[str, Any] = {"backend": "postgres", "dsn_env": VARIABLE}
PG_STORAGE: dict[str, Any] = {"events": PG_EVENTS, "artifacts": {"backend": "memory"}}


# --- Le SQL ------------------------------------------------------------------


def test_the_events_table_is_closed_to_the_other_clients() -> None:
    """La politique compare le client de la ligne au réglage de la transaction."""
    sql = ddl()
    assert f"ALTER TABLE {EVENTS_TABLE} ENABLE ROW LEVEL SECURITY;" in sql
    # ``FORCE`` : la politique vaut aussi pour le propriétaire des tables.
    assert f"ALTER TABLE {EVENTS_TABLE} FORCE ROW LEVEL SECURITY;" in sql
    assert "tenant_id = current_setting('loom.tenant_id', true)" in sql
    assert "WITH CHECK" in sql


def test_the_ddl_can_be_applied_twice() -> None:
    """Chaque instruction se rattrape : loom l'applique, ou l'exploitant, ou les deux."""
    sql = ddl()
    assert sql.count("IF NOT EXISTS") >= 8
    # Politique et rôle n'ont pas d'``IF NOT EXISTS`` : le bloc avale l'erreur.
    assert sql.count("EXCEPTION WHEN duplicate_object THEN NULL;") == 2


def test_the_app_role_cannot_change_a_written_event() -> None:
    """Le journal est immuable par privilège, pas seulement par convention."""
    sql = ddl()
    assert f"GRANT SELECT, INSERT, DELETE ON {EVENTS_TABLE} TO {DEFAULT_ROLE};" in sql
    # L'idempotence, elle, a besoin de modifier : une réservation devient un résultat.
    assert f"GRANT SELECT, INSERT, UPDATE, DELETE ON {IDEMPOTENCY_TABLE} TO {DEFAULT_ROLE};" in sql


def executable(sql: str) -> str:
    """Le DDL sans ses commentaires : ce que Postgres exécutera vraiment."""
    return "\n".join(line for line in sql.splitlines() if not line.startswith("--"))


def test_without_an_app_role_nothing_is_granted() -> None:
    # Sur la partie exécutable : l'en-tête, lui, parle d'un rôle d'exploitation.
    sql = executable(ddl(role=None))
    assert "GRANT" not in sql
    assert "CREATE ROLE" not in sql
    # La politique reste : c'est ``FORCE`` qui la fait tenir sur le propriétaire.
    assert "CREATE POLICY" in sql


def test_the_sql_says_what_the_policy_changes_for_whoever_reads_or_backs_up() -> None:
    """Le jour où une lecture à la main ou une sauvegarde échoue, on cherche loin."""
    sql = ddl()
    assert sql.startswith("-- Stockage Postgres de loom")
    assert "SET loom.tenant_id = '<client>';" in sql
    assert "BYPASSRLS" in sql
    assert "--enable-row-security" in sql


def test_a_table_can_be_asked_for_alone() -> None:
    assert IDEMPOTENCY_TABLE not in executable(ddl(idempotency=False))
    assert EVENTS_TABLE not in executable(ddl(events=False))


@pytest.mark.parametrize("name", ["loom app", "loom-app", 'x"; DROP TABLE loom_events; --', "Loom"])
def test_a_role_name_that_would_need_quoting_is_refused(name: str) -> None:
    """Le DDL est du texte : plutôt qu'échapper, on n'accepte que le simple."""
    with pytest.raises(ValueError, match="Rôle Postgres"):
        check_name(name)


def test_a_plain_role_name_passes() -> None:
    assert check_name("loom_app_2") == "loom_app_2"


@sans_extra
@pytest.mark.parametrize(
    ("status", "expected"), [("INSERT 0 1", 1), ("UPDATE 0", 0), ("DELETE 12", 12), ("", 0)]
)
def test_how_many_rows_a_command_touched(status: str, expected: int) -> None:
    from loom_ia.adapters.postgres.pool import rows_touched

    assert rows_touched(status) == expected


# --- La config ---------------------------------------------------------------


def test_the_default_app_role_is_the_one_the_ddl_creates() -> None:
    """La config ne dépend pas du pilote : les deux constantes doivent concorder."""
    assert DEFAULT_DB_ROLE == DEFAULT_ROLE
    assert EventsStorage(backend="postgres", dsn_env=VARIABLE).role == DEFAULT_ROLE


def test_postgres_is_durable_and_shared() -> None:
    """Une approbation et une clé métier l'acceptent désormais (#28, #49)."""
    assert "postgres" in DURABLE_BACKENDS
    assert "postgres" in SHARED_IDEMPOTENCY
    assert "postgres" in DSN_BACKENDS


def test_the_config_names_the_variable_never_the_dsn() -> None:
    storage = StorageConfig.model_validate(PG_STORAGE)
    assert storage.events.dsn_env == VARIABLE
    assert storage.events.path is None
    # Le journal Postgres ne donne pas de dossier : les artefacts se déclarent.
    assert storage.artifacts_backend == "memory"


def test_a_postgres_journal_demands_that_artifacts_be_declared() -> None:
    with pytest.raises(ValueError, match=r"déclarer 'storage.artifacts'"):
        StorageConfig.model_validate({"events": PG_EVENTS})


def test_the_idempotency_store_can_share_the_journals_variable() -> None:
    storage = StorageConfig.model_validate(
        {**PG_STORAGE, "idempotency": {"backend": "postgres", "dsn_env": VARIABLE}}
    )
    assert storage.idempotency.shared
    assert storage.idempotency.dsn_env == storage.events.dsn_env


# --- Le câblage --------------------------------------------------------------


@sans_extra
def test_an_empty_variable_is_named_in_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(VARIABLE, raising=False)
    storage = StorageConfig.model_validate(PG_STORAGE)
    with pytest.raises(ConfigError, match=rf"{VARIABLE}.* est vide ou absente"):
        create_event_store(storage)


@sans_extra
def test_the_journal_is_built_from_the_variable(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(VARIABLE, DSN)
    config = load_config(demo(storage=PG_STORAGE))
    store = create_event_store(config)
    # Rien n'est ouvert avant la première requête : la base peut être absente.
    assert repr(store) == f"PostgresEventStore({EVENTS_TABLE!r})"


@sans_extra
def test_the_idempotency_store_too(demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VARIABLE, DSN)
    config = load_config(
        demo(storage={**PG_STORAGE, "idempotency": {"backend": "postgres", "dsn_env": VARIABLE}})
    )
    store = create_idempotency_store(config)
    assert repr(store) == f"PostgresIdempotency({IDEMPOTENCY_TABLE!r})"


@avec_extra
def test_without_the_extra_the_refusal_names_it(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ce que voit qui déclare un journal postgres sans avoir installé l'extra."""
    monkeypatch.setenv(VARIABLE, DSN)
    with pytest.raises(ConfigError, match=r"loom-ia\[postgres\]"):
        create_event_store(load_config(demo(storage=PG_STORAGE)))


# --- Le SQL que la config demande -------------------------------------------


def test_a_config_without_postgres_gets_the_whole_sql(demo: ConfigFactory) -> None:
    """De quoi préparer une base avant de la déclarer."""
    sql = postgres_ddl(load_config(demo()))
    assert EVENTS_TABLE in sql
    assert IDEMPOTENCY_TABLE in sql
    assert f"CREATE ROLE {DEFAULT_ROLE}" in sql


def test_only_what_the_config_declares(demo: ConfigFactory) -> None:
    sql = postgres_ddl(load_config(demo(storage=PG_STORAGE)))
    assert f"CREATE TABLE IF NOT EXISTS {EVENTS_TABLE}" in sql
    assert IDEMPOTENCY_TABLE not in sql


def test_two_stores_two_roles_two_role_blocks() -> None:
    storage = StorageConfig.model_validate(
        {
            "events": {**PG_EVENTS, "role": "lecture_journal"},
            "artifacts": {"backend": "memory"},
            "idempotency": {"backend": "postgres", "dsn_env": VARIABLE, "role": "lecture_cles"},
        }
    )
    sql = postgres_ddl(storage)
    assert "CREATE ROLE lecture_journal" in sql
    assert "CREATE ROLE lecture_cles" in sql
    assert f"GRANT SELECT, INSERT, DELETE ON {EVENTS_TABLE} TO lecture_journal;" in sql


def test_one_role_for_both_is_created_once() -> None:
    storage = StorageConfig.model_validate(
        {
            "events": PG_EVENTS,
            "artifacts": {"backend": "memory"},
            "idempotency": {"backend": "postgres", "dsn_env": VARIABLE},
        }
    )
    assert postgres_ddl(storage).count(f"CREATE ROLE {DEFAULT_ROLE}") == 1


# --- La ligne de commande ----------------------------------------------------


def test_storage_sql_prints_what_there_is_to_apply(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config", str(demo(storage=PG_STORAGE)), "storage", "sql"]) == 0
    out = capsys.readouterr().out
    assert f"CREATE TABLE IF NOT EXISTS {EVENTS_TABLE}" in out
    assert "FORCE ROW LEVEL SECURITY" in out
    # Rien n'a été exécuté : la commande n'ouvre aucune connexion.
    assert "loom_owner" not in out


@sans_extra
def test_validate_says_whether_the_variable_is_filled(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """La variable manquante est dite, puis elle fait échouer le montage.

    Dite, parce que c'est ce qui manque le plus souvent ; et ``validate`` sort
    en 2, parce qu'une config qui ne peut pas monter n'est pas valide.
    """
    monkeypatch.delenv(VARIABLE, raising=False)
    path = demo(storage=PG_STORAGE)
    assert main(["--config", str(path), "validate"]) == 2
    absente = capsys.readouterr()
    assert (
        f"Journal    : postgres (DSN dans {VARIABLE} : ABSENTE, rôle : {DEFAULT_ROLE})"
        in absente.out
    )
    assert f"la variable {VARIABLE!r} est vide ou absente" in absente.err

    # Renseignée, le montage va jusqu'au bout : rien ne se connecte avant la
    # première requête, et ``validate`` n'en fait aucune.
    monkeypatch.setenv(VARIABLE, DSN)
    assert main(["--config", str(path), "validate"]) == 0
    remplie = capsys.readouterr().out
    assert f"DSN dans {VARIABLE} : renseignée" in remplie
    # Le DSN lui-même n'est jamais imprimé.
    assert "secret" not in remplie


def test_the_dsn_is_never_written_to_a_file(tmp_path: Path) -> None:
    """Un rappel : la config porte le nom de la variable, jamais sa valeur."""
    written = yaml.safe_dump({"storage": PG_STORAGE})
    assert VARIABLE in written
    assert "secret" not in written
    (tmp_path / "loom.yaml").write_text(written, encoding="utf-8")


def test_a_backend_still_to_come_is_refused() -> None:
    with pytest.raises(ValueError, match="seuls memory, jsonl, sqlite, postgres"):
        EventsStorage(backend="firestore")
    with pytest.raises(ValueError, match="seuls journal, memory, sqlite, postgres, redis"):
        IdempotencyStorage(backend="firestore")

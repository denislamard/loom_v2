# SPDX-License-Identifier: Apache-2.0
"""Profils dev et prod : ce qu'ils durcissent, ce qu'ils assouplissent (M4, J5.5a).

Sans ``profile``, rien ne change : les avertissements restent des
avertissements et les erreurs des erreurs. ``prod`` durcit les premiers,
``dev`` assouplit les secondes.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import MODEL, QUESTION, ConfigFactory, demo_agent

from loom_ia.access import Loom
from loom_ia.config import ConfigError, load_config
from loom_ia.config.loader import PROFILE_ENV, chosen_profile
from loom_ia.core.model import JudgeWhen
from loom_ia.runtime import storage_warnings

CRITERES: list[dict[str, Any]] = [{"name": "fidele", "rule": "Rien d'inventé."}]
# Un juge dont le modèle est celui de la sortie qu'il évalue : avertissement
# depuis 3.3, erreur en prod.
CORRELE: dict[str, Any] = {"model": "FAKE", "criteria": CRITERES}
# Le même juge, sur son propre modèle : rien à reprocher, dans aucun profil.
NOTE: dict[str, Any] = {"name": "fidele", "score": 1.0, "reason": "vérifié"}
VERDICT: dict[str, Any] = {"tool_calls": [{"name": "verdict", "arguments": {"criteria": [NOTE]}}]}
JUGE: dict[str, Any] = {
    "id": "JUGE",
    "sdk": "fake",
    "model": "juge-1",
    "params": {"script": [VERDICT]},
}
PROPRE: dict[str, Any] = {"model": "JUGE", "criteria": CRITERES}
BROUILLE: dict[str, Any] = {
    "queue": {"backend": "rabbitmq", "url_env": "LOOM_RABBITMQ"},
    "artifacts": {"backend": "local", "path": "data/.artifacts"},
}


def juge(demo: ConfigFactory, spec: dict[str, Any], **root: Any) -> Path:
    """``demo`` avec un juge sur sa réponse finale, et le modèle qu'il faut."""
    return demo(models=[MODEL, JUGE], agents=[demo_agent(judge=spec)], **root)


# --- D'où vient le profil -----------------------------------------------------


def test_the_profile_comes_from_the_option_then_the_environment_then_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(PROFILE_ENV, raising=False)
    assert chosen_profile(None, None) == (None, "aucun")
    assert chosen_profile(None, "dev") == ("dev", "config")
    monkeypatch.setenv(PROFILE_ENV, "prod")
    assert chosen_profile(None, "dev") == ("prod", PROFILE_ENV)
    # L'option l'emporte sur les deux.
    assert chosen_profile("dev", "prod") == ("dev", "option")


def test_an_unknown_profile_names_where_it_came_from(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PROFILE_ENV, raising=False)
    with pytest.raises(ConfigError, match="Profil 'staging' inconnu \\(option\\)"):
        chosen_profile("staging")
    monkeypatch.setenv(PROFILE_ENV, "recette")
    with pytest.raises(ConfigError, match=f"inconnu \\({PROFILE_ENV}\\)"):
        chosen_profile(None)


def test_the_loaded_config_carries_the_profile_that_applies(
    demo: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(PROFILE_ENV, raising=False)
    path = demo(profile="dev")
    assert load_config(path).profile == "dev"
    # L'option l'emporte, et c'est elle que la config porte.
    assert load_config(path, profile="prod").profile == "prod"
    assert load_config(path, profile="prod").strict is True
    assert load_config(path).lax is True
    assert load_config(demo()).profile is None


# --- Les surcharges d'un profil -----------------------------------------------


def test_a_profile_merges_objects_and_replaces_lists(demo: ConfigFactory) -> None:
    path = demo(
        storage={"events": {"backend": "memory"}},
        execution={"tools": {"timeout": 5, "offload_over": 1000}},
        profiles={
            "prod": {
                "storage": {"events": {"backend": "jsonl", "path": "data"}},
                "execution": {"tools": {"timeout": 60}},
                "imports": ["outils_acces"],
            }
        },
    )
    dev = load_config(path)
    prod = load_config(path, profile="prod")

    assert dev.storage.events.backend == "memory"
    assert prod.storage.events.backend == "jsonl"
    # Fusion profonde : ce que la surcharge ne dit pas est gardé.
    assert prod.execution.tools.timeout == 60
    assert prod.execution.tools.offload_over == 1000
    # Une liste est remplacée, jamais fusionnée.
    assert prod.imports == ("outils_acces",)


def test_a_profile_that_is_not_active_changes_nothing(demo: ConfigFactory) -> None:
    path = demo(
        storage={"events": {"backend": "memory"}},
        profiles={"prod": {"storage": {"events": {"backend": "jsonl", "path": "data"}}}},
    )
    assert load_config(path).storage.events.backend == "memory"
    assert load_config(path, profile="dev").storage.events.backend == "memory"


def test_a_profile_block_that_is_not_an_object_is_refused(demo: ConfigFactory) -> None:
    with pytest.raises(ConfigError, match=r"'profiles\.prod' doit être un objet"):
        load_config(demo(profiles={"prod": ["storage"]}), profile="prod")


# --- Ce que prod durcit -------------------------------------------------------


async def test_a_correlated_judge_warns_without_a_profile_and_refuses_in_prod(
    demo: ConfigFactory,
) -> None:
    path = juge(demo, CORRELE)
    # Sans profil : ça monte, avec un avertissement dans les logs.
    async with Loom.from_config(path) as loom:
        assert loom.context("demo") is not None
    with pytest.raises(ConfigError, match=r"Profil prod .*juge corrélé"):
        async with Loom.from_config(path, profile="prod") as loom:
            loom.context("demo")


def test_a_scattered_service_refuses_to_start_in_prod(demo: ConfigFactory) -> None:
    path = demo(storage=BROUILLE)
    # Sans profil, l'avertissement de 5.3c suffit : un volume partagé est un
    # montage légitime, que la config ne distingue pas d'un dossier local. Il
    # se lit sur la config seule, sans monter la file — l'extra 'rabbitmq'
    # n'est pas nécessaire pour savoir que ça ne va pas ensemble.
    assert storage_warnings(load_config(path))
    with pytest.raises(ConfigError, match="Profil prod"):
        Loom(load_config(path, profile="prod"))


async def test_skipping_the_judges_is_refused_in_prod(demo: ConfigFactory) -> None:
    path = juge(demo, PROPRE)
    async with Loom.from_config(path) as loom:
        # Sans profil, `skip` reste permis : la portée `admin` de l'accès REST
        # est la seule barrière, comme depuis 3.3.
        assert (await loom.run("demo", QUESTION, judges="skip")).status == "completed"
    async with Loom.from_config(path, profile="prod") as loom:
        with pytest.raises(ConfigError, match=r"judges='skip'"):
            await loom.run("demo", QUESTION, judges="skip")
        # Les autres modes passent.
        assert (await loom.run("demo", QUESTION, judges="force")).status == "completed"


def test_an_open_api_without_keys_refuses_to_serve_in_prod(demo: ConfigFactory) -> None:
    pytest.importorskip("fastapi", reason="extra 'http' absent")

    from loom_ia.access.http import create_app

    ouverte: dict[str, Any] = {"http": {"host": "0.0.0.0", "port": 8000}}
    path = demo(server=ouverte)
    # Sans profil : un avertissement, et l'application se monte.
    assert create_app(Loom(load_config(path))) is not None
    with pytest.raises(ConfigError, match=r"Profil prod .*sans clé déclarée"):
        create_app(Loom(load_config(path, profile="prod")))


# --- Ce que dev assouplit -----------------------------------------------------


async def test_a_pausing_agent_on_a_volatile_journal_is_tolerated_in_dev(
    atelier: Callable[..., Path],
) -> None:
    volatile: dict[str, Any] = {"events": {"backend": "memory"}}
    path = atelier(storage=volatile)
    # Sans profil, c'est une erreur depuis 4.3a : en pause, le run n'existe
    # plus que dans le journal.
    with pytest.raises(ConfigError, match="journal durable"):
        async with Loom.from_config(path) as loom:
            loom.context("demo")
    # En dev, perdre un run en pause à la fermeture est le prix d'un essai.
    async with Loom.from_config(path, profile="dev") as loom:
        assert loom.context("demo") is not None


# --- Un juge qui ne travaille que dans un profil ------------------------------


def test_when_accepts_profiles() -> None:
    assert JudgeWhen.model_validate({"profiles": ["prod"]}).profiles == ("prod",)
    assert JudgeWhen().profiles is None


async def test_a_judge_declared_for_prod_is_skipped_elsewhere_and_says_so(
    demo: ConfigFactory,
) -> None:
    path = juge(
        demo,
        {**PROPRE, "when": {"profiles": ["prod"]}},
        storage={"events": {"backend": "jsonl", "path": "d"}},
    )
    async with Loom.from_config(path, profile="dev") as loom:
        ailleurs = await loom.run("demo", QUESTION)
        sautes = await loom.events(ailleurs.run_id)
    async with Loom.from_config(path, profile="prod") as loom:
        chez_lui = await loom.run("demo", QUESTION, judges="auto")
        juges = await loom.events(chez_lui.run_id)

    # En dev le juge ne juge pas, et le journal dit pourquoi.
    assert not ailleurs.verdicts
    passes = [e for e in sautes if e.type == "guard.checked" and e.facets.get("guard") == "judge"]
    assert [e.facets.get("outcome") for e in passes] == ["skipped"]
    assert [getattr(e.payload, "reason", "") for e in passes] == ["other_profile"]
    # En prod il juge pour de bon.
    assert [verdict.judge for verdict in chez_lui.verdicts] == ["output"]
    assert any(e.type == "judge.evaluated" for e in juges)


# --- Ce que la ligne de commande en dit --------------------------------------


def test_validate_says_which_profile_applies(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from loom_ia.access.cli import main

    monkeypatch.delenv(PROFILE_ENV, raising=False)
    path = str(
        demo(profile="dev", profiles={"prod": {"storage": {"events": {"backend": "memory"}}}})
    )
    assert main(["--config", path, "validate"]) == 0
    sans = capsys.readouterr().out
    assert "Profil     : dev (par config) — assoupli ; surcharges : prod" in sans

    assert main(["--config", path, "--profile", "prod", "validate"]) == 0
    option = capsys.readouterr().out
    assert "Profil     : prod (par option) — les avertissements sont des erreurs" in option

    assert main(["--config", str(demo()), "validate"]) == 0
    aucun = capsys.readouterr().out
    assert "Profil     : aucun (ni --profile, ni LOOM_PROFILE, ni 'profile:')" in aucun

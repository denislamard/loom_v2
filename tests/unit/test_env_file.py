# SPDX-License-Identifier: Apache-2.0
"""Le fichier d'environnement d'un projet (``.env``) : lequel, quand, et ce qu'il ne fait jamais.

``loom`` le lit avant la config ; la librairie ne lit rien d'elle-même
(``load_env_file``). Une variable déjà définie l'emporte toujours, et aucune
valeur n'est jamais affichée. Le rechargement (``serve --reload``) est
éprouvé dans ``test_rechargement.py``.
"""

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import MODEL, ConfigFactory, demo_agent

from loom_ia.access import Loom
from loom_ia.access.cli import main
from loom_ia.config import ConfigError, EnvFileReport, load_config, load_env_file
from loom_ia.config.env_file import DISABLED, reload_env_file
from loom_ia.config.loader import PROFILE_ENV

# Une valeur qu'on cherche ensuite dans tout ce qui est affiché.
SECRET = "sk-essai-ne-doit-jamais-sortir"


@pytest.fixture(autouse=True)
def environ_restored() -> Iterator[None]:
    """La CLI pose ses variables dans ``os.environ`` : chaque essai rend celui qu'il a trouvé."""
    before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(before)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def projet(folder: Path, **root: Any) -> Path:
    """Un ``loom.yaml`` réduit : ``load_env_file`` n'en lit que ``env_file``."""
    lines = ["version: 1", *(f"{key}: {value}" for key, value in root.items())]
    return write(folder / "loom.yaml", "\n".join(lines) + "\n")


# --- Quel fichier -----------------------------------------------------------------


def test_the_env_file_next_to_the_config_is_read(tmp_path: Path) -> None:
    config = projet(tmp_path)
    write(tmp_path / ".env", f"LOOM_ESSAI_CLE={SECRET}\n")
    environ: dict[str, str] = {}
    report = load_env_file(config, environ=environ)
    assert environ == {"LOOM_ESSAI_CLE": SECRET}
    assert report == EnvFileReport(
        path=(tmp_path / ".env").resolve(), source="défaut", found=True, loaded=("LOOM_ESSAI_CLE",)
    )


def test_without_a_file_nothing_changes(tmp_path: Path) -> None:
    environ = {"LOOM_ESSAI_A": "1"}
    report = load_env_file(projet(tmp_path), environ=environ)
    assert environ == {"LOOM_ESSAI_A": "1"}
    assert not report.found
    assert report.describe() == f"aucun ({(tmp_path / '.env').resolve()})"


def test_neither_the_current_folder_nor_a_parent_is_searched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un projet rangé sous un dépôt n'hérite pas des clés de sa racine."""
    write(tmp_path / "depot" / ".env", "LOOM_ESSAI_RACINE=1\n")
    config = projet(tmp_path / "depot" / "examples" / "projet")
    write(tmp_path / "ailleurs" / ".env", "LOOM_ESSAI_COURANT=1\n")
    monkeypatch.chdir(tmp_path / "ailleurs")
    environ: dict[str, str] = {}
    assert not load_env_file(config, environ=environ).found
    assert environ == {}


def test_without_a_config_no_env_file_is_looked_for(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Chercher à côté d'un loom.yaml absent, ce serait lire le .env du dossier courant."""
    write(tmp_path / ".env", "LOOM_ESSAI_A=1\n")
    environ: dict[str, str] = {}
    assert load_env_file(tmp_path / "loom.yaml", environ=environ).path is None
    assert environ == {}
    assert main(["--config", str(tmp_path / "loom.yaml"), "validate"]) == 2
    assert "LOOM_ESSAI_A" not in os.environ
    assert "Fichier illisible" in capsys.readouterr().err


def test_env_file_in_the_config_wins_over_the_neighbour(tmp_path: Path) -> None:
    config = projet(tmp_path / "projet", env_file="../secrets/loom.env")
    write(tmp_path / "projet" / ".env", "LOOM_ESSAI_A=voisin\n")
    write(tmp_path / "secrets" / "loom.env", "LOOM_ESSAI_A=config\n")
    environ: dict[str, str] = {}
    report = load_env_file(config, environ=environ)
    assert environ == {"LOOM_ESSAI_A": "config"}
    assert (report.path, report.source) == ((tmp_path / "secrets" / "loom.env").resolve(), "config")


def test_the_file_given_by_the_caller_wins_over_the_config(tmp_path: Path) -> None:
    config = projet(tmp_path, env_file="loom.env")
    write(tmp_path / "loom.env", "LOOM_ESSAI_A=config\n")
    given = write(tmp_path / "autre.env", "LOOM_ESSAI_A=option\n")
    environ: dict[str, str] = {}
    report = load_env_file(config, env_file=given, environ=environ)
    assert environ == {"LOOM_ESSAI_A": "option"}
    assert report.source == "option"


def test_a_file_asked_for_and_missing_is_an_error(tmp_path: Path) -> None:
    config = projet(tmp_path, env_file="absent.env")
    with pytest.raises(ConfigError, match=r"introuvable \(env_file\)"):
        load_env_file(config, environ={})
    with pytest.raises(ConfigError, match=r"introuvable \(--env-file\)"):
        load_env_file(projet(tmp_path), env_file=tmp_path / "absent.env", environ={})


def test_env_file_must_be_a_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="env_file — un chemin de fichier est attendu"):
        load_env_file(projet(tmp_path, env_file=3), environ={})


# --- Ce qui est lu ----------------------------------------------------------------


def test_a_variable_already_defined_wins_even_empty(tmp_path: Path) -> None:
    write(tmp_path / ".env", "LOOM_ESSAI_A=fichier\nLOOM_ESSAI_B=fichier\nLOOM_ESSAI_C=fichier\n")
    environ = {"LOOM_ESSAI_A": "", "LOOM_ESSAI_B": "shell"}
    report = load_env_file(projet(tmp_path), environ=environ)
    assert environ == {"LOOM_ESSAI_A": "", "LOOM_ESSAI_B": "shell", "LOOM_ESSAI_C": "fichier"}
    assert report.loaded == ("LOOM_ESSAI_C",)
    assert report.kept == ("LOOM_ESSAI_A", "LOOM_ESSAI_B")


def test_the_file_is_read_as_written(tmp_path: Path) -> None:
    """Guillemets et ``export`` compris, commentaires sautés, ``${…}`` jamais remplacé."""
    write(
        tmp_path / ".env",
        "# clés du projet\n"
        'export LOOM_ESSAI_A="deux mots"   # commentaire\n'
        "LOOM_ESSAI_B='${LOOM_ESSAI_A} tel quel'\n"
        "LOOM_ESSAI_C=\n"
        "LOOM_ESSAI_SANS_VALEUR\n",
    )
    environ: dict[str, str] = {}
    load_env_file(projet(tmp_path), environ=environ)
    assert environ == {
        "LOOM_ESSAI_A": "deux mots",
        "LOOM_ESSAI_B": "${LOOM_ESSAI_A} tel quel",
        "LOOM_ESSAI_C": "",
    }


def test_an_unreadable_line_names_its_number_never_its_content(tmp_path: Path) -> None:
    write(tmp_path / ".env", f"LOOM_ESSAI_A=1\nceci porte {SECRET}\n")
    environ: dict[str, str] = {}
    with pytest.raises(ConfigError, match="ligne 2 : illisible") as raised:
        load_env_file(projet(tmp_path), environ=environ)
    assert SECRET not in str(raised.value)
    # Rien n'est posé d'un fichier refusé.
    assert environ == {}


# --- La configuration -------------------------------------------------------------


def test_the_loaded_config_carries_env_file_as_an_absolute_path(
    demo: ConfigFactory, tmp_path: Path
) -> None:
    config = load_config(demo(env_file="../secrets/loom.env"))
    assert config.env_file == (tmp_path.parent / "secrets" / "loom.env").resolve()
    assert load_config(demo()).env_file is None


def test_env_file_is_refused_in_any_profile(demo: ConfigFactory) -> None:
    """Il est lu avant le choix du profil : déclaré là, il ne le serait jamais."""
    path = demo(profiles={"prod": {"env_file": "prod.env"}})
    with pytest.raises(ConfigError, match=r"profiles\.prod\.env_file — refusé"):
        load_config(path)


# --- La ligne de commande ---------------------------------------------------------


def test_the_file_comes_before_the_config_and_its_profile(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(PROFILE_ENV, raising=False)
    config = demo()
    write(config.parent / ".env", f"LOOM_PROFILE=dev\nLOOM_ESSAI_CLE={SECRET}\n")
    assert main(["--config", str(config), "validate"]) == 0
    seen = capsys.readouterr()
    assert f"Profil     : dev (par {PROFILE_ENV})" in seen.out
    assert "(par défaut) — lues : LOOM_PROFILE, LOOM_ESSAI_CLE" in seen.out
    assert os.environ["LOOM_ESSAI_CLE"] == SECRET
    assert SECRET not in seen.out + seen.err


def test_a_key_from_the_file_mounts_a_real_model(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("anthropic")
    reel = {"id": "REEL", "sdk": "anthropic", "model": "claude-x", "api_key_env": "LOOM_ESSAI_CLE"}
    config = demo(
        models=[MODEL, reel],
        agents=[demo_agent(main={"model": "REEL", "system_file": "demo.md"})],
    )
    write(config.parent / ".env", f"LOOM_ESSAI_CLE={SECRET}\n")
    assert main(["--config", str(config), "--no-env-file", "validate"]) == 2
    assert "LOOM_ESSAI_CLE est absente ou vide" in capsys.readouterr().err
    assert main(["--config", str(config), "validate"]) == 0
    seen = capsys.readouterr()
    assert "1 agent(s) monté(s) sans erreur." in seen.out
    assert SECRET not in seen.out + seen.err


def test_no_env_file_reads_nothing(demo: ConfigFactory, capsys: pytest.CaptureFixture[str]) -> None:
    config = demo()
    write(config.parent / ".env", "LOOM_ESSAI_A=1\n")
    assert main(["--config", str(config), "--no-env-file", "validate"]) == 0
    assert "Env        : non lu (--no-env-file)" in capsys.readouterr().out
    assert "LOOM_ESSAI_A" not in os.environ


def test_the_env_file_option_replaces_the_default(
    demo: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = demo()
    write(config.parent / ".env", "LOOM_ESSAI_VOISIN=1\n")
    autre = write(tmp_path / "secrets" / "autre.env", "LOOM_ESSAI_AUTRE=1\n")
    assert main(["--config", str(config), "--env-file", str(autre), "validate"]) == 0
    assert f"Env        : {autre.resolve()} (par option)" in capsys.readouterr().out
    assert "LOOM_ESSAI_AUTRE" in os.environ
    assert "LOOM_ESSAI_VOISIN" not in os.environ


def test_the_two_options_together_are_refused(demo: ConfigFactory) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--config", str(demo()), "--env-file", "x.env", "--no-env-file", "validate"])
    assert raised.value.code == 2


def test_a_missing_file_asked_for_stops_the_command(
    demo: ConfigFactory, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    absent = tmp_path / "absent.env"
    assert main(["--config", str(demo()), "--env-file", str(absent), "validate"]) == 2
    assert "introuvable (--env-file)" in capsys.readouterr().err


def test_in_prod_variables_from_the_file_are_a_warning(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    config = demo()
    write(config.parent / ".env", f"LOOM_ESSAI_CLE={SECRET}\n")
    assert main(["--config", str(config), "--profile", "prod", "validate"]) == 0
    err = capsys.readouterr().err
    assert "Avertissement : variables lues dans" in err
    assert "en prod, préférer l'environnement du service" in err
    assert SECRET not in err
    # Rien ne vient du fichier : rien à dire.
    assert main(["--config", str(config), "--profile", "prod", "validate"]) == 0
    assert "Avertissement" not in capsys.readouterr().err


# --- La librairie -----------------------------------------------------------------


async def test_the_library_reads_nothing_by_itself(demo: ConfigFactory) -> None:
    config = demo()
    write(config.parent / ".env", "LOOM_ESSAI_A=1\n")
    async with Loom.from_config(config):
        pass
    assert "LOOM_ESSAI_A" not in os.environ
    # À la demande : les mêmes règles que la commande, sans ses options.
    assert load_env_file(config).loaded == ("LOOM_ESSAI_A",)
    assert os.environ["LOOM_ESSAI_A"] == "1"


# --- La relecture d'un process neuf -----------------------------------------------


def test_a_new_process_rereads_the_file_and_keeps_the_shell(tmp_path: Path) -> None:
    """Ce que la première lecture a posé est retiré, puis le fichier modifié est relu."""
    config = projet(tmp_path)
    write(tmp_path / ".env", "LOOM_ESSAI_A=v1\nLOOM_ESSAI_B=v1\nLOOM_ESSAI_SHELL=fichier\n")
    environ = {"LOOM_ESSAI_SHELL": "shell"}
    first = load_env_file(config, environ=environ)
    write(tmp_path / ".env", "LOOM_ESSAI_A=v2\nLOOM_ESSAI_SHELL=fichier\n")
    second = reload_env_file(config, first, environ=environ)
    assert environ == {"LOOM_ESSAI_SHELL": "shell", "LOOM_ESSAI_A": "v2"}
    assert (second.loaded, second.kept) == (("LOOM_ESSAI_A",), ("LOOM_ESSAI_SHELL",))


def test_a_disabled_reading_stays_disabled(tmp_path: Path) -> None:
    config = projet(tmp_path)
    write(tmp_path / ".env", "LOOM_ESSAI_A=1\n")
    environ: dict[str, str] = {}
    assert reload_env_file(config, DISABLED, environ=environ) is DISABLED
    assert environ == {}

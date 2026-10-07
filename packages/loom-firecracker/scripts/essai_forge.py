# SPDX-License-Identifier: Apache-2.0
"""Essai de la source ``forge`` sur une vraie VM : sans agent ni modèle.

    uv run python packages/loom-firecracker/scripts/essai_forge.py <dossier-vm>
    uv run python packages/loom-firecracker/scripts/essai_forge.py <dossier-vm> --garder

La source est fabriquée par sa fabrique, comme loom le fait au montage, avec
un catalogue dans un dossier temporaire effacé à la fin. L'essai joue deux
runs : le premier forge un outil (et voit refuser quatre outils mal faits),
l'appelle par ``call`` ; le second le trouve comme outil à part entière et
l'appelle directement. La VM est démarrée au premier appel si elle ne
tournait pas, et arrêtée à la fin si c'est la source qui l'a démarrée (pas
avec ``--garder``).

Ce qui est vérifié est annoncé puis tenu ou non ; le code de sortie vaut 1
si un point n'a pas tenu.
"""

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from loom_firecracker import Vm, VmError
from loom_firecracker.forge import CALL, FORGE, MANIFEST, forge_source
from loom_ia.core.model import RunId, SessionId, TenantId, ToolOutput
from loom_ia.core.ports import SourceContext, Tool, ToolContext, ToolError

TENANT = TenantId("default")
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"n": {"type": "integer", "minimum": 0}},
    "required": ["n"],
}
SOMME_CARRES = '''def somme_carres(n):
    """Somme des carrés de 0 à n."""
    print("calcul pour", n)
    return sum(i * i for i in range(n + 1))
'''

# Quatre outils mal faits : le premier refusé par l'hôte, les trois autres dans la VM.
MAL_FAITS: dict[str, tuple[str, str]] = {
    "sans_fonction": ("x = 1\n", "pas de fonction sans_fonction()"),
    "faux": ("def faux(n):\n    return n + 1\n", "attendu 14, obtenu 4"),
    "lourd": ("import numpy\n\ndef lourd(n):\n    return n\n", "import_error"),
    "leve": ("def leve(n):\n    raise ValueError('non')\n", 'File "leve.py", line 2, in leve'),
}


class Controle:
    """Ce que l'essai annonce, tenu ou non."""

    def __init__(self) -> None:
        self.ecarts: list[str] = []
        self.tenus = 0

    def tient(self, quoi: str, vrai: bool) -> None:
        if vrai:
            self.tenus += 1
        else:
            self.ecarts.append(quoi)
        print(f"  {quoi} : {'oui' if vrai else 'NON'}")

    def bilan(self) -> bool:
        print("\n=== Bilan ===")
        if not self.ecarts:
            print(f"  {self.tenus} vérifications, toutes tenues.")
            return True
        print(f"  {self.tenus} tenues, {len(self.ecarts)} non tenues :")
        for ecart in self.ecarts:
            print(f"    {ecart}")
        return False


def forging(name: str, code: str, expected: object = 14) -> dict[str, Any]:
    return {
        "name": name,
        "description": "Somme des carrés de 0 à n.",
        "input_schema": SCHEMA,
        "code": code,
        "examples": [
            {"arguments": {"n": 3}, "expected": expected},
            {"arguments": {"n": 0}, "expected": 0},
        ],
    }


def contexts(run: str) -> tuple[SourceContext, ToolContext]:
    session = SessionId(run)
    return (
        SourceContext(tenant_id=TENANT, session_id=session, run_id=RunId(run), agent="essai"),
        ToolContext(
            tenant_id=TENANT, session_id=session, run_id=RunId(run), call_id="c1", agent="essai"
        ),
    )


def montre(output: ToolOutput) -> None:
    for line in output.as_text.splitlines():
        print(f"    | {line}")


def named(tools: Sequence[Tool]) -> dict[str, Tool]:
    return {tool.spec.name: tool for tool in tools}


async def premier_run(source: Any, vm: Vm, controle: Controle, tournait: bool) -> None:
    source_ctx, tool_ctx = contexts(str(uuid.uuid7()))
    print("\n--- run 1 : ouverture ---")
    async with source.open(source_ctx) as tools:
        noms = [tool.spec.name for tool in tools]
        print(f"  outils : {noms}")
        controle.tient("forge et call, catalogue vide", noms == [FORGE, CALL])
        if not tournait:
            controle.tient("ouvrir la source ne démarre pas la VM", not vm.is_running())
        outils = named(tools)

        print("\n--- forge somme_carres (deux exemples) ---")
        debut = time.monotonic()
        out = await outils[FORGE].invoke(forging("somme_carres", SOMME_CARRES), tool_ctx)
        print(f"  rendu en {time.monotonic() - debut:.2f} s (boot compris si la VM dormait)")
        montre(out)
        controle.tient("somme_carres forgé", not out.is_error)
        controle.tient("la VM tourne", vm.is_running())

        for name, (code, said) in MAL_FAITS.items():
            print(f"\n--- forge {name} (mal fait) ---")
            try:
                out = await outils[FORGE].invoke(forging(name, code), tool_ctx)
                texte = out.as_text
                refuse = out.is_error
            except ToolError as exc:
                texte = exc.message
                refuse = True
            for line in texte.splitlines():
                print(f"    | {line}")
            controle.tient(f"{name} refusé, en disant « {said} »", refuse and said in texte)

        print("\n--- call somme_carres(n=10) ---")
        out = await outils[CALL].invoke({"name": "somme_carres", "arguments": {"n": 10}}, tool_ctx)
        montre(out)
        controle.tient("call rend 385", out.data == 385)
        controle.tient("et la sortie du code", "calcul pour 10" in out.as_text)


async def second_run(source: Any, controle: Controle) -> None:
    source_ctx, tool_ctx = contexts(str(uuid.uuid7()))
    print("\n--- run 2 : ouverture ---")
    async with source.open(source_ctx) as tools:
        noms = [tool.spec.name for tool in tools]
        print(f"  outils : {noms}")
        controle.tient(
            "somme_carres est un outil à part entière", noms == [FORGE, CALL, "somme_carres"]
        )
        if "somme_carres" in noms:
            outil = named(tools)["somme_carres"]
            print(f"  schéma : {json.dumps(outil.spec.input_schema)}")
            out = await outil.invoke({"n": 4}, tool_ctx)
            montre(out)
            controle.tient("somme_carres(n=4) rend 30", out.data == 30)


def inventaire(catalogue: Path) -> bool:
    """Montre le catalogue ; vrai s'il ne tient que somme_carres, son module et outil.json."""
    for path in sorted(catalogue.rglob("*")):
        print(f"  {path.relative_to(catalogue)}")
    client = catalogue / "default"
    dossier = client / "somme_carres"
    return (
        client.is_dir()
        and [p.name for p in client.iterdir()] == ["somme_carres"]
        and sorted(p.name for p in dossier.iterdir()) == [MANIFEST, "somme_carres.py"]
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dossier", type=Path, help="dossier de la VM (vm.env, run.sh)")
    parser.add_argument("--garder", action="store_true", help="laisse la VM tourner à la fin")
    options = parser.parse_args()
    controle = Controle()

    catalogue = Path(tempfile.mkdtemp(prefix="essai-forge-"))
    try:
        source = forge_source(
            name="forge",
            params={
                "vm_dir": str(options.dossier),
                "catalog_dir": str(catalogue),
                "limits": {"wall_ms": 10_000},
            },
            secrets={},
            base_dir=Path.cwd(),
        )
        vm = Vm.load(options.dossier)
        tournait = vm.is_running()
        print(f"source    : {source!r}")
        print(f"VM        : {vm.name}, {'tourne déjà' if tournait else 'arrêtée'}")

        try:
            await premier_run(source, vm, controle, tournait)
            print("\n--- catalogue ---")
            controle.tient(
                "un seul outil au catalogue, son module et outil.json", inventaire(catalogue)
            )
            await second_run(source, controle)
        except (VmError, OSError) as exc:
            controle.tient(f"l'essai va au bout ({type(exc).__name__}: {exc})", False)
        finally:
            if options.garder:
                print(f"\nVM laissée en marche (pid {vm.pid}).")
            else:
                print("\n--- démontage de la source ---")
                await source.aclose()
                if tournait:
                    controle.tient("la VM, qui tournait avant, tourne encore", vm.is_running())
                else:
                    controle.tient(
                        "la VM, démarrée par la source, est arrêtée", not vm.is_running()
                    )
    finally:
        shutil.rmtree(catalogue, ignore_errors=True)
    return 0 if controle.bilan() else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except (VmError, ValueError) as exc:
        print(f"\nÉchec : {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)

# SPDX-License-Identifier: Apache-2.0
"""Essai d'une vraie VM : boot, le verrou de run.sh, execd, des jobs, l'arrêt — hors de loom.

    uv run python packages/loom-firecracker/scripts/essai_vm.py <dossier-vm>
    uv run python packages/loom-firecracker/scripts/essai_vm.py <dossier-vm> --garder
    uv run python packages/loom-firecracker/scripts/essai_vm.py <dossier-vm> --attente 60

Le dossier est celui que ``make_vm.sh`` a construit (``vm.env``, ``run.sh``),
avec execd dans l'image. La VM n'est arrêtée à la fin que si ce script l'a
démarrée, et pas avec ``--garder``.

Ce qui est vérifié est annoncé puis tenu ou non ; le bilan le récapitule et
le code de sortie vaut 1 si un point n'a pas tenu. Les « constats » mesurent
ce que la plateforme fait, sans attente de résultat : ils renseignent les
choix de l'étape suivante.
"""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

from loom_firecracker import ExecdError, Execution, ProtocolError, Session, Vm, VmError

ESSAI = b"""import os, socket, sys, time


def principal(n):
    print("sortie", n)
    print("erreur", n, file=sys.stderr)
    with open(os.path.join(os.environ["OUT_DIR"], "carres.txt"), "w") as f:
        f.write(" ".join(str(i * i) for i in range(n)))
    return {"n": n, "somme": sum(range(n))}


def echoue():
    print("avant l'erreur")
    raise ValueError("valeur refusee")


def dort(secondes):
    time.sleep(secondes)


def reseau():
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=3).close()
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
    return "connecte"


def chemins():
    return {"code": os.environ["CODE_DIR"], "uid": os.getuid()}


def lit_chemin(chemin):
    try:
        return sorted(os.listdir(chemin))
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"
"""


class Controle:
    """Ce que l'essai annonce, tenu ou non ; et ce qu'il constate."""

    def __init__(self) -> None:
        self.ecarts: list[str] = []
        self.tenus = 0
        self.constats: list[str] = []

    def tient(self, quoi: str, vrai: bool) -> str:
        if vrai:
            self.tenus += 1
        else:
            self.ecarts.append(quoi)
        print(f"  {quoi} : {'oui' if vrai else 'NON'}")
        return "oui" if vrai else "NON"

    def constate(self, quoi: str) -> None:
        self.constats.append(quoi)
        print(f"  constat — {quoi}")

    def bilan(self) -> bool:
        print("\n=== Bilan ===")
        for constat in self.constats:
            print(f"  constat — {constat}")
        if not self.ecarts:
            print(f"  {self.tenus} vérifications, toutes tenues.")
            return True
        print(f"  {self.tenus} tenues, {len(self.ecarts)} non tenues :")
        for ecart in self.ecarts:
            print(f"    {ecart}")
        return False


def montre(execution: Execution) -> None:
    """Le compte rendu d'un job, en entier."""
    print(f"    ok={execution.ok} durée={execution.duration_ms} ms")
    if execution.error is not None:
        print(f"    erreur : {execution.error.kind} — {execution.error.message}")
        if execution.error.detail:
            print(f"    détail : {json.dumps(dict(execution.error.detail), ensure_ascii=False)}")
    print(f"    résultat : {execution.result!r}")
    print(f"    stdout : {execution.stdout!r}")
    print(f"    stderr : {execution.stderr!r}")
    for output in execution.outputs:
        print(f"    produit : {output.path} ({output.size} o, sha256 {output.sha256[:16]}…)")


async def ouvre(vm: Vm, port: int, attente: float) -> Session:
    reader, writer = await vm.connect(port, wait=attente)
    return await Session.open(reader, writer)


async def essai(
    vm: Vm, port: int, attente: float, controle: Controle, lancement: float | None
) -> None:
    print("\n--- un second run.sh pendant que la VM tourne ---")
    pid = vm.pid
    refuse = await asyncio.create_subprocess_exec(
        str(vm.directory / "run.sh"),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    _, dit = await refuse.communicate()
    print(f"  code {refuse.returncode} : {dit.decode(errors='replace').strip()}")
    controle.tient("refusé, code 1", refuse.returncode == 1)
    controle.tient(f"vm.lock garde le pid ({pid})", pid is not None and vm.pid == pid)
    controle.tient("le refus nomme ce pid", f"(pid {pid})" in dit.decode(errors="replace"))
    controle.tient("la VM tourne toujours", vm.is_running())

    print("\n--- hello ---")
    debut = time.monotonic()
    session = await ouvre(vm, port, attente)
    print(f"  execd joint en {time.monotonic() - debut:.2f} s")
    if lancement is not None:
        print(f"  boot : du lancement de run.sh à execd, {time.monotonic() - lancement:.2f} s")
    hello = session.hello
    print(f"  Python invité {hello.python}, runner {hello.runner}, session {hello.session_id}")
    print(f"  plafonds : {dict(hello.ceilings)}")
    controle.tient("protocole 1", hello.protocol == 1)

    async with session:
        await session.put_code("essai.py", ESSAI)

        print("\n--- un job qui réussit : essai:principal(n=5) ---")
        fait = await session.exec("essai:principal", args={"n": 5})
        montre(fait)
        controle.tient("le job réussit", fait.ok)
        controle.tient("sa valeur revient", fait.result == {"n": 5, "somme": 10})
        controle.tient("stdout revient", fait.stdout == "sortie 5\n")
        controle.tient("stderr revient", fait.stderr == "erreur 5\n")
        produits = [output.path for output in fait.outputs]
        controle.tient("carres.txt est au manifeste", produits == ["carres.txt"])
        if produits == ["carres.txt"]:
            data = await session.get_file("carres.txt")
            print(f"    carres.txt rapatrié : {data!r}")
            controle.tient("son contenu revient", data == b"0 1 4 9 16")
            controle.tient(
                "son empreinte est celle du manifeste",
                hashlib.sha256(data).hexdigest() == fait.outputs[0].sha256,
            )

        print("\n--- un job qui lève : essai:echoue() ---")
        fait = await session.exec("essai:echoue")
        montre(fait)
        kind = fait.error.kind if fait.error is not None else None
        controle.tient("l'échec est un compte rendu, genre tool_raised", kind == "tool_raised")
        trace = json.dumps(dict(fait.error.detail)) if fait.error is not None else ""
        controle.tient("la pile nomme essai.py", "essai.py" in trace)
        controle.tient("la sortie d'avant l'erreur revient", fait.stdout == "avant l'erreur\n")

        print("\n--- un job trop long : essai:dort(5), wall_ms 1000 ---")
        debut = time.monotonic()
        fait = await session.exec("essai:dort", args={"secondes": 5}, limits={"wall_ms": 1000})
        duree = time.monotonic() - debut
        montre(fait)
        kind = fait.error.kind if fait.error is not None else None
        controle.tient("arrêté en genre timeout", kind == "timeout")
        controle.tient(f"avant 4 s (rendu en {duree:.2f} s)", duree < 4)

        print("\n--- réseau depuis l'invité : essai:reseau() ---")
        fait = await session.exec("essai:reseau", limits={"wall_ms": 10_000})
        montre(fait)
        controle.tient("pas de réseau dans l'invité", fait.ok and fait.result != "connecte")

        print("\n--- deux sessions : l'une voit-elle le dossier de l'autre ? ---")
        fait = await session.exec("essai:chemins")
        voisine = await ouvre(vm, port, attente)
        async with voisine:
            await voisine.put_code("essai.py", ESSAI)
            vu = fait.result
            if fait.ok and isinstance(vu, dict):
                infos = cast(dict[str, object], vu)
                code = str(infos.get("code", ""))
                lu = await voisine.exec("essai:lit_chemin", args={"chemin": code})
                print(f"    uid du job : {infos.get('uid')!r}")
                print(f"    la voisine lit {code} : {lu.result!r}")
                verdict = "oui" if isinstance(lu.result, list) else "non"
                controle.constate(f"une session lit le dossier de code d'une autre : {verdict}")
            else:
                controle.tient("essai:chemins réussit", False)

    print("\n--- fermer une session pendant son job (30 s, wall_ms 20000) ---")
    abandon = await ouvre(vm, port, attente)
    await abandon.put_code("essai.py", ESSAI)
    tache = asyncio.create_task(
        abandon.exec("essai:dort", args={"secondes": 30}, limits={"wall_ms": 20_000})
    )
    await asyncio.sleep(0.5)
    await abandon.close()
    try:
        await tache
    except ProtocolError:
        pass
    async with await ouvre(vm, port, attente) as suivante:
        await suivante.put_code("essai.py", ESSAI)
        debut = time.monotonic()
        fait = await suivante.exec("essai:principal", args={"n": 1})
        attendu = time.monotonic() - debut
    controle.tient("le job suivant passe", fait.ok)
    # execd n'exécute qu'un job à la fois : sans l'arrêt du job abandonné, le
    # suivant attendrait son wall_ms (20 s).
    controle.tient(
        f"il n'attend pas le job abandonné (rendu en {attendu:.2f} s)", fait.ok and attendu < 3
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dossier", type=Path, help="dossier de la VM (vm.env, run.sh)")
    parser.add_argument("--garder", action="store_true", help="laisse la VM tourner à la fin")
    parser.add_argument("--attente", type=float, default=60.0, help="délai du boot, en s")
    options = parser.parse_args()
    controle = Controle()

    vm = Vm.load(options.dossier)
    print(f"VM        : {vm.name} (CID {vm.cid})")
    print(f"dossier   : {vm.directory}")
    print(f"API       : {vm.api_sock}")
    print(f"vsock     : {vm.vsock_uds}")
    print(f"execd     : port {vm.execd_port}, source {vm.execd_src or '(vide)'}")
    print(f"/dev/kvm  : {'accessible' if os.access('/dev/kvm', os.R_OK | os.W_OK) else 'NON'}")
    if vm.execd_port is None:
        print("\nEXECD_PORT absent de vm.env : reconstruis la VM avec make_vm.sh.")
        return 1
    if not vm.execd_src:
        print("  /!\\ EXECD_SRC vide : l'image n'embarque peut-être pas execd.")

    tournait = vm.is_running()
    vivante = await vm.is_alive()
    print(f"verrou    : {'tenu, pid ' + str(vm.pid) if tournait else 'libre'}")
    print(f"API       : {'répond' if vivante else 'muette'}")
    if vivante and not tournait:
        print("\nUn VMM répond sans tenir le verrou : arrête-le d'abord (pgrep -af firecracker).")
        return 1

    print("\n--- démarrage ---")
    debut = time.monotonic()
    demarree = await vm.ensure_started(wait=options.attente)
    if demarree:
        print(f"  démarrée par l'essai, API en {time.monotonic() - debut:.2f} s (pid {vm.pid})")
    else:
        print("  tournait déjà : l'essai la laissera tourner")

    try:
        await essai(vm, vm.execd_port, options.attente, controle, debut if demarree else None)
    except (ExecdError, ProtocolError, TimeoutError, VmError) as exc:
        controle.tient(f"l'essai va au bout ({type(exc).__name__}: {exc})", False)
    finally:
        if demarree and not options.garder:
            print("\n--- arrêt ---")
            debut = time.monotonic()
            maniere = await vm.stop()
            print(f"  arrêtée en {time.monotonic() - debut:.2f} s par {maniere}")
            controle.tient("la VM est arrêtée", not await vm.is_alive() and not vm.is_running())
            controle.tient("arrêt propre (console ou acpi)", maniere in ("console", "acpi"))
        elif demarree:
            print(f"\nVM laissée en marche (pid {vm.pid}).")

    return 0 if controle.bilan() else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except VmError as exc:
        print(f"\nÉchec : {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)

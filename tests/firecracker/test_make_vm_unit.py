# SPDX-License-Identifier: Apache-2.0
"""``make_vm.sh`` : les unités systemd qu'il écrit pour ``execd`` (SBX-2, SBX-11).

Le script produit chaque unité par un heredoc ; le test la relit telle qu'il l'écrirait, les
variables du shell remplacées par des valeurs d'exemple.
"""

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "firecracker" / "make_vm.sh"


def _unit(marker: str = "UNIT") -> dict[str, dict[str, str]]:
    """Les sections d'une unité : ``{section: {clé: valeur}}`` (dernière valeur d'une clé)."""
    pattern = rf"<<{marker}\n(.*?)^{marker}$"
    found = re.search(pattern, SCRIPT.read_text(encoding="utf-8"), re.S | re.M)
    assert found is not None, f"heredoc {marker} absent de make_vm.sh"
    text = re.sub(r"\$\{[A-Z_]+\}", "1500", found.group(1))
    sections: dict[str, dict[str, str]] = {}
    current = ""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections[current] = {}
        else:
            key, _, value = line.partition("=")
            sections[current][key] = value
    return sections


def test_the_unit_never_gives_up_restarting_execd() -> None:
    """Le plafond par défaut (5 départs en 10 s) tombe en 1 s à ``RestartSec=200ms``."""
    unit = _unit()
    assert unit["Service"]["Restart"] == "always"
    # Dans [Unit] : depuis systemd 230 la clé n'est plus lue ailleurs.
    assert unit["Unit"]["StartLimitIntervalSec"] == "0"
    assert "StartLimitIntervalSec" not in unit["Service"]


def test_the_unit_sets_its_own_descriptor_limit() -> None:
    limit = int(_unit()["Service"]["LimitNOFILE"])
    assert limit >= 4096


def test_proc_is_remounted_with_hidepid_before_execd_starts() -> None:
    """Un job de l'uid commun ne doit pas lire la ligne de commande des autres processus."""
    hidepid = _unit("HIDEPID")
    assert hidepid["Service"]["ExecStart"] == "/bin/mount -o remount,hidepid=2 /proc"
    assert hidepid["Service"]["Type"] == "oneshot"
    assert hidepid["Unit"]["Before"] == "execd.service"
    execd = _unit()["Unit"]
    # « Requires » : si le remontage échoue, execd ne démarre pas avec un /proc ouvert.
    assert "procfs-hidepid.service" in execd["Requires"].split()
    assert "procfs-hidepid.service" in execd["After"].split()

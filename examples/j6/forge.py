# SPDX-License-Identifier: Apache-2.0
"""Phase 6.4c : un agent qui forge ses outils et les exécute dans une microVM Firecracker.

    uv run python examples/j6/forge.py --vm ~/temp                 # tous les cas
    uv run python examples/j6/forge.py --vm ~/temp --cas forger
    uv run python examples/j6/forge.py --vm ~/temp --cas corriger
    uv run python examples/j6/forge.py --vm ~/temp --cas rejeu
    uv run python examples/j6/forge.py --vm ~/temp --cas bornes
    uv run --env-file .env --extra anthropic \\
        python examples/j6/forge.py --vm ~/temp --reel
    uv run python examples/j6/forge.py --vm ~/temp --garder        # dossier gardé

``--vm`` : le dossier d'une VM construite par ``firecracker/make_vm.sh``, avec
execd dans son image. Il faut une vraie VM dans les deux modes — c'est elle
que l'exemple montre — ; sans elle, il dit quoi passer et sort en code 2. La
source ``forge`` (``loom_ia.adapters.firecracker``, point d'entrée ``forge``) la
démarre au premier appel qui s'exécute dans la VM s'il le faut, et l'arrête
quand l'instance loom se ferme, si c'est elle qui l'a démarrée : chaque cas
vérifie qu'elle ne tourne plus une fois son instance fermée.

La config, le catalogue des outils forgés et le journal sont écrits dans un
dossier temporaire, effacé à la fin ; ``--garder`` le laisse en place et dit
où il est (les échanges bruts du modèle sont dans le journal, aux événements
``model.exchanged``). L'agent : ``atelier``, un orchestrateur
simulé, ou en ``--reel`` ``atelier_reel``, MiniMax-M3 comme dans la config de
J4 (clé dans ``M3_API_KEY``). Il voit ``forge__forge``, ``forge__call``, et
les outils déjà forgés (``forge__total_ttc``…).

* **forger** : le run 1 forge ``total_ttc`` — le total HT, la TVA et le TTC
  d'un devis, arrondis au centime — et l'appelle par ``forge__call`` sur le
  devis D-2026-042 ; le run 2 le trouve parmi ses outils (``forge__total_ttc``)
  et l'appelle directement sur D-2026-043. Les outils proposés au modèle se
  lisent dans les échanges bruts du journal.
* **corriger** : un premier jet de ``tva_par_taux`` oublie les quantités ; ses
  exemples échouent dans la VM et l'outil est refusé, en disant l'écart ; le
  jet corrigé est accepté et appelé.
* **rejeu** : le run 1 de ``forger`` rejoué à l'identique, dans une instance
  neuve, sans rien exécuter — la VM n'est pas démarrée. ``forger`` est joué
  d'abord s'il n'est pas demandé.
* **bornes** : ``bavard`` écrit 20 000 caractères, le modèle n'en voit qu'un
  début et une fin ; ``lent`` dépasse son ``wall_ms``, l'appel revient en
  erreur ``timeout`` et le run continue. En ``--reel``, sauté : il éprouve la
  source, pas le modèle.

En ``--reel``, MiniMax écrit lui-même ses outils (code, schéma, exemples) :
ce qu'il rend est montré, pas exigé — les refus de ``corriger`` aussi, avec
leur raison. Ce qui dépend d'un outil forgé n'est exigé que s'il l'a forgé ;
sinon la partie est sautée, et le bilan le dit. Ce qui tient à la source et
non au modèle reste exigé : la VM arrêtée une fois l'instance fermée.
"""

import argparse
import asyncio
import contextlib
import json
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any, cast

import yaml

from loom_ia.access import Loom, RunResult
from loom_ia.adapters.firecracker import Vm, VmError
from loom_ia.adapters.models import ModelConfigError
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import Event, ModelExchanged, ToolCalled, ToolCompleted
from loom_ia.core.model import RunStatus

J4 = Path(__file__).parent.parent / "j4" / "relance"
CAS = ("forger", "corriger", "rejeu", "bornes")
# Ce que l'outil d'un job peut tenir dans la VM (ms) : de quoi voir un timeout vite.
WALL_MS = 5000

DEVIS_042 = [
    {"designation": "Chauffe-eau 200 L", "quantite": 1, "prix_ht": 1150, "tva": 10},
    {"designation": "Main-d'œuvre (heures)", "quantite": 4, "prix_ht": 55, "tva": 10},
    {"designation": "Déplacement", "quantite": 1, "prix_ht": 40, "tva": 20},
]
TOTAL_042 = {"total_ht": 1410.0, "tva": 145.0, "total_ttc": 1555.0}
DEVIS_043 = [
    {"designation": "Robinet thermostatique", "quantite": 2, "prix_ht": 89.9, "tva": 10},
    {"designation": "Main-d'œuvre (heures)", "quantite": 1.5, "prix_ht": 55, "tva": 10},
    {"designation": "Joints", "quantite": 3, "prix_ht": 2.35, "tva": 20},
]
TOTAL_043 = {"total_ht": 269.35, "tva": 27.64, "total_ttc": 296.99}
TVA_042 = {"10": 137.0, "20": 8.0}

LIGNES: dict[str, Any] = {
    "type": "array",
    "minItems": 1,
    "items": {
        "type": "object",
        "properties": {
            "designation": {"type": "string"},
            "quantite": {"type": "number", "exclusiveMinimum": 0},
            "prix_ht": {"type": "number", "minimum": 0},
            "tva": {"type": "number", "minimum": 0},
        },
        "required": ["quantite", "prix_ht", "tva"],
    },
}

TOTAL_TTC = '''from decimal import ROUND_HALF_UP, Decimal


def total_ttc(lignes):
    """Total HT, TVA et TTC d'un devis, arrondis au centime (au demi supérieur)."""
    centime = Decimal("0.01")
    ht = tva = Decimal(0)
    for ligne in lignes:
        montant = Decimal(str(ligne["quantite"])) * Decimal(str(ligne["prix_ht"]))
        ht += montant
        tva += montant * Decimal(str(ligne["tva"])) / 100
    ht = ht.quantize(centime, ROUND_HALF_UP)
    tva = tva.quantize(centime, ROUND_HALF_UP)
    return {"total_ht": float(ht), "tva": float(tva), "total_ttc": float(ht + tva)}
'''

# Le premier jet de tva_par_taux : la TVA d'une ligne y oublie sa quantité.
TVA_JET = '''from decimal import ROUND_HALF_UP, Decimal


def tva_par_taux(lignes):
    """Montant de TVA par taux, arrondi au centime."""
    par_taux = {}
    for ligne in lignes:
        taux = Decimal(str(ligne["tva"]))
        montant = Decimal(str(ligne["prix_ht"])) * taux / 100
        par_taux[taux] = par_taux.get(taux, Decimal(0)) + montant
    return {
        format(t.normalize(), "f"): float(m.quantize(Decimal("0.01"), ROUND_HALF_UP))
        for t, m in sorted(par_taux.items())
    }
'''
TVA_CORRIGE = TVA_JET.replace(
    'montant = Decimal(str(ligne["prix_ht"])) * taux / 100',
    'montant = Decimal(str(ligne["quantite"])) * Decimal(str(ligne["prix_ht"])) * taux / 100',
)

BAVARD = '''def bavard(n):
    """Écrit n caractères sur la sortie, et rend n."""
    print("x" * n)
    return n
'''

LENT = '''import time


def lent(secondes):
    """Attend autant de secondes, et les rend."""
    time.sleep(secondes)
    return secondes
'''


def forgeage(
    nom: str, description: str, schema: dict[str, Any], code: str, exemples: list[Any]
) -> dict[str, Any]:
    return {
        "name": nom,
        "description": description,
        "input_schema": schema,
        "code": code,
        "examples": exemples,
    }


FORGE_TOTAL = forgeage(
    "total_ttc",
    "Total HT, TVA et TTC d'un devis à partir de ses lignes (quantité, prix HT, taux de TVA "
    "en %), arrondis au centime.",
    {"type": "object", "properties": {"lignes": LIGNES}, "required": ["lignes"]},
    TOTAL_TTC,
    [
        {
            "arguments": {"lignes": [{"quantite": 1, "prix_ht": 100, "tva": 20}]},
            "expected": {"total_ht": 100, "tva": 20, "total_ttc": 120},
        },
        {
            "arguments": {"lignes": [{"quantite": 3, "prix_ht": 19.99, "tva": 5.5}]},
            "expected": {"total_ht": 59.97, "tva": 3.3, "total_ttc": 63.27},
        },
    ],
)
EXEMPLES_TVA = [
    {
        "arguments": {
            "lignes": [
                {"quantite": 2, "prix_ht": 50, "tva": 10},
                {"quantite": 1, "prix_ht": 40, "tva": 20},
            ]
        },
        "expected": {"10": 10.0, "20": 8.0},
    }
]
SCHEMA_LIGNES = {"type": "object", "properties": {"lignes": LIGNES}, "required": ["lignes"]}
FORGE_TVA_JET = forgeage(
    "tva_par_taux", "Montant de TVA par taux d'un devis.", SCHEMA_LIGNES, TVA_JET, EXEMPLES_TVA
)
FORGE_TVA = {**FORGE_TVA_JET, "code": TVA_CORRIGE}
FORGE_BAVARD = forgeage(
    "bavard",
    "Écrit n caractères sur la sortie.",
    {"type": "object", "properties": {"n": {"type": "integer", "minimum": 0}}, "required": ["n"]},
    BAVARD,
    [{"arguments": {"n": 3}, "expected": 3}],
)
FORGE_LENT = forgeage(
    "lent",
    "Attend un nombre de secondes.",
    {
        "type": "object",
        "properties": {"secondes": {"type": "number", "minimum": 0}},
        "required": ["secondes"],
    },
    LENT,
    [{"arguments": {"secondes": 0}, "expected": 0}],
)

DEMANDE_042 = (
    "Forge un outil total_ttc qui calcule le total HT, la TVA et le total TTC d'un devis à "
    "partir de ses lignes (quantité, prix HT, taux de TVA en %), arrondis au centime, puis "
    "calcule le devis D-2026-042 : " + json.dumps(DEVIS_042, ensure_ascii=False)
)
DEMANDE_043 = "Avec ton outil total_ttc, calcule le devis D-2026-043 : " + json.dumps(
    DEVIS_043, ensure_ascii=False
)
DEMANDE_TVA = (
    "Forge l'outil tva_par_taux à partir de ce premier jet, tel quel d'abord, avec cet exemple ; "
    "s'il est refusé, corrige-le et forge-le à nouveau. Puis donne la TVA par taux du devis "
    f"D-2026-042 : {json.dumps(DEVIS_042, ensure_ascii=False)}\n\nPremier jet :\n{TVA_JET}\n"
    f"Exemple : {json.dumps(EXEMPLES_TVA[0], ensure_ascii=False)}"
)
DEMANDE_BORNES = "Forge bavard et lent, puis appelle bavard(n=20000) et lent(secondes=30)."

SYSTEME = (
    "Tu aides un artisan à chiffrer ses devis. Pour forger un outil, appelle forge__forge : "
    "son code est un module Python (bibliothèque standard seulement) qui définit une fonction "
    "du nom de l'outil, recevant par nom les propriétés de input_schema et rendant une valeur "
    "JSON ; donne au moins deux exemples dont tu es sûr du résultat. Si l'outil est refusé, "
    "lis la raison, corrige, et forge-le à nouveau. Un outil qu'on vient de forger s'appelle "
    "par forge__call ; s'il est déjà parmi tes outils (forge__<nom>), appelle-le directement. "
    "Les montants s'arrondissent au centime, au demi supérieur. Réponds en une phrase."
)


def appel(nom: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"name": nom, "arguments": arguments}


# Le script de l'orchestrateur simulé : chaque demande choisit ses réponses.
SCRIPT: list[dict[str, Any]] = [
    {
        "with_text": "D-2026-042 :",
        "without_text": "tva_par_taux",
        "text": "Je forge total_ttc.",
        "tool_calls": [appel("forge__forge", FORGE_TOTAL)],
    },
    {
        "with_text": "D-2026-042 :",
        "without_text": "tva_par_taux",
        "text": "Je calcule le devis.",
        "tool_calls": [
            appel("forge__call", {"name": "total_ttc", "arguments": {"lignes": DEVIS_042}})
        ],
    },
    {
        "with_text": "D-2026-042 :",
        "without_text": "tva_par_taux",
        "text": "Le devis D-2026-042 fait 1 410,00 € HT, 145,00 € de TVA, 1 555,00 € TTC.",
    },
    {
        "with_text": "D-2026-043",
        "text": "J'utilise total_ttc.",
        "tool_calls": [appel("forge__total_ttc", {"lignes": DEVIS_043})],
    },
    {
        "with_text": "D-2026-043",
        "text": "Le devis D-2026-043 fait 269,35 € HT, 27,64 € de TVA, 296,99 € TTC.",
    },
    {
        "with_text": "tva_par_taux",
        "text": "Je forge le premier jet.",
        "tool_calls": [appel("forge__forge", FORGE_TVA_JET)],
    },
    {
        "with_text": "tva_par_taux",
        "text": "Refusé : la quantité manquait. Je corrige.",
        "tool_calls": [appel("forge__forge", FORGE_TVA)],
    },
    {
        "with_text": "tva_par_taux",
        "text": "Je l'appelle.",
        "tool_calls": [
            appel("forge__call", {"name": "tva_par_taux", "arguments": {"lignes": DEVIS_042}})
        ],
    },
    {
        "with_text": "tva_par_taux",
        "text": "TVA du devis D-2026-042 : 137,00 € à 10 %, 8,00 € à 20 %.",
    },
    {
        "with_text": "bavard",
        "text": "Je forge les deux.",
        "tool_calls": [appel("forge__forge", FORGE_BAVARD), appel("forge__forge", FORGE_LENT)],
    },
    {
        "with_text": "bavard",
        "text": "Je les appelle.",
        "tool_calls": [
            appel("forge__call", {"name": "bavard", "arguments": {"n": 20000}}),
            appel("forge__call", {"name": "lent", "arguments": {"secondes": 30}}),
        ],
    },
    {"with_text": "bavard", "text": "bavard a écrit 20 000 caractères ; lent a dépassé son délai."},
]


def titre(texte: str) -> None:
    print(f"\n=== {texte} ===")


class Controle:
    """Ce que chaque essai devait rendre : l'exemple l'annonce, puis le vérifie."""

    def __init__(self) -> None:
        self.ecarts: list[str] = []
        self.sautes: list[str] = []
        self.parties: list[str] = []

    def tient(self, quoi: str, vrai: bool) -> str:
        if not vrai:
            self.ecarts.append(quoi)
        return "oui" if vrai else "NON"

    def saute(self, quoi: str, *, partie: bool = False) -> None:
        """Un cas, ou une partie d'un cas, qui n'a pas pu être jouée : à dire,
        sinon le bilan mentirait. Une partie sautée laisse le cas joué."""
        (self.parties if partie else self.sautes).append(quoi)

    def bilan(self, joues: int) -> bool | None:
        """Vrai si tout a tenu, faux sinon ; ``None`` si aucun cas n'a été joué."""
        for saute in self.sautes:
            print(f"\nCas sauté : {saute}")
        for partie in self.parties:
            print(f"\nPartie sautée : {partie}")
        if joues == len(self.sautes):
            print("\nAucun cas joué : l'exemple n'a rien éprouvé.")
            return None
        if not self.ecarts:
            sautes = self.sautes or self.parties
            dit = "Chaque essai joué a rendu" if sautes else "Chaque essai a rendu"
            print(f"\n{dit} ce que l'exemple annonçait.")
            return True
        print("\nUn essai au moins n'a pas rendu ce qui était annoncé :")
        for ecart in self.ecarts:
            print(f"  {ecart}")
        return False


def enonce(quoi: str, texte: str, largeur: int = 96) -> None:
    """Imprime un message long sur plusieurs lignes, sans rien en couper."""
    marge = " " * len(quoi)
    lignes = [
        morceau
        for bloc in texte.splitlines()
        for morceau in textwrap.wrap(bloc, largeur - len(quoi), replace_whitespace=False) or [""]
    ]
    for numero, ligne in enumerate(lignes or [""]):
        print(f"{quoi if numero == 0 else marge}{ligne}")


# --- La config, et ce que le journal dit d'un run ---------------------------------------


def ecrit_config(dossier: Path, vm: Path, *, reel: bool) -> Path:
    """La config : la source forge, et l'agent du mode (un seul : il monte sans clé)."""
    base = dossier / "atelier"
    (base / "agents").mkdir(parents=True)
    m3 = load_config(J4 / "loom.yaml").model_spec("M3_MAIN")
    simule: dict[str, Any] = {
        "id": "FAKE_MAIN",
        "sdk": "fake",
        "model": "fake-main",
        "params": {"script": SCRIPT},
    }
    config: dict[str, Any] = {
        "version": 1,
        "models": [m3.model_dump(mode="json", exclude_defaults=True) if reel else simule],
        "tool_sources": [
            {
                "name": "forge",
                "entry_point": "forge",
                "params": {
                    "vm_dir": str(vm),
                    "catalog_dir": "catalogue",
                    "limits": {"wall_ms": WALL_MS},
                },
            }
        ],
        "storage": {"events": {"backend": "jsonl", "path": "data"}},
        "telemetry": {"logging": {"level": "WARNING"}, "capture": {"raw_exchanges": True}},
    }
    (base / "loom.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    nom, modele = ("atelier_reel", m3.id) if reel else ("atelier", "FAKE_MAIN")
    agent = {
        "name": nom,
        "description": "Forge ses outils de chiffrage et les exécute dans une VM.",
        "main": {"model": modele, "system": SYSTEME},
        "max_iterations": 8,
        "tools": [{"source": "forge"}],
    }
    (base / "agents" / f"{nom}.yaml").write_text(
        yaml.safe_dump(agent, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return base / "loom.yaml"


def proposes(events: list[Event]) -> list[str]:
    """Les outils proposés au modèle dans le premier appel du run, lus dans l'échange brut."""
    for event in events:
        if isinstance(event.payload, ModelExchanged) and event.payload.request_body:
            corps = cast(dict[str, Any], json.loads(event.payload.request_body))
            outils = cast(list[dict[str, Any]], corps.get("tools") or [])
            return [str(o.get("name") or o.get("function", {}).get("name")) for o in outils]
    return []


def appels(events: list[Event]) -> list[tuple[ToolCalled, ToolCompleted | None]]:
    """Chaque appel d'outil du run et son résultat."""
    rendus = {e.payload.call_id: e.payload for e in events if isinstance(e.payload, ToolCompleted)}
    return [
        (e.payload, rendus.get(e.payload.call_id))
        for e in events
        if isinstance(e.payload, ToolCalled)
    ]


def raconte(result: RunResult, events: list[Event]) -> None:
    print(f"  statut : {result.status.value}" + (f" ({result.error})" if result.error else ""))
    print(f"  outils proposés : {', '.join(proposes(events)) or '(échange brut absent)'}")
    for fait, rendu in appels(events):
        arguments = dict(fait.arguments)
        if isinstance(arguments.get("code"), str):
            arguments["code"] = f"<{len(str(arguments['code']).splitlines())} lignes>"
        enonce(f"  → {fait.tool_name} ", json.dumps(arguments, ensure_ascii=False))
        if rendu is not None:
            marque = "erreur" if rendu.output.is_error else "rendu"
            enonce(f"    {marque} : ", rendu.output.as_text or "(vide)")
    enonce("  texte  : ", result.text or "(vide)")


def forge_reussi(events: list[Event], nom: str) -> bool:
    return any(
        fait.tool_name == "forge__forge"
        and fait.arguments.get("name") == nom
        and rendu is not None
        and not rendu.output.is_error
        for fait, rendu in appels(events)
    )


def rendus_de(events: list[Event], outil: str) -> list[ToolCompleted]:
    return [rendu for fait, rendu in appels(events) if fait.tool_name == outil and rendu]


# --- Les cas ------------------------------------------------------------------------


class Atelier:
    """Ce que les cas partagent : la config, la VM, l'agent, et le run 1 de forger."""

    def __init__(self, args: argparse.Namespace, dossier: Path, vm: Vm) -> None:
        self.args = args
        self.reel: bool = args.reel
        self.fichier = ecrit_config(dossier, vm.directory, reel=self.reel)
        self.catalogue = self.fichier.parent / "catalogue" / "default"
        self.vm = vm
        self.agent = "atelier_reel" if self.reel else "atelier"
        self.premier: RunResult | None = None
        self.vm_avant = vm.is_running()

    async def run(self, loom: Loom, numero: str, demande: str) -> tuple[RunResult, list[Event]]:
        titre(f"Run {numero} de {self.agent}")
        enonce("  demande : ", demande)
        result = await loom.run(self.agent, demande)
        events = await loom.events(result.run_id, session_id=result.session_id)
        raconte(result, events)
        return result, events


async def cas_forger(atelier: Atelier, controle: Controle) -> None:
    reel = atelier.reel
    print(f"  VM {atelier.vm.name} : {'tourne déjà' if atelier.vm_avant else 'arrêtée'}")
    async with Loom(load_config(atelier.fichier)) as loom:
        premier, events = await atelier.run(loom, "1", DEMANDE_042)
        atelier.premier = premier
        if not atelier.vm_avant:
            tourne = atelier.vm.is_running()
            if not appels(events):
                print(
                    "  aucun outil appelé, et la VM n'a pas eu à démarrer : "
                    + controle.tient("forger : la VM a démarré sans appel d'outil", not tourne)
                )
            elif not reel:
                print(
                    "  la VM a été démarrée par la source, au premier appel exécuté dans la VM : "
                    + controle.tient("forger : la VM ne tourne pas après le run 1", tourne)
                )
            else:
                # Un refus de l'hôte n'exécute rien : en réel, on ne sait pas
                # d'avance si un appel a atteint la VM.
                print(f"  VM après le run 1 : {'tourne' if tourne else 'arrêtée'}")
        forge = forge_reussi(events, "total_ttc")
        offerts = proposes(events)
        print(
            "  au run 1, le modèle voit forge__forge et forge__call, pas encore total_ttc : "
            + controle.tient(
                "forger : les outils proposés au run 1 ne sont pas ceux annoncés",
                offerts == ["forge__forge", "forge__call"],
            )
        )
        if not reel:
            calcul = rendus_de(events, "forge__call")
            print(
                f"  total_ttc forgé, puis appelé par forge__call : {TOTAL_042} : "
                + controle.tient(
                    "forger : le run 1 ne rend pas ce qui est annoncé",
                    premier.status == RunStatus.COMPLETED
                    and forge
                    and [c.output.data for c in calcul] == [TOTAL_042],
                )
            )
        if forge:
            await suite_forger(atelier, loom, controle)
        else:
            print("  total_ttc n'a pas été forgé : la suite du cas n'a rien à éprouver")
            controle.saute(
                "forger (total_ttc pas forgé au run 1 : la suite n'est pas exigée)", partie=True
            )
    vm_arretee(atelier, controle, "forger")
    if reel and forge:
        controle.saute(
            "forger (en --reel, ce que rendent les outils est montré, pas exigé)", partie=True
        )


async def suite_forger(atelier: Atelier, loom: Loom, controle: Controle) -> None:
    """Le catalogue après le run 1, puis le run 2 : ``total_ttc`` en outil à part entière."""
    dossier = atelier.catalogue / "total_ttc"
    fichiers = sorted(p.name for p in dossier.iterdir()) if dossier.is_dir() else []
    print(f"  catalogue : default/total_ttc/ {fichiers}")
    enonce(
        "  code forgé : ",
        (dossier / "total_ttc.py").read_text(encoding="utf-8") if fichiers else "",
    )
    print(
        "  le module et outil.json sont au catalogue du client : "
        + controle.tient(
            "forger : le catalogue ne tient pas total_ttc",
            fichiers == ["outil.json", "total_ttc.py"],
        )
    )
    second, events = await atelier.run(loom, "2", DEMANDE_043)
    print(
        "  au run 2, total_ttc est un outil à part entière (forge__total_ttc) : "
        + controle.tient(
            "forger : forge__total_ttc n'est pas proposé au run 2",
            "forge__total_ttc" in proposes(events),
        )
    )
    if not atelier.reel:
        directs = rendus_de(events, "forge__total_ttc")
        print(
            f"  appelé directement sur D-2026-043 : {TOTAL_043} : "
            + controle.tient(
                "forger : le run 2 ne rend pas ce qui est annoncé",
                second.status == RunStatus.COMPLETED
                and [d.output.data for d in directs] == [TOTAL_043],
            )
        )


def vm_arretee(
    atelier: Atelier, controle: Controle, cas: str, *, avant: bool | None = None
) -> None:
    """Instance fermée : la VM, arrêtée avant le cas, l'est encore — c'est la source qui
    l'arrête si elle l'a démarrée. Rien à voir si elle tournait déjà avant."""
    tournait = atelier.vm_avant if avant is None else avant
    if tournait:
        return
    print(
        "  l'instance fermée, la VM est arrêtée (la source l'arrête si elle l'a démarrée) : "
        + controle.tient(
            f"{cas} : la VM tourne encore après l'instance", not atelier.vm.is_running()
        )
    )


async def cas_corriger(atelier: Atelier, controle: Controle) -> None:
    avant = atelier.vm.is_running()
    async with Loom(load_config(atelier.fichier)) as loom:
        result, events = await atelier.run(loom, "de correction", DEMANDE_TVA)
    forges = [
        (fait, rendu)
        for fait, rendu in appels(events)
        if fait.tool_name == "forge__forge" and fait.arguments.get("name") == "tva_par_taux"
    ]
    refuses = [r for _, r in forges if r is not None and r.output.is_error]
    acceptes = [r for _, r in forges if r is not None and not r.output.is_error]
    if not atelier.reel:
        if refuses:
            texte = refuses[0].output.as_text
            print(
                "  le premier jet est refusé, et le refus dit l'écart au modèle : "
                + controle.tient(
                    "corriger : le refus ne dit pas l'écart",
                    "Outil tva_par_taux refusé" in texte
                    and "attendu" in texte
                    and "obtenu" in texte,
                )
            )
        code = atelier.catalogue / "tva_par_taux" / "tva_par_taux.py"
        calcul = rendus_de(events, "forge__call")
        print(
            "  refusé, corrigé, accepté : le catalogue garde le jet corrigé, qui calcule "
            f"{TVA_042} : "
            + controle.tient(
                "corriger : le run ne rend pas ce qui est annoncé",
                result.status == RunStatus.COMPLETED
                and len(refuses) == 1
                and len(acceptes) == 1
                and code.is_file()
                and code.read_text(encoding="utf-8") == TVA_CORRIGE
                and [c.output.data for c in calcul] == [TVA_042],
            )
        )
    else:
        # Chaque refus est au récit du run, avec sa raison : qu'elle vienne de
        # l'hôte (schéma, signature) ou d'un exemple joué dans la VM, MiniMax
        # la lit. Rien n'en est exigé.
        print(f"  forges : {len(refuses)} refusé(s), {len(acceptes)} accepté(s)")
        controle.saute(
            "corriger (en --reel, les refus et la correction sont montrés, pas exigés)",
            partie=True,
        )
    vm_arretee(atelier, controle, "corriger", avant=avant)


async def cas_rejeu(atelier: Atelier, controle: Controle) -> None:
    premier = atelier.premier
    if premier is None:
        controle.saute("rejeu (pas de run 1 de forger à rejouer)")
        return
    titre("Le run 1 de forger rejoué, dans une instance neuve")
    tournait = atelier.vm.is_running()
    print(f"  VM avant le rejeu : {'tourne' if tournait else 'arrêtée'}")
    async with Loom(load_config(atelier.fichier)) as loom:
        rapport = await loom.replay(premier.run_id, session_id=premier.session_id)
        pendant = atelier.vm.is_running()
    print(f"  identique : {'oui' if rapport.identical else 'non'}")
    if rapport.divergence is not None:
        enonce("  divergence : ", f"{rapport.divergence.where} — {rapport.divergence.detail}")
    servis, rejoues = rapport.tool_calls
    print(f"  appels d'outil au journal : {servis}, servis par le journal au rejeu : {rejoues}")
    if premier.status != RunStatus.COMPLETED:
        print("  le run 1 n'est pas allé au bout : son rejeu n'éprouve pas la source")
        controle.saute("rejeu (le run 1 n'est pas allé au bout : rejeu non exigé)", partie=True)
        return
    print(
        "  rejoué à l'identique, chaque outil servi par le journal : "
        + controle.tient(
            "rejeu : le rejeu n'est pas identique, ou n'a pas servi les outils du journal",
            rapport.identical and servis == rejoues and servis > 0,
        )
    )
    if tournait:
        controle.saute(
            "rejeu (la VM tournait avant : qu'il ne la démarre pas ne se voit pas)", partie=True
        )
    else:
        print(
            "  la VM n'a pas été démarrée : "
            + controle.tient("rejeu : la VM a été démarrée par le rejeu", not pendant)
        )


async def cas_bornes(atelier: Atelier, controle: Controle) -> None:
    if atelier.reel:
        controle.saute("bornes (en --reel : il éprouve la source, pas le modèle ; joué en simulé)")
        return
    avant = atelier.vm.is_running()
    async with Loom(load_config(atelier.fichier)) as loom:
        result, events = await atelier.run(loom, "des bornes", DEMANDE_BORNES)
    rendus = {
        str(fait.arguments.get("name")): rendu
        for fait, rendu in appels(events)
        if fait.tool_name == "forge__call" and rendu is not None
    }
    bavard, lent = rendus.get("bavard"), rendus.get("lent")
    if bavard is not None:
        texte = bavard.output.as_text
        print(f"  bavard(n=20000) : {len(texte)} caractères au journal, rendu {bavard.output.data}")
        print(
            "  le modèle n'en voit qu'un début et une fin, la valeur intacte : "
            + controle.tient(
                "bornes : la sortie de bavard n'est pas bornée",
                bavard.output.data == 20000 and "caractères omis" in texte and len(texte) < 4500,
            )
        )
    else:
        controle.tient("bornes : bavard n'a pas été appelé", False)
    if lent is not None:
        print(
            f"  lent(secondes=30) revient en timeout après {WALL_MS} ms, et le run continue : "
            + controle.tient(
                "bornes : lent ne revient pas en timeout, ou le run s'arrête",
                lent.output.is_error
                and "timeout" in lent.output.as_text
                and result.status == RunStatus.COMPLETED,
            )
        )
    else:
        controle.tient("bornes : lent n'a pas été appelé", False)
    vm_arretee(atelier, controle, "bornes", avant=avant)


# --- Lancement ----------------------------------------------------------------------


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Un agent qui forge ses outils dans une VM")
    parser.add_argument("--vm", type=Path, help="dossier de la VM (vm.env, run.sh)")
    parser.add_argument("--reel", action="store_true", help="vrai modèle (atelier_reel)")
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    parser.add_argument(
        "--garder", action="store_true", help="garder le dossier (config, catalogue, journal)"
    )
    args = parser.parse_args(argv)
    if args.vm is None:
        print(
            "Il faut une VM : --vm <dossier>, celui que make_vm.sh a construit (vm.env, run.sh), "
            "avec execd dans son image.",
            file=sys.stderr,
        )
        return 2
    try:
        vm = Vm.load(args.vm)
    except VmError as error:
        print(f"VM : {error}", file=sys.stderr)
        return 2
    cas = tuple(args.cas) if args.cas else CAS
    if "rejeu" in cas and "forger" not in cas:
        print("Le cas `rejeu` rejoue le run 1 de `forger` : `forger` est joué d'abord.")
        cas = ("forger", *cas)
    cas = tuple(nom for nom in CAS if nom in cas)
    controle = Controle()
    gardien: contextlib.AbstractContextManager[str] = (
        contextlib.nullcontext(tempfile.mkdtemp(prefix="loom-forge-"))
        if args.garder
        else tempfile.TemporaryDirectory(prefix="loom-forge-")
    )
    with gardien as dossier:
        try:
            atelier = Atelier(args, Path(dossier), vm)
            print(f"Agent : {atelier.agent} ; VM : {vm.directory} ; wall_ms {WALL_MS}")
            for nom in cas:
                print(f"\n{'─' * 78}\nCas {nom}\n{'─' * 78}")
                if nom == "forger":
                    await cas_forger(atelier, controle)
                elif nom == "corriger":
                    await cas_corriger(atelier, controle)
                elif nom == "rejeu":
                    await cas_rejeu(atelier, controle)
                else:
                    await cas_bornes(atelier, controle)
        except (ModelConfigError, ConfigError) as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
        finally:
            if args.garder:
                print(
                    f"\nDossier gardé : {dossier}\n  la config dans atelier/loom.yaml, les outils "
                    "forgés dans atelier/catalogue/, le journal dans atelier/data/ (un .jsonl "
                    "par session ; les échanges bruts du modèle aux événements model.exchanged). "
                    "À effacer à la main."
                )
    verdict = controle.bilan(len(cas))
    return 2 if verdict is None else 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))

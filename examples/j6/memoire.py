# SPDX-License-Identifier: Apache-2.0
"""Phase 6.4d : la mémoire long terme — ``loom-notes`` branché en serveur MCP.

    uv run python examples/j6/memoire.py
    uv run python examples/j6/memoire.py --cas chercher
    uv run python examples/j6/memoire.py --cas memoriser
    uv run python examples/j6/memoire.py --cas rejeu
    uv run python examples/j6/memoire.py --cas clients
    uv run python examples/j6/memoire.py --serveur ~/dev/loom-notes/.venv/bin/loom-notes-mcp
    uv run --env-file .env --extra anthropic \\
        python examples/j6/memoire.py --reel
    uv run --env-file .env --extra anthropic \\
        python examples/j6/memoire.py --reel --device cpu

Le serveur est ``loom-notes`` publié sur PyPI, à la version ``LOOM_NOTES``,
lancé par ``uvx`` : rien à installer. L'exemple le prépare avant de démarrer
(``uvx --from loom-notes==… loom-notes --help``) ; la première fois, uvx
télécharge le paquet, et en ``--reel`` son extra ``models`` (torch compris,
plusieurs Go). ``--serveur`` le remplace par un binaire ``loom-notes-mcp``
local, pour essayer un changement pas encore publié. loom le lance en
serveur MCP stdio, ``memoire``, sur une base vide d'un dossier temporaire
(``LOOM_NOTES_DATA_DIR``), effacé à la fin : aucune base existante n'est
ouverte. Le serveur tourne dans ce dossier, pour qu'aucun ``.env`` du dossier
de lancement ne règle sa base à la place de la config. En simulé, il tourne
en modèles factices ; en ``--reel``, avec ses vrais modèles (BGE-M3 et le
reranker) sur ``--device`` (``cuda`` par défaut), chargés au premier appel qui
en a besoin plutôt qu'en tâche de fond — le délai de ces outils est allongé
d'autant ; un cache de modèles déplacé (``HF_HOME``, ``HF_HUB_CACHE``) lui est
transmis.

Ce que la config dit du serveur, outil par outil (``mcp_servers[].tools``) :

- les cinq écritures (``add_text``, ``add_url``, ``add_file``, ``update``,
  ``delete``) en ``approval: always`` : le run s'arrête avant chacune, et
  l'exemple joue l'artisan qui accepte ou refuse ;
- les descriptions qui nomment l'utilisateur de ``loom-notes`` sont
  réécrites (``description``) pour celui de l'agent, l'artisan : c'est ce que
  le modèle lit. loom ne transmet pas les ``instructions`` du serveur ; le
  prompt de l'agent dit lui-même quand chercher et quand écrire.

L'agent : ``assistant``, un orchestrateur simulé, ou en ``--reel``
``assistant_reel``, MiniMax-M3 comme dans la config de J4 (clé dans
``M3_API_KEY``). ``assistant`` reste dans la config en ``--reel`` : il remplit
la mémoire, relève ce qu'elle contient (``list_docs``), efface la note du
rejeu, et joue l'écriture que personne n'a demandée — un vrai modèle ne la
fait pas sur commande.

* **chercher** : la mémoire est remplie de trois notes du carnet, écritures
  approuvées par l'artisan ; puis l'agent répond sur le devis D-2026-042 en
  appelant ``search``, sans approbation. Les outils que voit le modèle se
  lisent dans les échanges bruts : aucun ne nomme plus l'utilisateur de
  ``loom-notes``.
* **memoriser** : « Mémorise que… » arrête le run sur l'écriture ; tant que
  l'artisan n'a pas répondu, la mémoire n'a pas changé ; il accepte, la note
  y est. Puis une écriture que personne n'a demandée est refusée, et rien
  n'est écrit.
* **rejeu** : la note de ``memoriser`` effacée (écriture approuvée), son run
  est rejoué à l'identique dans une instance neuve : le journal sert
  l'écriture, la note ne revient pas. ``memoriser`` est joué d'abord s'il
  n'est pas demandé.
* **clients** : Dupont et Martin en ``scope: tenant`` — un serveur par
  client, sa base dans le dossier que nomment ses secrets. Ce que Dupont
  mémorise, Martin ne le trouve pas. En ``--reel``, sauté : chaque client y
  chargerait les modèles.

En ``--reel``, ce que fait MiniMax est montré, pas exigé — ce qu'il cherche,
ce qu'il écrit, ce qu'il répond, ce que la recherche trouve. Ce qui tient à
loom reste exigé : la lecture sans approbation, l'arrêt avant l'écriture, la
mémoire inchangée sans accord, les descriptions de la config, le rejeu.
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import yaml

from loom_ia.access import Loom, RunResult
from loom_ia.adapters.models import ModelConfigError
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import (
    ApprovalGranted,
    ApprovalRejected,
    ApprovalRequested,
    Event,
    ModelExchanged,
    ToolCalled,
    ToolCompleted,
)
from loom_ia.core.model import PendingApproval, RunStatus, TenantId

J4 = Path(__file__).parent.parent / "j4" / "relance"
CAS = ("chercher", "memoriser", "rejeu", "clients")
SERVEUR = "memoire"
LECTURES = ("search", "get", "list_docs", "projects")
ECRITURES = ("add_text", "add_url", "add_file", "update", "delete")
# Les outils qui passent par les modèles : leur premier appel les charge.
CHARGENT = ("search", "add_text", "add_url", "add_file", "update")
DELAI_REEL = 300.0
# L'utilisateur que les descriptions de loom-notes nomment.
NOMME = "Denis"
ARTISAN = "l'artisan"
PROJET = "dupont"
CLIENTS = ("dupont", "martin")
# Les caches de modèles que le serveur doit retrouver, s'ils sont déplacés.
CACHES = ("HF_HOME", "HF_HUB_CACHE")
# La version publiée de loom-notes, lancée par uvx quand --serveur n'est pas donné.
LOOM_NOTES = "1.1.0"
# Ce dont uvx a besoin pour retrouver l'environnement préparé : le serveur n'hérite
# que d'un environnement réduit.
UV_ENV = ("UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR")

NOTES = (
    (
        "Devis D-2026-042",
        "Devis D-2026-042 pour Mme Martin : remplacement d'un chauffe-eau de 200 L, "
        "1 840 € TTC, envoyé le 2 septembre, en attente de réponse.",
    ),
    (
        "Grossiste chauffe-eau",
        "Les chauffe-eau se commandent chez le grossiste habituel, livrés en trois jours ; "
        "le compte est au nom de Plomberie Dupont.",
    ),
    (
        "Chantier de M. Bernard",
        "Fuite sous l'évier de M. Bernard réparée le 15 septembre ; repasser changer le "
        "siphon, la pièce est commandée.",
    ),
)
RAPPEL = {
    "text": "Mme Martin préfère être rappelée après 17 h.",
    "title": "Rappels de Mme Martin",
    "project": PROJET,
}
IMPREVU = {
    "text": "Mme Martin est joignable le samedi matin.",
    "title": "Samedis de Mme Martin",
    "project": PROJET,
}

DEMANDE_CARNET = "Mémorise ces trois notes de mon carnet :\n" + "\n".join(
    f"- {titre_} : {texte}" for titre_, texte in NOTES
)
DEMANDE_DEVIS = "Où en est le devis du chauffe-eau de Mme Martin ?"
DEMANDE_RAPPEL = "Mémorise que Mme Martin préfère être rappelée après 17 h."
DEMANDE_QUAND = "Quand puis-je rappeler Mme Martin ?"
DEMANDE_LISTE = "Liste les documents de la mémoire."
DEMANDE_EFFACE = "Efface la note sur les rappels de Mme Martin."

GARDE = " Uniquement si l'artisan le demande dans son message ; chaque écriture attend son accord."
# Ce que le modèle lit des outils qui nomment l'utilisateur de loom-notes ;
# get, list_docs et projects gardent la leur.
DESCRIPTIONS = {
    "search": (
        "Recherche dans la mémoire de l'entreprise : notes, devis, clients, chantiers. À "
        "appeler avant de répondre sur un client, un devis ou une décision passée. Rend des "
        "extraits courts avec doc_id, titre, projet et score ; get lit un document en entier."
    ),
    "add_text": "Ajoute une note à la mémoire." + GARDE,
    "add_url": (
        "Télécharge une page web, en extrait le contenu principal et l'ajoute à la mémoire. "
        "Le titre est celui de la page." + GARDE
    ),
    "add_file": (
        "Ajoute un fichier markdown local à la mémoire, découpé par titres. Le titre est le "
        "premier H1, sinon le nom du fichier." + GARDE
    ),
    "update": (
        "Remplace le texte d'un document existant (même doc_id ; titre, projet et tags "
        "conservés)." + GARDE
    ),
    "delete": (
        "Supprime définitivement un document de la mémoire. Rend le résumé de ce qui a été "
        "supprimé." + GARDE
    ),
}

SYSTEME = (
    "Tu es l'assistant d'un artisan plombier. Sa mémoire (outils memoire__*) garde ses notes : "
    "clients, devis, chantiers. Avant de répondre sur un client, un devis ou une décision "
    "passée, cherche dans la mémoire avec memoire__search. N'écris dans la mémoire que si "
    "l'artisan le demande dans son message ; chaque écriture attend son accord, et un refus ne "
    "se discute pas. Réponds en une ou deux phrases."
)


def appel(nom: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"name": f"{SERVEUR}__{nom}", "arguments": arguments}


def script(efface: str | None = None) -> list[dict[str, Any]]:
    """Le script de l'orchestrateur simulé : chaque demande choisit ses réponses.

    ``efface`` : le document que « Efface la note… » supprime — connu une fois
    la note écrite, il demande de réécrire la config.
    """
    # Les trois notes l'une après l'autre : en réel, le premier appel charge le
    # modèle, et loom-notes ne protège pas ce chargement d'appels simultanés.
    pas: list[dict[str, Any]] = [
        {
            "with_text": "trois notes",
            "text": f"Je mémorise « {titre_} ».",
            "tool_calls": [appel("add_text", {"text": texte, "title": titre_, "project": PROJET})],
        }
        for titre_, texte in NOTES
    ]
    pas += [
        {"with_text": "trois notes", "text": "Les trois notes sont en mémoire."},
        {
            "with_text": "Où en est",
            "text": "Je cherche.",
            "tool_calls": [appel("search", {"query": "devis chauffe-eau Mme Martin"})],
        },
        {
            "with_text": "Où en est",
            "text": "Le devis D-2026-042, 1 840 € TTC, attend la réponse de Mme Martin depuis "
            "le 2 septembre.",
        },
        {
            "with_text": "Mémorise que",
            "text": "Je le note.",
            "tool_calls": [appel("add_text", RAPPEL)],
        },
        {"with_text": "Mémorise que", "text": "C'est noté."},
        {
            "with_text": "Quand puis-je",
            "text": "Je cherche.",
            "tool_calls": [appel("search", {"query": "rappeler Mme Martin"})],
        },
        {
            "with_text": "Quand puis-je",
            "text": "Je note aussi qu'elle est joignable le samedi.",
            "tool_calls": [appel("add_text", IMPREVU)],
        },
        {"with_text": "Quand puis-je", "text": "Après 17 h, d'après vos notes."},
        {"with_text": "Liste les documents", "tool_calls": [appel("list_docs", {"n": 50})]},
        {"with_text": "Liste les documents", "text": "Voilà la liste."},
    ]
    if efface is not None:
        pas += [
            {
                "with_text": "Efface la note",
                "text": "Je l'efface.",
                "tool_calls": [appel("delete", {"doc_id": efface})],
            },
            {"with_text": "Efface la note", "text": "C'est effacé."},
        ]
    return pas


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


# --- La config ----------------------------------------------------------------------


def serveur_memoire(base: Path, args: argparse.Namespace, *, clients: bool) -> dict[str, Any]:
    """Le serveur ``memoire`` : son lancement, sa base, et ce que la config dit de ses outils."""
    env: dict[str, str] = {
        "FASTMCP_SHOW_SERVER_BANNER": "false",
        "FASTMCP_CHECK_FOR_UPDATES": "off",
        "FASTMCP_LOG_LEVEL": "WARNING",
    }
    if args.reel:
        # Les modèles se chargent au premier appel qui en a besoin, pas en tâche de fond :
        # un chargement à la fois.
        env |= {"LOOM_NOTES_DEVICE": args.device, "LOOM_NOTES_WARMUP_ON_START": "false"}
        env |= {nom: os.environ[nom] for nom in CACHES if nom in os.environ}
    else:
        env["LOOM_NOTES_FAKE_MODELS"] = "true"
    if args.serveur is None:
        env |= {nom: os.environ[nom] for nom in UV_ENV if nom in os.environ}
    if not clients:
        env["LOOM_NOTES_DATA_DIR"] = str(base / "memoire")
    outils: dict[str, dict[str, Any]] = {nom: {"description": d} for nom, d in DESCRIPTIONS.items()}
    for nom in ECRITURES:
        outils[nom]["approval"] = "always"
    if args.reel:
        for nom in CHARGENT:
            outils[nom]["timeout"] = DELAI_REEL
    memoire: dict[str, Any] = {
        "name": SERVEUR,
        "transport": "stdio",
        "command": args.commande,
        "args": list(args.arguments),
        "cwd": str(base),
        "env": env,
        # Gardé ouvert le temps de l'instance : en réel, avec ses modèles chargés.
        "idle_timeout": None,
        "tools": outils,
    }
    if clients:
        # Un serveur par client, sa base dans le dossier que nomment ses secrets.
        memoire |= {"scope": "tenant", "env_from": {"LOOM_NOTES_DATA_DIR": "MEMOIRE_DOSSIER"}}
    return memoire


def ecrit_config(
    base: Path, args: argparse.Namespace, *, efface: str | None = None, clients: bool = False
) -> Path:
    """La config : le serveur, l'orchestrateur simulé, et en réel MiniMax-M3."""
    (base / "agents").mkdir(parents=True, exist_ok=True)
    reel = args.reel and not clients
    modeles: list[dict[str, Any]] = [
        {
            "id": "FAKE_MAIN",
            "sdk": "fake",
            "model": "fake-main",
            "params": {"script": script(efface)},
        }
    ]
    agents = [("assistant", "FAKE_MAIN")]
    if reel:
        m3 = load_config(J4 / "loom.yaml").model_spec("M3_MAIN")
        modeles.append(m3.model_dump(mode="json", exclude_defaults=True))
        agents.append(("assistant_reel", m3.id))
    config: dict[str, Any] = {
        "version": 1,
        "models": modeles,
        "mcp_servers": [serveur_memoire(base, args, clients=clients)],
        "storage": {"events": {"backend": "jsonl", "path": "data"}},
        "telemetry": {"logging": {"level": "WARNING"}, "capture": {"raw_exchanges": True}},
    }
    if clients:
        config["tenants"] = [
            {"id": client, "secrets": {"MEMOIRE_DOSSIER": f"{client.upper()}_MEMOIRE"}}
            for client in CLIENTS
        ]
    (base / "loom.yaml").write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    for nom, modele in agents:
        agent = {
            "name": nom,
            "description": "Répond à l'artisan en s'appuyant sur sa mémoire.",
            "main": {"model": modele, "system": SYSTEME},
            "max_iterations": 6,
            "tools": [{"mcp": SERVEUR, "required": True}],
        }
        (base / "agents" / f"{nom}.yaml").write_text(
            yaml.safe_dump(agent, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
    return base / "loom.yaml"


# --- Ce que le journal dit d'un run -------------------------------------------------


def proposes(events: list[Event]) -> list[dict[str, Any]]:
    """Les outils proposés au modèle dans le premier appel du run, lus dans l'échange brut."""
    for event in events:
        if isinstance(event.payload, ModelExchanged) and event.payload.request_body:
            corps = cast(dict[str, Any], json.loads(event.payload.request_body))
            return cast(list[dict[str, Any]], corps.get("tools") or [])
    return []


def appels(events: list[Event]) -> list[tuple[ToolCalled, ToolCompleted | None]]:
    """Chaque appel d'outil du run et son résultat."""
    rendus = {e.payload.call_id: e.payload for e in events if isinstance(e.payload, ToolCompleted)}
    return [
        (e.payload, rendus.get(e.payload.call_id))
        for e in events
        if isinstance(e.payload, ToolCalled)
    ]


def demandes(events: list[Event]) -> list[str]:
    """Les outils pour lesquels le run a demandé une approbation."""
    return [e.payload.tool_name for e in events if isinstance(e.payload, ApprovalRequested)]


def liste(rendu: ToolCompleted) -> list[dict[str, Any]] | None:
    """La liste qu'un outil de la mémoire a rendue (``search``, ``list_docs``) ; sinon ``None``.

    FastMCP enveloppe une liste dans ``{"result": …}`` ; loom en fait le ``data``.
    """
    data = rendu.output.data
    if not isinstance(data, dict) or not isinstance(data.get("result"), list):
        return None
    return [cast(dict[str, Any], item) for item in cast(list[Any], data["result"])]


def resume(rendu: ToolCompleted) -> str:
    """Ce qu'un appel a rendu, en une ligne lisible."""
    if rendu.output.is_error:
        return f"erreur : {rendu.output.as_text}"
    trouves = liste(rendu)
    if trouves is not None:
        if not trouves:
            return "rien"
        return " ; ".join(
            f"{t.get('title')} ({t['score']:.2f})" if "score" in t else str(t.get("title"))
            for t in trouves
        )
    data = rendu.output.data
    if isinstance(data, dict) and "doc_id" in data:
        double = f", doublon de {data['duplicate_of']}" if data.get("duplicate_of") else ""
        return f"« {data.get('title')} » → {data['doc_id']}{double}"
    return rendu.output.as_text or "(vide)"


def raconte(result: RunResult, events: list[Event], *, depuis: int = 0, texte: bool = True) -> int:
    """Le récit d'un run, à partir de son appel n° ``depuis`` ; rend le nombre d'appels.

    ``texte=False`` tait la réponse : celle d'un orchestrateur simulé est écrite
    d'avance, quoi que l'outil ait rendu.
    """
    faits = appels(events)
    for fait, rendu in faits[depuis:]:
        enonce(f"  → {fait.tool_name} ", json.dumps(fait.arguments, ensure_ascii=False))
        if rendu is not None:
            enonce("    rendu : ", resume(rendu))
    print(f"  statut : {result.status.value}" + (f" ({result.error})" if result.error else ""))
    if result.pending_approvals:
        print(f"  en attente de l'accord de {ARTISAN} :")
    for attente in result.pending_approvals:
        enonce(f"    {attente.tool_name} ", json.dumps(attente.arguments, ensure_ascii=False))
    if texte and result.status != RunStatus.PAUSED:
        enonce("  texte  : ", result.text or "(vide)")
    return len(faits)


def sans_suite(result: RunResult, fait: str) -> str:
    """Pourquoi un run de MiniMax n'a pas donné ce que le cas éprouve."""
    if result.status in {RunStatus.FAILED, RunStatus.CANCELLED}:
        return f"le run de MiniMax n'est pas allé au bout ({result.status.value})"
    return f"MiniMax {fait}"


def rendus_de(events: list[Event], outil: str) -> list[ToolCompleted]:
    return [r for f, r in appels(events) if f.tool_name == f"{SERVEUR}__{outil}" and r]


def titres(documents: list[dict[str, Any]]) -> list[str]:
    return sorted(str(d.get("title")) for d in documents)


# --- Les runs ----------------------------------------------------------------------


async def demande(
    loom: Loom, agent: str, texte: str, *, tenant: TenantId | None = None
) -> tuple[RunResult, list[Event]]:
    """Un run neuf, dans sa propre session ; rend son résultat et son journal."""
    result = await loom.run(agent, texte, tenant=tenant)
    events = await loom.events(result.run_id, session_id=result.session_id, tenant_id=tenant)
    return result, events


async def decide(
    loom: Loom,
    asked: RunResult,
    *,
    accorde: bool,
    motif: str = "",
    tenant: TenantId | None = None,
) -> tuple[RunResult, list[Event]]:
    """L'artisan accepte ou refuse ce que le run attend ; le run va au bout."""
    deja = len(await loom.events(asked.run_id, session_id=asked.session_id, tenant_id=tenant))
    if accorde:
        await loom.approve(asked.run_id, by=ARTISAN, session_id=asked.session_id, tenant_id=tenant)
    else:
        await loom.reject(
            asked.run_id, by=ARTISAN, reason=motif, session_id=asked.session_id, tenant_id=tenant
        )
    await loom.drain()
    fini = await loom.result(asked.run_id, session_id=asked.session_id, tenant_id=tenant)
    events = await loom.events(asked.run_id, session_id=asked.session_id, tenant_id=tenant)
    for event in events[deja:]:
        if isinstance(event.payload, ApprovalGranted):
            print(f"  au journal : {event.payload.tool_name} accordé par {event.payload.by}")
        elif isinstance(event.payload, ApprovalRejected):
            enonce(
                f"  au journal : {event.payload.tool_name} refusé par {event.payload.by}, ",
                f"motif rendu au modèle : « {event.payload.reason} »",
            )
    return fini, events


class ReleveImpossible(Exception):
    """La mémoire n'a pas pu être relevée : son serveur ne répond pas comme prévu."""


async def releve(loom: Loom, *, tenant: TenantId | None = None) -> list[dict[str, Any]]:
    """Ce que la mémoire contient, lu par l'orchestrateur simulé (``list_docs``).

    Il passe par le même serveur que l'agent : une base embarquée ne s'ouvre
    que dans un process à la fois.
    """
    result, events = await demande(loom, "assistant", DEMANDE_LISTE, tenant=tenant)
    rendus = rendus_de(events, "list_docs")
    trouves = liste(rendus[0]) if len(rendus) == 1 else None
    if result.status != RunStatus.COMPLETED or trouves is None:
        raise ReleveImpossible(
            f"run {result.status.value}" + (f" ({result.error})" if result.error else "")
        )
    return trouves


# --- Les cas ------------------------------------------------------------------------


class Atelier:
    """Ce que les cas partagent : la config, la base, l'agent, la note de memoriser."""

    def __init__(self, args: argparse.Namespace, dossier: Path) -> None:
        self.args = args
        self.reel: bool = args.reel
        self.dossier = dossier
        self.base = dossier / "atelier"
        self.fichier = ecrit_config(self.base, args)
        self.agent = "assistant_reel" if self.reel else "assistant"
        # Le run de memoriser qui a écrit, et le document qu'il a écrit.
        self.ecrit: RunResult | None = None
        self.document: str | None = None


async def remplit(loom: Loom, controle: Controle) -> None:
    """Trois notes du carnet, écrites par l'orchestrateur simulé avec l'accord de l'artisan."""
    titre("La mémoire remplie : trois notes du carnet, chacune avec l'accord de l'artisan")
    print("  (l'orchestrateur simulé les écrit, dans les deux modes)")
    fini, events = await demande(loom, "assistant", DEMANDE_CARNET)
    deja = raconte(fini, events)
    # Une pause par écriture : l'artisan accorde chacune, le run passe à la suivante.
    attentes: list[list[str]] = []
    while fini.status == RunStatus.PAUSED and len(attentes) <= len(NOTES):
        attentes.append([str(a.arguments.get("title")) for a in fini.pending_approvals])
        fini, events = await decide(loom, fini, accorde=True)
        deja = raconte(fini, events, depuis=deja)
    print(
        "  le run s'est arrêté avant chacune des trois écritures : "
        + controle.tient(
            "chercher : le remplissage ne s'arrête pas avant chaque écriture",
            attentes == [[titre_] for titre_, _ in NOTES],
        )
    )
    documents = await releve(loom)
    print(f"  la mémoire : {', '.join(titres(documents)) or 'vide'}")
    print(
        "  accordées, les trois notes sont en mémoire : "
        + controle.tient(
            "chercher : les trois notes ne sont pas en mémoire",
            fini.status == RunStatus.COMPLETED and {t for t, _ in NOTES} <= set(titres(documents)),
        )
    )


async def cas_chercher(atelier: Atelier, controle: Controle) -> None:
    async with Loom(load_config(atelier.fichier)) as loom:
        await remplit(loom, controle)
        titre(f"Run de {atelier.agent} : {DEMANDE_DEVIS}")
        result, events = await demande(loom, atelier.agent, DEMANDE_DEVIS)
        raconte(result, events)
    outils = proposes(events)
    lus = {str(o.get("name")): str(o.get("description")) for o in outils}
    enonce("  le modèle lit memoire__search : ", lus.get(f"{SERVEUR}__search", "(absent)"))
    print(
        f"  il voit les neuf outils, aucun ne nomme {NOMME}, les descriptions sont celles "
        "de la config : "
        + controle.tient(
            "chercher : les outils vus par le modèle ne sont pas ceux annoncés",
            sorted(lus) == sorted(f"{SERVEUR}__{n}" for n in LECTURES + ECRITURES)
            and NOMME not in json.dumps(outils, ensure_ascii=False)
            and all(lus[f"{SERVEUR}__{n}"] == d for n, d in DESCRIPTIONS.items()),
        )
    )
    lectures = {f"{SERVEUR}__{n}" for n in LECTURES}
    lu = [f.tool_name for f, _ in appels(events) if f.tool_name in lectures]
    if lu:
        print(
            "  la lecture n'a demandé aucune approbation : "
            + controle.tient(
                "chercher : une lecture a demandé une approbation",
                not set(demandes(events)) & lectures,
            )
        )
    if not atelier.reel:
        trouves = [liste(r) or [] for r in rendus_de(events, "search")]
        print(
            "  search est appelé, et la note du devis vient en tête : "
            + controle.tient(
                "chercher : le run ne rend pas ce qui est annoncé",
                result.status == RunStatus.COMPLETED
                and len(trouves) == 1
                and bool(trouves[0])
                and trouves[0][0].get("title") == NOTES[0][0],
            )
        )
    else:
        if not lu:
            pourquoi = sans_suite(result, "n'a rien lu dans la mémoire")
            print(f"  {pourquoi} : la lecture sans approbation n'est pas vue")
        controle.saute(
            "chercher (en --reel, ce que MiniMax cherche, ce qu'il trouve et ce qu'il répond est "
            "montré, pas exigé)",
            partie=True,
        )


async def cas_memoriser(atelier: Atelier, controle: Controle) -> None:
    async with Loom(load_config(atelier.fichier)) as loom:
        avant = await releve(loom)
        print(f"  la mémoire : {', '.join(titres(avant)) or 'vide'}")
        titre(f"Run de {atelier.agent} : {DEMANDE_RAPPEL}")
        asked, events = await demande(loom, atelier.agent, DEMANDE_RAPPEL)
        deja = raconte(asked, events)
        ecritures = {f"{SERVEUR}__{n}" for n in ECRITURES}
        attente = [a for a in asked.pending_approvals if a.tool_name in ecritures]
        if asked.status == RunStatus.PAUSED and attente:
            await accorde_la_note(atelier, loom, controle, asked, attente[0], avant, deja)
            if atelier.reel:
                controle.saute(
                    "memoriser (en --reel, ce que MiniMax écrit et répond est montré, pas exigé)",
                    partie=True,
                )
        elif atelier.reel:
            pourquoi = sans_suite(asked, "n'a demandé aucune écriture")
            print(f"  {pourquoi} : l'arrêt avant l'écriture n'est pas vu")
            controle.saute(
                f"memoriser ({pourquoi} : l'écriture accordée n'est pas éprouvée)", partie=True
            )
        else:
            controle.tient("memoriser : le run ne s'arrête pas avant l'écriture", False)
        await refuse_l_imprevu(atelier, loom, controle)


async def accorde_la_note(
    atelier: Atelier,
    loom: Loom,
    controle: Controle,
    asked: RunResult,
    attente: PendingApproval,
    avant: list[dict[str, Any]],
    deja: int,
) -> None:
    pendant = await releve(loom)
    print(
        "  tant que l'artisan n'a pas répondu, la mémoire n'a pas changé : "
        + controle.tient(
            "memoriser : la mémoire a changé avant l'accord", titres(pendant) == titres(avant)
        )
    )
    titre(f"{ARTISAN.capitalize()} accepte")
    fini, events = await decide(loom, asked, accorde=True)
    raconte(fini, events, depuis=deja)
    apres = await releve(loom)
    nouveaux = sorted(set(titres(apres)) - set(titres(avant)))
    print(f"  la mémoire : {', '.join(titres(apres))}")
    if attente.tool_name != f"{SERVEUR}__add_text":
        print(
            f"  l'écriture accordée est {attente.tool_name}, pas add_text : "
            "la note n'est pas suivie"
        )
        controle.saute("memoriser (écriture accordée autre que add_text : non suivie)", partie=True)
        return
    voulu = str(attente.arguments.get("title", "")).strip()
    ecrits = [r for r in rendus_de(events, "add_text") if not r.output.is_error]
    data = ecrits[-1].output.data if ecrits else None
    document = data.get("doc_id") if isinstance(data, dict) else None
    print(
        f"  accordée, la note « {voulu} » est en mémoire, et elle seule : "
        + controle.tient(
            "memoriser : la note accordée n'est pas en mémoire",
            fini.status == RunStatus.COMPLETED
            and nouveaux == [voulu]
            and isinstance(document, str),
        )
    )
    if isinstance(document, str):
        atelier.ecrit, atelier.document = fini, document
    if not atelier.reel:
        print(
            f"  l'écriture est celle du script : {RAPPEL['title']} : "
            + controle.tient(
                "memoriser : l'écriture n'est pas celle du script",
                attente.arguments == RAPPEL,
            )
        )


async def refuse_l_imprevu(atelier: Atelier, loom: Loom, controle: Controle) -> None:
    titre(f"Run de assistant : {DEMANDE_QUAND} — une écriture que personne n'a demandée")
    if atelier.reel:
        print("  (joué par l'orchestrateur simulé : MiniMax n'écrit pas de lui-même sur commande)")
    avant = await releve(loom)
    asked, events = await demande(loom, "assistant", DEMANDE_QUAND)
    deja = raconte(asked, events)
    print(
        "  le prompt dit de ne pas écrire, le modèle écrit quand même, et le run s'arrête : "
        + controle.tient(
            "memoriser : le run ne s'arrête pas avant l'écriture imprévue",
            asked.status == RunStatus.PAUSED
            and [(a.tool_name, a.arguments) for a in asked.pending_approvals]
            == [(f"{SERVEUR}__add_text", IMPREVU)],
        )
    )
    titre(f"{ARTISAN.capitalize()} refuse")
    fini, events = await decide(loom, asked, accorde=False, motif="Je n'ai rien demandé.")
    raconte(fini, events, depuis=deja)
    apres = await releve(loom)
    faits = [r for r in rendus_de(events, "add_text") if not r.output.is_error]
    print(
        "  refusée, l'écriture n'est jamais faite, et le run finit normalement : "
        + controle.tient(
            "memoriser : l'écriture refusée a été faite, ou le run n'a pas fini",
            fini.status == RunStatus.COMPLETED and not faits and titres(apres) == titres(avant),
        )
    )


async def cas_rejeu(atelier: Atelier, controle: Controle) -> None:
    ecrit, document = atelier.ecrit, atelier.document
    if ecrit is None or document is None:
        controle.saute("rejeu (pas d'écriture accordée de memoriser à rejouer)")
        return
    titre("L'artisan fait effacer la note de memoriser")
    print("  (l'orchestrateur simulé l'efface, dans les deux modes ; la config est réécrite")
    print(f"  pour qu'il connaisse le document, {document})")
    ecrit_config(atelier.base, atelier.args, efface=document)
    async with Loom(load_config(atelier.fichier)) as loom:
        asked, events = await demande(loom, "assistant", DEMANDE_EFFACE)
        deja = raconte(asked, events)
        fini, events = await decide(loom, asked, accorde=True)
        raconte(fini, events, depuis=deja)
        efface = await releve(loom)
    print(
        "  le run s'arrête avant l'effacement ; accordé, la note n'est plus en mémoire : "
        + controle.tient(
            "rejeu : la note n'a pas été effacée",
            asked.status == RunStatus.PAUSED
            and [a.tool_name for a in asked.pending_approvals] == [f"{SERVEUR}__delete"]
            and fini.status == RunStatus.COMPLETED
            and document not in {d.get("doc_id") for d in efface},
        )
    )
    # Le rejeu tourne avec la config du run d'origine : sinon le script de
    # l'orchestrateur simulé, changé, ferait diverger sa première requête.
    ecrit_config(atelier.base, atelier.args)
    titre("Le run de memoriser rejoué à l'identique, dans une instance neuve")
    async with Loom(load_config(atelier.fichier)) as loom:
        rapport = await loom.replay(ecrit.run_id, session_id=ecrit.session_id)
        apres = await releve(loom)
    print(f"  identique : {'oui' if rapport.identical else 'non'}")
    if rapport.divergence is not None:
        enonce("  divergence : ", f"{rapport.divergence.where} — {rapport.divergence.detail}")
    servis, rejoues = rapport.tool_calls
    print(f"  appels d'outil au journal : {servis}, servis par le journal au rejeu : {rejoues}")
    print(f"  la mémoire : {', '.join(titres(apres)) or 'vide'}")
    print(
        "  rejoué à l'identique, l'écriture servie par le journal, la note ne revient pas : "
        + controle.tient(
            "rejeu : le rejeu n'est pas identique, ou la note est revenue",
            rapport.identical
            and servis == rejoues
            and servis > 0
            and document not in {d.get("doc_id") for d in apres},
        )
    )


@contextmanager
def dossiers_clients(dossier: Path) -> Generator[dict[str, Path]]:
    """Le dossier de chaque client, dans la variable que nomment ses secrets."""
    dossiers = {client: dossier / f"memoire-{client}" for client in CLIENTS}
    variables = {f"{client.upper()}_MEMOIRE": str(d) for client, d in dossiers.items()}
    avant = {nom: os.environ.get(nom) for nom in variables}
    os.environ.update(variables)
    try:
        yield dossiers
    finally:
        for nom, valeur in avant.items():
            if valeur is None:
                os.environ.pop(nom, None)
            else:
                os.environ[nom] = valeur


async def cas_clients(atelier: Atelier, controle: Controle) -> None:
    if atelier.reel:
        controle.saute(
            "clients (en --reel : chaque client y chargerait les modèles dans son serveur ; "
            "joué en simulé)"
        )
        return
    dupont, martin = (TenantId(client) for client in CLIENTS)
    with dossiers_clients(atelier.dossier) as dossiers:
        fichier = ecrit_config(atelier.dossier / "clients", atelier.args, clients=True)
        for client, chemin in dossiers.items():
            print(f"  {client} : base dans {chemin.name}/, nommée par {client.upper()}_MEMOIRE")
        async with Loom(load_config(fichier)) as loom:
            titre(f"Dupont : {DEMANDE_RAPPEL}")
            asked, events = await demande(loom, "assistant", DEMANDE_RAPPEL, tenant=dupont)
            deja = raconte(asked, events)
            fini, events = await decide(loom, asked, accorde=True, tenant=dupont)
            raconte(fini, events, depuis=deja)
            chez_dupont = await releve(loom, tenant=dupont)
            chez_martin = await releve(loom, tenant=martin)
            print(f"  mémoire de Dupont : {', '.join(titres(chez_dupont)) or 'vide'}")
            print(f"  mémoire de Martin : {', '.join(titres(chez_martin)) or 'vide'}")
            trouve: dict[str, list[dict[str, Any]]] = {}
            for client, tenant in ((CLIENTS[0], dupont), (CLIENTS[1], martin)):
                titre(f"{client.capitalize()} : {DEMANDE_DEVIS}")
                result, events = await demande(loom, "assistant", DEMANDE_DEVIS, tenant=tenant)
                raconte(result, events, texte=False)
                rendus = rendus_de(events, "search")
                trouve[client] = (liste(rendus[0]) or []) if rendus else []
    print(
        "  la note de Dupont est dans sa mémoire, pas dans celle de Martin : "
        + controle.tient(
            "clients : les mémoires ne sont pas séparées",
            fini.status == RunStatus.COMPLETED
            and titres(chez_dupont) == [RAPPEL["title"]]
            and chez_martin == []
            and [t.get("title") for t in trouve[CLIENTS[0]]] == [RAPPEL["title"]]
            and trouve[CLIENTS[1]] == [],
        )
    )
    print(
        "  deux bases, chacune dans le dossier de son client : "
        + controle.tient(
            "clients : les deux bases ne sont pas dans leurs dossiers",
            all((chemin / "qdrant").is_dir() for chemin in dossiers.values()),
        )
    )


# --- Lancement ----------------------------------------------------------------------


async def jouer(nom: str, atelier: Atelier, controle: Controle) -> None:
    if nom == "chercher":
        await cas_chercher(atelier, controle)
    elif nom == "memoriser":
        await cas_memoriser(atelier, controle)
    elif nom == "rejeu":
        await cas_rejeu(atelier, controle)
    else:
        await cas_clients(atelier, controle)


def prepare_uvx(reel: bool) -> tuple[str, tuple[str, ...]] | None:
    """Prépare loom-notes de PyPI par uvx ; rend la commande du serveur, ou None.

    Le premier lancement résout et télécharge le paquet : il est fait ici, hors du
    délai de connexion de loom (``connect_timeout``), et uvx montre sa progression.
    """
    uvx = shutil.which("uvx")
    if uvx is None:
        print(
            "uvx introuvable : installe uv, ou passe --serveur <binaire loom-notes-mcp>.",
            file=sys.stderr,
        )
        return None
    paquet = f"loom-notes[models]=={LOOM_NOTES}" if reel else f"loom-notes=={LOOM_NOTES}"
    print(f"Préparation de {paquet} par uvx…")
    try:
        subprocess.run(
            [uvx, "--from", paquet, "loom-notes", "--help"], check=True, stdout=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        print(f"uvx n'a pas pu préparer {paquet} (voir ci-dessus).", file=sys.stderr)
        return None
    return uvx, ("--from", paquet, "loom-notes-mcp")


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="La mémoire long terme : loom-notes en MCP")
    parser.add_argument(
        "--serveur", type=Path, help="binaire loom-notes-mcp local, à la place du paquet PyPI"
    )
    parser.add_argument("--reel", action="store_true", help="vrais modèles (assistant_reel)")
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    parser.add_argument(
        "--device", default="cuda", help="périphérique des modèles de loom-notes en --reel"
    )
    args = parser.parse_args(argv)
    if args.serveur is not None:
        args.serveur = args.serveur.expanduser().resolve()
        if not args.serveur.is_file():
            print(f"Serveur : {args.serveur} introuvable", file=sys.stderr)
            return 2
        args.commande, args.arguments, args.decrit = str(args.serveur), (), str(args.serveur)
    elif (lancement := prepare_uvx(args.reel)) is None:
        return 2
    else:
        args.commande, args.arguments = lancement
        args.decrit = f"loom-notes {LOOM_NOTES} (PyPI, par uvx)"
    cas = tuple(args.cas) if args.cas else CAS
    if "rejeu" in cas and "memoriser" not in cas:
        print("Le cas `rejeu` rejoue l'écriture de `memoriser` : `memoriser` est joué d'abord.")
        cas = ("memoriser", *cas)
    cas = tuple(nom for nom in CAS if nom in cas)
    controle = Controle()
    with tempfile.TemporaryDirectory(prefix="loom-memoire-") as dossier:
        try:
            atelier = Atelier(args, Path(dossier))
            modeles = f"vrais, sur {args.device}" if args.reel else "factices"
            print(f"Agent : {atelier.agent} ; serveur : {args.decrit} ; modèles {modeles}")
            for nom in cas:
                print(f"\n{'─' * 78}\nCas {nom}\n{'─' * 78}")
                try:
                    await jouer(nom, atelier, controle)
                except ReleveImpossible as error:
                    # Le cas s'arrête là ; les suivants ouvrent leur propre instance.
                    print(f"  relevé de la mémoire impossible : {error}")
                    controle.tient(f"{nom} : relevé de la mémoire impossible — {error}", False)
        except (ModelConfigError, ConfigError) as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
    verdict = controle.bilan(len(cas))
    return 2 if verdict is None else 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))

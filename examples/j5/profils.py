# SPDX-License-Identifier: Apache-2.0
"""Phase 5.5a : les profils dev et prod — la même config, deux exigences.

    uv run python examples/j5/profils.py                     # les trois cas
    uv run python examples/j5/profils.py --cas provenance
    uv run python examples/j5/profils.py --cas durcit
    uv run python examples/j5/profils.py --cas assouplit
    uv run --env-file .env --extra anthropic --extra openai \\
        python examples/j5/profils.py --reel

Config : ``examples/j5/relance/``, celle de 5.1a. Les surcharges et les
variantes sont posées **en code** : les fichiers de ``relance/`` ne changent
pas, et cet exemple ne lance aucun serveur.

Trois états, et non deux. **Sans `profile`**, loom se comporte comme il l'a
toujours fait : les avertissements sont des avertissements, les erreurs des
erreurs. **`prod`** durcit les premiers — la même config cesse de charger.
**`dev`** assouplit les secondes — ce qu'on refuse en service, on le tolère
sur une machine.

* **provenance** : d'où vient le profil. L'option l'emporte sur
  ``LOOM_PROFILE``, qui l'emporte sur le fichier ; un profil inconnu est
  refusé en nommant sa provenance ; et les surcharges d'un profil fusionnent
  les objets en profondeur là où elles remplacent les listes.
* **durcit** : quatre règles qui avertissent aujourd'hui et refusent en
  ``prod`` — le juge corrélé de cette config, un service éparpillé, une API
  sans clé, et ``judges="skip"``.
* **assouplit** : la règle qui refuse aujourd'hui et tolère en ``dev`` — un
  agent qui peut se mettre en pause sur un journal qui ne survit pas au
  process. Plus le juge qui ne travaille que dans un profil, et le journal qui
  dit pourquoi il ne s'est pas exprimé.
"""

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from loom_ia.access import Loom
from loom_ia.adapters.models import ModelConfigError
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.config.loader import PROFILE_ENV, chosen_profile
from loom_ia.core.model import SessionId, TenantId, new_id
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent / "relance" / "loom.yaml"
CAS = ("provenance", "durcit", "assouplit")

DUPONT = TenantId("dupont-plomberie")
DEVIS = "D-2026-042"


def shown(path: Path) -> str:
    return os.path.relpath(path)


def titre(texte: str) -> None:
    print(f"\n=== {texte} ===")


def agent_de(args: argparse.Namespace) -> str:
    return "relance_reel" if args.reel else "relance"


def demande() -> str:
    return f"Relance le client du devis {DEVIS}, sur un ton cordial."


class Controle:
    """Ce que chaque essai devait rendre : l'exemple l'annonce, puis le vérifie."""

    def __init__(self) -> None:
        self.ecarts: list[str] = []

    def tient(self, quoi: str, vrai: bool) -> str:
        if not vrai:
            self.ecarts.append(quoi)
        return "oui" if vrai else "NON"

    def bilan(self) -> bool:
        if not self.ecarts:
            print("\nChaque essai a rendu ce que l'exemple annonçait.")
            return True
        print("\nUn essai au moins n'a pas rendu ce qui était annoncé :")
        for ecart in self.ecarts:
            print(f"  {ecart}")
        return False


def refus(faire: Any) -> str:
    """Le message d'un refus de configuration, ou une chaîne vide s'il n'y en a pas."""
    try:
        faire()
    except ConfigError as error:
        return str(error)
    return ""


async def refus_async(faire: Any) -> str:
    try:
        await faire()
    except ConfigError as error:
        return str(error)
    return ""


def coupe(texte: str, largeur: int = 96) -> str:
    return texte if len(texte) <= largeur else f"{texte[: largeur - 1]}…"


# --- Cas 1 : d'où vient le profil, et ce qu'il surcharge ---------------------


def provenance(args: argparse.Namespace, controle: Controle) -> None:
    titre("Trois endroits pour le dire, un seul qui gagne")
    os.environ.pop(PROFILE_ENV, None)
    print(f"  {'option':<10}{'variable':<12}{'fichier':<10}→ profil (provenance)")
    essais: tuple[tuple[str | None, str | None, str | None], ...] = (
        (None, None, None),
        (None, None, "dev"),
        (None, "prod", "dev"),
        ("dev", "prod", "prod"),
    )
    for option, variable, fichier in essais:
        if variable is None:
            os.environ.pop(PROFILE_ENV, None)
        else:
            os.environ[PROFILE_ENV] = variable
        actif, source = chosen_profile(option, fichier)
        print(
            f"  {option or '—':<10}{variable or '—':<12}{fichier or '—':<10}"
            f"→ {actif or 'aucun'} ({source})"
        )
    os.environ.pop(PROFILE_ENV, None)
    print(
        "\n  l'option l'emporte, puis la variable, puis le fichier : "
        + controle.tient(
            "l'arbitrage des trois sources n'est pas celui annoncé",
            chosen_profile("dev", "prod")[0] == "dev" and chosen_profile(None, "prod")[0] == "prod",
        )
    )

    titre("Un profil inconnu est refusé, en nommant sa provenance")
    dit = refus(lambda: chosen_profile("recette"))
    print(f"  --profile recette → {dit}")
    print(
        "  la provenance est dite : "
        + controle.tient("le refus ne nomme pas sa provenance", "(option)" in dit)
    )

    titre("Ce qu'une surcharge de profil fait, et ne fait pas")
    # Une vraie config, chargée deux fois : la fusion se montre par le
    # chargeur, pas par une réimplémentation. Le dossier est temporaire et
    # disparaît avec l'exemple — les fichiers de `relance/` ne changent pas.
    with tempfile.TemporaryDirectory(prefix="loom-profils-") as dossier:
        fichier = _minuscule(Path(dossier))
        sans = load_config(fichier)
        en_prod = load_config(fichier, profile="prod")
        print(f"  un {fichier.name} temporaire déclare :")
        print("    execution: {tools: {timeout: 30, offload_over: 1000}}")
        print("    imports: [outils_un, outils_deux]")
        print("    profiles: {prod: {execution: {tools: {timeout: 90}}, imports: [outils_un]}}")
        print(f"\n  {'':<14}{'sans profil':<22}--profile prod")
        lignes = (
            ("tools.timeout", sans.execution.tools.timeout, en_prod.execution.tools.timeout),
            (
                "offload_over",
                sans.execution.tools.offload_over,
                en_prod.execution.tools.offload_over,
            ),
            ("imports", len(sans.imports), len(en_prod.imports)),
        )
        for quoi, avant, apres in lignes:
            print(f"  {quoi:<14}{avant!s:<22}{apres}")
        print(
            "\n  les objets fusionnent en profondeur : "
            + controle.tient(
                "la fusion a perdu ce que la surcharge ne dit pas",
                en_prod.execution.tools.timeout == 90
                and en_prod.execution.tools.offload_over == sans.execution.tools.offload_over,
            )
        )
        print(
            "  les listes sont remplacées, jamais fusionnées : "
            + controle.tient(
                "une liste a été fusionnée",
                len(sans.imports) == 2 and en_prod.imports == ("outils_un",),
            )
        )
        print(
            "  et un profil qui n'est pas actif ne change rien : "
            + controle.tient(
                "un profil inactif a surchargé quelque chose",
                load_config(fichier, profile="dev").execution.tools.timeout == 30,
            )
        )


def _minuscule(dossier: Path) -> Path:
    """Une config minuscule, dans un dossier temporaire, qui déclare un profil."""
    (dossier / "agents").mkdir()
    (dossier / "outils_un.py").write_text("", encoding="utf-8")
    (dossier / "outils_deux.py").write_text("", encoding="utf-8")
    agent: dict[str, Any] = {
        "name": "demo",
        "description": "Ne fait rien.",
        "main": {"model": "FAKE", "system": "Tu ne fais rien."},
    }
    (dossier / "agents" / "demo.yaml").write_text(yaml.safe_dump(agent), encoding="utf-8")
    config: dict[str, Any] = {
        "version": 1,
        "imports": ["outils_un", "outils_deux"],
        "models": [{"id": "FAKE", "sdk": "fake", "model": "fake-1", "params": {"script": []}}],
        "execution": {"tools": {"timeout": 30, "offload_over": 1000}},
        "profiles": {"prod": {"execution": {"tools": {"timeout": 90}}, "imports": ["outils_un"]}},
    }
    fichier = dossier / "loom.yaml"
    fichier.write_text(yaml.safe_dump(config), encoding="utf-8")
    return fichier


# --- Cas 2 : ce que prod durcit ---------------------------------------------


async def durcit(args: argparse.Namespace, controle: Controle) -> None:
    agent = agent_de(args)
    titre("Un juge corrélé — avertissement depuis 3.3, erreur en prod")
    print("  Un juge qui évalue une sortie avec le modèle qui l'a écrite ne garantit")
    print("  presque rien. Dans cette config, Chauffage Martin en produit un tout seul")
    print("  en réel : sa correspondance GLM_FLASH → HAIKU replie le juge sur le modèle")
    print("  du rôle. Ici on pose la même corrélation en code, pour la montrer aussi en")
    print("  simulé, et sur l'agent qu'on a sous la main.")
    correle = _correle(load_config(CONFIG), agent)
    async with Loom(correle) as loom:
        loom.context(agent, DUPONT)
        print("\n  sans profil → monte, avec l'avertissement dans les logs")
    dit = await refus_async(
        lambda: _monte_simple(correle.model_copy(update={"profile": "prod"}), agent)
    )
    print(f"  --profile prod → {coupe(dit)}")
    print(
        "  la même config refuse de partir en production : "
        + controle.tient("le juge corrélé n'est pas une erreur en prod", "juge corrélé" in dit)
    )

    titre("Un service éparpillé dont les fichiers ne suivent pas — 5.3c")
    os.environ.setdefault("LOOM_RABBITMQ", "amqp://exemple.invalide")
    eparpille = _brouille(load_config(CONFIG))
    async with Loom(eparpille) as loom:
        print(
            f"  sans profil → monte ; file {loom.config.storage.queue.backend}, "
            f"artefacts {loom.config.storage.artifacts_backend}"
        )
    dit = refus(lambda: Loom(_brouille(load_config(CONFIG, profile="prod"))))
    print(f"  --profile prod → {coupe(dit)}")
    print(
        "  un volume partagé reste un montage légitime, mais il faut le dire : "
        + controle.tient("le service éparpillé n'est pas une erreur en prod", "Profil prod" in dit)
    )

    titre("Une API sans clé déclarée, ouverte sur le réseau — #39")
    ouverte = _ouverte(load_config(CONFIG))
    dit = refus(lambda: _appli(_ouverte(load_config(CONFIG, profile="prod"))))
    print(f"  sans profil → monte sur {ouverte.server.http.host} (avertissement)")
    print(f"  --profile prod → {coupe(dit) or '(extra http absent : essai sauté)'}")
    if dit:
        print(
            "  une API ouverte sans clé ne part pas en production : "
            + controle.tient("l'API sans clé n'est pas une erreur en prod", "sans clé" in dit)
        )

    titre("Se passer des juges — judges='skip'")
    async with Loom(load_config(CONFIG)) as loom:
        result = await loom.run(
            agent, demande(), session_id=SessionId(_session("sans")), tenant=DUPONT, judges="skip"
        )
        print(f"  sans profil → {result.status}, {len(result.verdicts)} verdict(s)")
    async with Loom(load_config(CONFIG, profile="prod")) as loom:
        dit = await refus_async(
            lambda: loom.run(
                agent,
                demande(),
                session_id=SessionId(_session("prod")),
                tenant=DUPONT,
                judges="skip",
            )
        )
        print(f"  --profile prod → {coupe(dit)}")
    print(
        "  retirer un contrôle n'est pas permis en production : "
        + controle.tient("judges='skip' passe en prod", "judges='skip'" in dit)
    )


def _appli(config: LoomConfig) -> object:
    from loom_ia.access.http import create_app

    return create_app(Loom(config))


def _brouille(config: LoomConfig) -> LoomConfig:
    """La file passe au courtier, les fichiers restent dans un dossier local."""
    queue = config.storage.queue.model_copy(
        update={"backend": "rabbitmq", "url_env": "LOOM_RABBITMQ"}
    )
    storage = config.storage.model_copy(update={"queue": queue})
    return config.model_copy(update={"storage": storage})


def _ouverte(config: LoomConfig) -> LoomConfig:
    """L'API écoute ailleurs que sur la machine, et aucune clé n'est déclarée."""
    http = config.server.http.model_copy(update={"host": "0.0.0.0"})
    return config.model_copy(update={"server": config.server.model_copy(update={"http": http})})


def _session(quoi: str) -> str:
    return f"profils-{new_id()[-8:]}-{quoi}"


# --- Cas 3 : ce que dev assouplit -------------------------------------------


async def assouplit(args: argparse.Namespace, controle: Controle) -> None:
    agent = agent_de(args)
    titre("Un agent qui peut se mettre en pause, sur un journal volatile — #28")
    print("  En pause, le run n'existe plus que dans le journal : un journal en")
    print("  mémoire le perdrait à la fermeture, et l'approbation n'aurait rien à")
    print("  reprendre. Une erreur, donc — sauf sur une machine.")
    volatile = _volatile(_approbation(load_config(CONFIG), agent))
    dit = await refus_async(lambda: _monte_simple(volatile, agent))
    print(f"\n  sans profil → {coupe(dit)}")
    print(
        "  c'est bien une erreur : "
        + controle.tient("le journal volatile passe sans profil", "journal durable" in dit)
    )
    en_dev = volatile.model_copy(update={"profile": "dev"})
    async with Loom(en_dev) as loom:
        loom.context(agent, DUPONT)
        print("  --profile dev → monte, avec l'avertissement dans les logs")
    print(
        "  ce qu'on refuse en service, on le tolère sur une machine : "
        + controle.tient("le profil dev n'assouplit rien", en_dev.lax)
    )

    titre("Un juge qui ne travaille qu'en prod — when.profiles")
    juge_prod = _juge_en(load_config(CONFIG), agent, "prod")
    async with Loom(juge_prod.model_copy(update={"profile": "dev"})) as loom:
        session = SessionId(_session("dev"))
        ailleurs = await loom.run(agent, demande(), session_id=session, tenant=DUPONT)
        sautes = await loom.events(ailleurs.run_id, session_id=session, tenant_id=DUPONT)
    passes = [e for e in sautes if e.type == "guard.checked" and e.facets.get("guard") == "judge"]
    motifs = [str(getattr(e.payload, "reason", "")) for e in passes]
    print(f"  en dev  → {len(ailleurs.verdicts)} verdict(s), {len(passes)} contrôle(s) sauté(s)")
    print(f"  {'':<10}motif au journal : {', '.join(motifs) or 'aucun'}")
    async with Loom(juge_prod.model_copy(update={"profile": "prod"})) as loom:
        session = SessionId(_session("juge"))
        chez_lui = await loom.run(agent, demande(), session_id=session, tenant=DUPONT)
    print(f"  en prod → {len(chez_lui.verdicts)} verdict(s)")
    print(
        "\n  un juge déclaré ne disparaît pas en silence : "
        + controle.tient(
            "le juge sauté ne dit pas pourquoi",
            motifs == ["other_profile"] and not ailleurs.verdicts,
        )
    )
    print(
        "  et dans son profil, il juge pour de bon : "
        + controle.tient("le juge ne juge pas dans son profil", bool(chez_lui.verdicts))
    )


async def _monte_simple(config: LoomConfig, agent: str) -> None:
    async with Loom(config) as loom:
        loom.context(agent, DUPONT)


def _approbation(config: LoomConfig, agent: str) -> LoomConfig:
    """Rend l'outil de l'agent soumis à approbation : de quoi le faire pauser."""
    spec = next(a for a in config.agents if a.name == agent)
    outils = tuple(tool.model_copy(update={"approval": "always"}) for tool in spec.tools)
    pausant = spec.model_copy(update={"tools": outils})
    agents = tuple(pausant if a.name == agent else a for a in config.agents)
    return config.model_copy(update={"agents": agents})


def _volatile(config: LoomConfig) -> LoomConfig:
    events = config.storage.events.model_copy(update={"backend": "memory", "path": None})
    storage = config.storage.model_copy(update={"events": events})
    return config.model_copy(update={"storage": storage})


def _correle(config: LoomConfig, agent: str) -> LoomConfig:
    """Donne au juge du rôle le modèle du rôle : la corrélation, posée en code."""
    spec = next(a for a in config.agents if a.name == agent)
    roles = tuple(
        role.model_copy(update={"judge": role.judge.model_copy(update={"model": role.model})})
        if role.judge is not None
        else role
        for role in spec.roles
    )
    corrige = spec.model_copy(update={"roles": roles})
    agents = tuple(corrige if a.name == agent else a for a in config.agents)
    return config.model_copy(update={"agents": agents})


def _juge_en(config: LoomConfig, agent: str, profil: str) -> LoomConfig:
    """Borne le juge du rôle de l'agent à un seul profil."""
    spec = next(a for a in config.agents if a.name == agent)
    roles = tuple(
        role.model_copy(
            update={"judge": role.judge.model_copy(update={"when": _quand(role.judge, profil)})}
        )
        if role.judge is not None
        else role
        for role in spec.roles
    )
    borne = spec.model_copy(update={"roles": roles})
    agents = tuple(borne if a.name == agent else a for a in config.agents)
    return config.model_copy(update={"agents": agents})


def _quand(judge: Any, profil: str) -> Any:
    return judge.when.model_copy(update={"profiles": (profil,)})


# --- Mise en route -----------------------------------------------------------


async def jouer(nom: str, args: argparse.Namespace, controle: Controle) -> None:
    print(f"\n{'─' * 78}\nCas {nom}\n{'─' * 78}")
    if nom == "provenance":
        provenance(args, controle)
    elif nom == "durcit":
        await durcit(args, controle)
    else:
        await assouplit(args, controle)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Profils dev et prod : ce qu'ils durcissent, ce qu'ils assouplissent"
    )
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    args = parser.parse_args(argv)
    cas = tuple(args.cas) if args.cas else CAS

    try:
        config = load_config(CONFIG)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(config)
    print(f"Config   : {shown(CONFIG)}")
    print(f"Agent    : {agent_de(args)}")
    print(f"Profil   : {config.profile or 'aucun'} (le fichier n'en déclare pas)")
    controle = Controle()
    for nom in cas:
        try:
            await jouer(nom, args, controle)
        except ModelConfigError as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
        except ImportError as error:
            print(f"Extra manquant pour le cas {nom} : {error}", file=sys.stderr)
            return 2
    return 0 if controle.bilan() else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))

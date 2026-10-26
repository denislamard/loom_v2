# SPDX-License-Identifier: Apache-2.0
"""Phase 6.1 : les traces d'un run dans un collecteur OpenTelemetry, ses échanges, ses logs.

    uv run --extra otel python examples/j6/traces.py                  # les cinq cas
    uv run --extra otel python examples/j6/traces.py --cas otel
    uv run --extra otel python examples/j6/traces.py --cas otel --collecteur http://localhost:4318
    uv run --env-file .env --extra otel --extra anthropic --extra openai \\
        python examples/j6/traces.py --reel

Les cas demandent l'extra ``otel``, sauf ``logs`` ; sans lui, ils se sautent
et le bilan le dit. Config : ``examples/j5/relance/``, celle de 5.1a (la relance de devis,
deux artisans). Le journal va dans un dossier **temporaire** ; la télémétrie
est posée **en code**.

Le collecteur est **dans l'exemple** : un petit serveur OTLP/HTTP qui reçoit ce
que loom envoie, le décode et le garde. Ce que l'exemple vérifie a donc passé
le réseau, comme chez un vrai collecteur. ``--collecteur URL`` envoie **aussi**
à un collecteur à soi (Jaeger : ``docker run --rm -p 16686:16686 -p 4318:4318
jaegertracing/all-in-one``, puis http://localhost:16686) — l'exemple ne peut
pas relire celui-là, il le dit.

* **otel** : les spans d'un run arrivent au collecteur, et ce sont ceux du
  journal — même trace que le run, chaque span du journal présent, un span
  ``chat`` par réponse de modèle, chaque parent dans la trace.
* **masquage** : en ``metadata``, rien de la demande ne sort ; en ``content``,
  elle sort, mais l'e-mail et le téléphone qu'elle cite sont masqués.
* **clients** : la capture se règle client par client — le contenu de Dupont
  part, celui de Martin non, dans le même process.
* **bruts** (6.1b) : avec ``capture.raw_exchanges``, chaque appel de modèle
  laisse au journal ses échanges bruts (``model.exchanged``), juste avant sa
  réponse ; aucune clé d'API n'y est, un corps trop long est coupé en le
  disant, et les corps ne partent jamais vers le collecteur — même en
  ``content``. En simulé, l'échange est **synthétique** (le faux modèle n'a
  pas d'HTTP) et l'absence de clé n'a rien à éprouver : le bilan le dit.
* **logs** (6.1b) : une ligne ``INFO`` par appel de modèle et par appel
  d'outil, autant que le journal compte de réponses et de résultats, sans
  rien de la demande.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
import textwrap
import threading
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.util import find_spec
from pathlib import Path
from typing import Any, cast

from loom_ia.access import Loom
from loom_ia.adapters.models import ModelConfigError
from loom_ia.config import ConfigError, LoomConfig, load_config
from loom_ia.config.models import ArtifactsStorage, IdempotencyStorage, TelemetryConfig
from loom_ia.config.telemetry import CaptureConfig, CaptureOverride, ExporterConfig, TenantTelemetry
from loom_ia.core.events import Event, ModelExchanged
from loom_ia.core.model import SessionId, TenantId, new_id
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent.parent / "j5" / "relance" / "loom.yaml"
CAS = ("otel", "masquage", "clients", "bruts", "logs")
# Borne des corps bruts dans le cas `bruts` : assez basse pour qu'une requête
# soit coupée, ce que l'exemple veut montrer.
BORNE = 2048

DUPONT = TenantId("dupont-plomberie")
MARTIN = TenantId("martin-chauffage")
DEVIS = {DUPONT: "D-2026-042", MARTIN: "D-2026-117"}
COURRIEL = "jeanne.martin@exemple.fr"
TELEPHONE = "06 12 34 56 78"
# Variables qui portent l'adresse des collecteurs : la config les nomme.
ICI = "LOOM_EXEMPLE_COLLECTEUR"
AILLEURS = "LOOM_EXEMPLE_COLLECTEUR_EXTERNE"


def shown(path: Path) -> str:
    return os.path.relpath(path)


def titre(texte: str) -> None:
    print(f"\n=== {texte} ===")


def agent_de(args: argparse.Namespace) -> str:
    return "relance_reel" if args.reel else "relance"


def demande(tenant: TenantId) -> str:
    return (
        f"Relance le client du devis {DEVIS[tenant]}, sur un ton cordial. "
        f"Qu'il réponde à {COURRIEL} ou au {TELEPHONE}."
    )


class Controle:
    """Ce que chaque essai devait rendre : l'exemple l'annonce, puis le vérifie."""

    def __init__(self) -> None:
        self.ecarts: list[str] = []
        self.sautes: list[str] = []
        self.non_verifies: list[str] = []

    def tient(self, quoi: str, vrai: bool) -> str:
        if not vrai:
            self.ecarts.append(quoi)
        return "oui" if vrai else "NON"

    def saute(self, quoi: str) -> None:
        """Un cas qui n'a pas pu être joué : à dire, sinon le bilan mentirait."""
        self.sautes.append(quoi)

    def bilan(self, joues: int) -> bool | None:
        """Vrai si tout a tenu, faux sinon ; ``None`` si rien n'a été joué."""
        if joues == len(self.sautes):
            for saute in self.sautes:
                print(f"\nCas sauté : {saute}")
            print("\nAucun cas joué : l'exemple n'a rien éprouvé.")
            return None
        for saute in self.sautes:
            print(f"\nCas sauté : {saute}")
        for note in self.non_verifies:
            print(f"\nNon vérifié par l'exemple : {note}")
        if not self.ecarts:
            dit = "Chaque essai joué a rendu" if self.sautes else "Chaque essai a rendu"
            print(f"\n{dit} ce que l'exemple annonçait.")
            return True
        print("\nUn essai au moins n'a pas rendu ce qui était annoncé :")
        for ecart in self.ecarts:
            print(f"  {ecart}")
        return False


def enonce(quoi: str, texte: str, largeur: int = 96) -> None:
    """Imprime un message long sur plusieurs lignes, sans rien en couper."""
    marge = " " * len(quoi)
    for numero, ligne in enumerate(textwrap.wrap(texte, largeur - len(quoi))):
        print(f"{quoi if numero == 0 else marge}{ligne}")


# --- Le collecteur de l'exemple ------------------------------------------------


@dataclass
class Span:
    """Un span tel que le collecteur l'a reçu, décodé du protobuf."""

    trace_id: str
    span_id: str
    parent_span_id: str
    name: str
    start_ns: int
    end_ns: int
    attributes: dict[str, Any]
    events: list[tuple[str, dict[str, Any]]] = field(
        default_factory=list[tuple[str, dict[str, Any]]]
    )

    @property
    def tenant(self) -> str:
        return str(self.attributes.get("loom.tenant_id", ""))

    def contenus(self) -> dict[str, str]:
        """Les attributs de contenu des événements du span : ``{clé: valeur}``."""
        found: dict[str, str] = {}
        for _, attributes in self.events:
            for key, value in attributes.items():
                if key.startswith("loom.content."):
                    found[key] = str(value)
        return found

    def textes(self) -> str:
        """Tout ce que le span porte en texte, pour chercher ce qui n'aurait pas dû sortir."""
        valeurs = [str(v) for v in self.attributes.values()]
        valeurs += [str(v) for _, attributes in self.events for v in attributes.values()]
        return "\n".join(valeurs)


def _valeur(any_value: Any) -> Any:
    kind = any_value.WhichOneof("value")
    return getattr(any_value, kind) if kind else None


class Collecteur:
    """Un collecteur OTLP/HTTP minimal, dans un fil : il décode et garde."""

    def __init__(self) -> None:
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceRequest,
        )

        self.spans: list[Span] = []
        self.envois = 0
        recu = self

        class Recepteur(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                corps = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                requete = ExportTraceServiceRequest()
                requete.ParseFromString(corps)
                recu.recevoir(requete)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:
                pass

        self._serveur = ThreadingHTTPServer(("127.0.0.1", 0), Recepteur)
        self._fil = threading.Thread(target=self._serveur.serve_forever, daemon=True)

    @property
    def adresse(self) -> str:
        return f"http://127.0.0.1:{self._serveur.server_address[1]}"

    def recevoir(self, requete: Any) -> None:
        self.envois += 1
        for resource in requete.resource_spans:
            for scope in resource.scope_spans:
                for span in scope.spans:
                    self.spans.append(
                        Span(
                            trace_id=span.trace_id.hex(),
                            span_id=span.span_id.hex(),
                            parent_span_id=span.parent_span_id.hex(),
                            name=span.name,
                            start_ns=span.start_time_unix_nano,
                            end_ns=span.end_time_unix_nano,
                            attributes={a.key: _valeur(a.value) for a in span.attributes},
                            events=[
                                (e.name, {a.key: _valeur(a.value) for a in e.attributes})
                                for e in span.events
                            ],
                        )
                    )

    @contextmanager
    def ouvert(self) -> Generator[Collecteur]:
        self._fil.start()
        try:
            yield self
        finally:
            self._serveur.shutdown()
            self._serveur.server_close()


# --- La config de l'exemple : la même, ailleurs, avec sa télémétrie ------------


def deplacee(
    dossier: Path,
    *,
    capture: str = "metadata",
    par_client: dict[TenantId, str] | None = None,
    externe: bool = False,
    bruts: int | None = None,
    collecteurs: bool = True,
) -> LoomConfig:
    """La config de ``relance/``, journal dans ``dossier``, collecteurs déclarés.

    Les collecteurs sont nommés par leur **variable** : c'est l'environnement
    passé à ``Loom`` qui leur donne une adresse.
    """
    config = load_config(CONFIG)
    events = config.storage.events.model_copy(update={"path": dossier / "events"})
    storage = config.storage.model_copy(
        update={
            "events": events,
            "artifacts": ArtifactsStorage(backend="local", path=dossier / "files"),
            "idempotency": IdempotencyStorage(),
        }
    )
    captures = par_client or {}
    tenants = tuple(
        tenant.model_copy(
            update={
                "storage": tenant.storage.model_copy(
                    update={
                        "events": tenant.storage.events.model_copy(
                            update={"path": dossier / "events" / tenant.id}
                        )
                    }
                )
                if tenant.storage is not None
                else None,
                "telemetry": TenantTelemetry(
                    capture=CaptureOverride.model_validate({"exports": captures[tenant.id]})
                )
                if tenant.id in captures
                else tenant.telemetry,
            }
        )
        for tenant in config.tenants
    )
    exporters: list[ExporterConfig] = []
    if collecteurs:
        exporters.append(ExporterConfig(type="otel", endpoint_env=ICI, service_name="loom-exemple"))
    if externe:
        exporters.append(
            ExporterConfig(type="otel", endpoint_env=AILLEURS, service_name="loom-exemple")
        )
    telemetry = TelemetryConfig(
        logging=config.telemetry.logging,
        capture=CaptureConfig.model_validate(
            {"exports": capture}
            if bruts is None
            else {"exports": capture, "raw_exchanges": True, "raw_max_bytes": bruts}
        ),
        exporters=tuple(exporters),
    )
    # ``model_copy`` et non une nouvelle validation : la config chargée a déjà
    # résolu ses prompts, et la revalider les lirait deux fois.
    return config.model_copy(
        update={"storage": storage, "tenants": tenants, "telemetry": telemetry}
    )


@dataclass
class Tour:
    """Ce qu'un run a laissé : son identifiant, son journal, ce que le collecteur a reçu."""

    run_id: str
    statut: str
    events: list[Event]
    spans: list[Span]


async def lancer(
    config: LoomConfig,
    args: argparse.Namespace,
    clients: Sequence[TenantId],
) -> list[Tour]:
    """Un run par client, puis la fermeture — qui vide les envois en attente."""
    tours: list[Tour] = []
    with Collecteur().ouvert() as collecteur:
        environ = {**os.environ, ICI: collecteur.adresse}
        if args.collecteur:
            environ[AILLEURS] = args.collecteur
        async with Loom(config, environ=environ) as loom:
            lances: list[tuple[TenantId, str, str, SessionId]] = []
            for tenant in clients:
                session = SessionId(f"traces-{new_id()[-8:]}")
                result = await loom.run(
                    agent_de(args), demande(tenant), session_id=session, tenant=tenant
                )
                lances.append((tenant, result.run_id, result.status, session))
            journaux = {
                run_id: [e for e in await loom.store.read(tenant, session) if e.run_id == run_id]
                for tenant, run_id, _, session in lances
            }
        # Ici, l'instance est fermée : ses envois sont partis.
        for _, run_id, statut, _ in lances:
            trace = run_id.replace("-", "")
            tours.append(
                Tour(
                    run_id=run_id,
                    statut=statut,
                    events=journaux[run_id],
                    spans=[s for s in collecteur.spans if s.trace_id == trace],
                )
            )
        print(
            f"  collecteur : {collecteur.envois} envoi(s) reçu(s), {len(collecteur.spans)} span(s)"
        )
    return tours


def arbre(spans: Sequence[Span]) -> None:
    """Les spans d'une trace en arbre, avec leur durée, comme un collecteur les montre."""
    par_id = {s.span_id: s for s in spans}
    enfants: dict[str, list[Span]] = {}
    for span in spans:
        enfants.setdefault(span.parent_span_id, []).append(span)

    def montre(span: Span, profondeur: int) -> None:
        duree = (span.end_ns - span.start_ns) / 1e6
        print(f"    {'  ' * profondeur}{span.name}  ({duree:.1f} ms)")
        for enfant in sorted(enfants.get(span.span_id, []), key=lambda s: s.start_ns):
            montre(enfant, profondeur + 1)

    racines = [s for s in spans if s.parent_span_id not in par_id]
    for racine in sorted(racines, key=lambda s: s.start_ns):
        montre(racine, 0)


def texte_de(contenu: str) -> str:
    """Le texte d'un message exporté (JSON), en entier ; le contenu brut sinon."""
    try:
        message = json.loads(contenu)
    except ValueError:
        return contenu
    if not isinstance(message, dict):
        return contenu
    blocs = cast(dict[str, Any], message).get("blocks")
    if not isinstance(blocs, list):
        return contenu
    textes = [
        str(cast(dict[str, Any], bloc).get("text", ""))
        for bloc in cast(list[Any], blocs)
        if isinstance(bloc, dict)
    ]
    return " ".join(t for t in textes if t) or contenu


def otel_id(span_id: str) -> str:
    """L'identifiant OTel d'un span du journal, comme l'adaptateur le calcule."""
    from loom_ia.adapters.telemetry.otel import span_id as convertir

    return f"{convertir(span_id):016x}"


# --- Cas 1 : otel ------------------------------------------------------------


async def otel(args: argparse.Namespace, controle: Controle) -> None:
    with tempfile.TemporaryDirectory(prefix="loom-traces-") as dossier:
        config = deplacee(Path(dossier), externe=bool(args.collecteur))
        titre("Un run, ses spans au collecteur")
        [tour] = await lancer(config, args, [DUPONT])
    print(f"  run {tour.run_id} : {tour.statut}")
    arbre(tour.spans)
    recus = {s.span_id for s in tour.spans}
    attendus = {otel_id(e.span_id) for e in tour.events}
    reponses = sum(1 for e in tour.events if e.type == "model.responded")
    chats = [s for s in tour.spans if s.name.startswith("chat ")]
    racines = [s for s in tour.spans if not s.parent_span_id]
    print(
        "\n  des spans sont arrivés, sous la trace du run : "
        + controle.tient("aucun span reçu pour la trace du run", bool(tour.spans))
    )
    print(
        f"  chaque span du journal est arrivé ({len(attendus)} au journal, "
        f"{len(recus & attendus)} reçus) : "
        + controle.tient("un span du journal manque au collecteur", attendus <= recus)
    )
    print(
        f"  un span 'chat' par réponse de modèle ({reponses} au journal, {len(chats)} reçus) : "
        + controle.tient(
            "le nombre de spans 'chat' n'est pas celui des réponses", len(chats) == reponses > 0
        )
    )
    print(
        "  une seule racine, le run, et chaque autre span a son parent dans la trace : "
        + controle.tient(
            "l'arbre reçu n'a pas une racine unique ou un parent manque",
            len(racines) == 1
            and racines[0].name.startswith("invoke_agent")
            and all(s.parent_span_id in recus for s in tour.spans if s not in racines),
        )
    )
    if args.collecteur:
        controle.non_verifies.append(
            f"les spans envoyés à {args.collecteur} — à regarder dans son interface"
        )


# --- Cas 2 : masquage ----------------------------------------------------------


async def masquage(args: argparse.Namespace, controle: Controle) -> None:
    for capture in ("metadata", "content"):
        with tempfile.TemporaryDirectory(prefix="loom-traces-") as dossier:
            titre(f"Capture '{capture}'")
            [tour] = await lancer(deplacee(Path(dossier), capture=capture), args, [DUPONT])
        textes = "\n".join(s.textes() for s in tour.spans)
        contenus = [v for s in tour.spans for v in s.contenus().values()]
        print(f"  run {tour.run_id} : {tour.statut}, {len(tour.spans)} span(s) reçus")
        print(
            "  des spans sont arrivés : "
            + controle.tient(f"{capture} : aucun span reçu", bool(tour.spans))
        )
        print(
            "  ni l'e-mail ni le téléphone de la demande ne sortent en clair : "
            + controle.tient(
                f"{capture} : un e-mail ou un téléphone est sorti en clair",
                COURRIEL not in textes and TELEPHONE not in textes,
            )
        )
        if capture == "metadata":
            print(
                "  aucun attribut de contenu : "
                + controle.tient("metadata : un contenu est sorti", not contenus)
            )
            print(
                "  la demande n'apparaît nulle part : "
                + controle.tient(
                    "metadata : la demande est sortie", f"devis {DEVIS[DUPONT]}, sur" not in textes
                )
            )
        else:
            question = next((v for v in contenus if f"devis {DEVIS[DUPONT]}, sur un ton" in v), "")
            print(
                "  la demande sort, masquée : "
                + controle.tient("content : la demande n'est pas sortie", bool(question))
            )
            if question:
                enonce("    ", texte_de(question))
            print(
                "  les deux motifs ont joué ([email], [phone]) : "
                + controle.tient(
                    "content : le masquage n'a pas remplacé l'e-mail et le téléphone",
                    "[email]" in question and "[phone]" in question,
                )
            )


# --- Cas 3 : clients -----------------------------------------------------------


async def clients(args: argparse.Namespace, controle: Controle) -> None:
    with tempfile.TemporaryDirectory(prefix="loom-traces-") as dossier:
        config = deplacee(Path(dossier), capture="metadata", par_client={DUPONT: "content"})
        titre("Racine en 'metadata', Dupont en 'content'")
        for tenant in (DUPONT, MARTIN):
            print(f"  {tenant:<20}{config.capture_for(tenant).exports}")
        tours = await lancer(config, args, [DUPONT, MARTIN])
    par_client = dict(zip((DUPONT, MARTIN), tours, strict=True))
    for tenant, tour in par_client.items():
        contenus = [v for s in tour.spans for v in s.contenus().values()]
        print(
            f"  {tenant} : run {tour.statut}, {len(tour.spans)} span(s), "
            f"{len(contenus)} attribut(s) de contenu"
        )
    dupont = [v for s in par_client[DUPONT].spans for v in s.contenus().values()]
    martin = [v for s in par_client[MARTIN].spans for v in s.contenus().values()]
    print(
        "\n  les deux runs sont arrivés au collecteur : "
        + controle.tient(
            "clients : un des deux runs n'a rien envoyé",
            bool(par_client[DUPONT].spans) and bool(par_client[MARTIN].spans),
        )
    )
    print(
        "  le contenu de Dupont part : "
        + controle.tient(
            "clients : le contenu de Dupont n'est pas sorti",
            any(DEVIS[DUPONT] in v for v in dupont),
        )
    )
    print(
        "  rien du contenu de Martin ne part : "
        + controle.tient(
            "clients : un contenu de Martin est sorti",
            not martin and all(DEVIS[MARTIN] not in s.textes() for s in par_client[MARTIN].spans),
        )
    )


# --- Cas 4 : bruts (6.1b) -------------------------------------------------------


def cles_d_api(config: LoomConfig) -> dict[str, str]:
    """Les clés d'API que les modèles de la config lisent, et leur valeur ici."""
    trouvees: dict[str, str] = {}
    for spec in config.models:
        if spec.api_key_env and os.environ.get(spec.api_key_env):
            trouvees[spec.api_key_env] = os.environ[spec.api_key_env]
    return trouvees


async def bruts(args: argparse.Namespace, controle: Controle) -> None:
    with tempfile.TemporaryDirectory(prefix="loom-traces-") as dossier:
        config = deplacee(Path(dossier), capture="content", bruts=BORNE)
        titre(f"Échanges bruts gardés, corps bornés à {BORNE} octets, capture 'content'")
        [tour] = await lancer(config, args, [DUPONT])
    print(f"  run {tour.run_id} : {tour.statut}")
    echanges = [e for e in tour.events if isinstance(e.payload, ModelExchanged)]
    reponses = [e for e in tour.events if e.type == "model.responded"]
    print(f"\n  {'seq':>4}  {'rôle':<28}{'essai':>6}{'statut':>8}{'requête':>16}{'réponse':>16}")
    for event in echanges:
        brut = event.payload
        assert isinstance(brut, ModelExchanged)
        # La taille est celle du corps entier ; « coupé » dit qu'au journal il
        # n'en reste que la borne — pour la requête comme pour la réponse.
        requete = f"{brut.request_bytes} o" + (" coupée" if brut.request_truncated else "")
        reponse = f"{brut.response_bytes} o" + (" coupée" if brut.response_truncated else "")
        print(
            f"  {event.seq:>4}  {event.role or '':<28}{brut.attempt:>6}"
            f"{brut.status_code if brut.status_code is not None else '—':>8}"
            f"{requete:>16}{reponse:>16}"
        )
    print(
        f"\n  au moins un échange par réponse de modèle ({len(echanges)} échange(s), "
        f"{len(reponses)} réponse(s)) : "
        + controle.tient(
            "bruts : une réponse de modèle sans échange gardé",
            bool(reponses) and len(echanges) >= len(reponses),
        )
    )
    par_seq = {e.seq: e for e in tour.events}
    precedes = all(
        any(
            isinstance(par_seq[s].payload, ModelExchanged)
            and (par_seq[s].span_id, par_seq[s].role) == (r.span_id, r.role)
            for s in range(1, r.seq)
            if s in par_seq
        )
        for r in reponses
    )
    print(
        "  chaque réponse a ses échanges avant elle, dans son span, au nom de son rôle : "
        + controle.tient("bruts : une réponse sans échange dans son span", precedes)
    )
    synthetiques = {e.payload.synthetic for e in echanges if isinstance(e.payload, ModelExchanged)}
    attendu = {not args.reel}
    print(
        f"  échanges {'réels (HTTP)' if args.reel else 'synthétiques (modèle simulé)'} : "
        + controle.tient(
            "bruts : la nature des échanges n'est pas celle du mode joué", synthetiques == attendu
        )
    )
    coupes = [
        e.payload
        for e in echanges
        if isinstance(e.payload, ModelExchanged) and e.payload.request_truncated
    ]
    print(
        f"  une requête plus longue que la borne est coupée et le dit ({len(coupes)} coupée(s)) : "
        + controle.tient(
            "bruts : aucune requête coupée, ou une coupe qui ne tient pas sa borne",
            bool(coupes)
            and all(len(c.request_body.encode()) <= BORNE < c.request_bytes for c in coupes),
        )
    )
    cles = cles_d_api(config)
    if cles:
        journal = "\n".join(e.model_dump_json() for e in tour.events)
        print(
            f"  aucune des {len(cles)} clé(s) d'API ({', '.join(cles)}) n'est au journal : "
            + controle.tient(
                "bruts : une clé d'API est au journal",
                all(valeur not in journal for valeur in cles.values()),
            )
        )
    else:
        controle.non_verifies.append(
            "bruts : l'absence de clé d'API au journal — aucune clé lue ici"
            + (" (modèles simulés)" if not args.reel else "")
        )
    sortis = [
        key
        for span in tour.spans
        for name, attributes in span.events
        if name == "model.exchanged"
        for key in attributes
        if key.startswith("loom.content.")
    ]
    vus = sum(1 for span in tour.spans for name, _ in span.events if name == "model.exchanged")
    print(
        f"  au collecteur, les échanges n'ont que leurs métadonnées ({vus} reçu(s)) : "
        + controle.tient(
            "bruts : un corps d'échange est parti au collecteur",
            vus == len(echanges) and not sortis,
        )
    )
    dernier = next(
        (e.payload for e in reversed(echanges) if isinstance(e.payload, ModelExchanged)), None
    )
    if dernier is not None:
        # Les lignes se comptent sur ce que le journal garde, la taille est celle
        # du corps entier : les deux se disent séparément, sans se mélanger.
        lignes = dernier.response_body.splitlines()
        garde = (
            f"coupé à {len(dernier.response_body.encode())} octets sur {dernier.response_bytes}"
            if dernier.response_truncated
            else f"entier, {dernier.response_bytes} octets"
        )
        print(
            f"\n  début de la dernière réponse brute — {min(len(lignes), 6)} ligne(s) sur "
            f"les {len(lignes)} gardées au journal (corps {garde}) :"
        )
        for ligne in lignes[:6]:
            enonce("    ", ligne or "(vide)")


# --- Cas 5 : logs (6.1b) ---------------------------------------------------------


class Recueil(logging.Handler):
    """Garde les lignes du moteur, le temps d'un cas."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lignes: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lignes.append(record)


async def logs(args: argparse.Namespace, controle: Controle) -> None:
    moteur = logging.getLogger("loom_ia.engine.loop")
    recueil = Recueil()
    # Le temps du cas, les lignes du moteur vont au recueil et à lui seul : la
    # console de l'exemple reste à son niveau habituel (WARNING).
    niveau, propage = moteur.level, moteur.propagate
    moteur.addHandler(recueil)
    moteur.setLevel(logging.INFO)
    moteur.propagate = False
    try:
        with tempfile.TemporaryDirectory(prefix="loom-traces-") as dossier:
            config = deplacee(Path(dossier), collecteurs=False)
            titre("Une ligne par appel de modèle et d'outil")
            session = SessionId(f"traces-{new_id()[-8:]}")
            async with Loom(config) as loom:
                result = await loom.run(
                    agent_de(args), demande(DUPONT), session_id=session, tenant=DUPONT
                )
                events = [
                    e for e in await loom.store.read(DUPONT, session) if e.run_id == result.run_id
                ]
    finally:
        moteur.removeHandler(recueil)
        moteur.setLevel(niveau)
        moteur.propagate = propage
    print(f"  run {result.run_id} : {result.status}")
    lignes = [r.getMessage() for r in recueil.lignes]
    modeles = [ligne for ligne in lignes if ligne.startswith("Modèle ")]
    outils = [ligne for ligne in lignes if ligne.startswith("Outil ")]
    for ligne in modeles + outils:
        enonce("    ", ligne)
    reponses = sum(1 for e in events if e.type == "model.responded")
    resultats = sum(1 for e in events if e.type == "tool.completed")
    print(
        f"\n  une ligne par réponse de modèle ({reponses} au journal, {len(modeles)} ligne(s)) : "
        + controle.tient(
            "logs : le nombre de lignes de modèle n'est pas celui des réponses",
            len(modeles) == reponses > 0,
        )
    )
    print(
        f"  une ligne par résultat d'outil ({resultats} au journal, {len(outils)} ligne(s)) : "
        + controle.tient(
            "logs : le nombre de lignes d'outil n'est pas celui des résultats",
            len(outils) == resultats > 0,
        )
    )
    print(
        "  chaque ligne porte le run : "
        + controle.tient(
            "logs : une ligne sans run_id",
            all(getattr(r, "run_id", None) == result.run_id for r in recueil.lignes),
        )
    )
    texte = "\n".join(lignes)
    print(
        "  rien de la demande dans les logs (ni le devis demandé, ni l'e-mail) : "
        + controle.tient(
            "logs : un contenu est dans les logs",
            f"devis {DEVIS[DUPONT]}, sur" not in texte and COURRIEL not in texte,
        )
    )


# --- Lancement -------------------------------------------------------------------


async def jouer(nom: str, args: argparse.Namespace, controle: Controle) -> None:
    print(f"\n{'─' * 78}\nCas {nom}\n{'─' * 78}")
    if nom == "logs":
        await logs(args, controle)
        return
    if find_spec("opentelemetry") is None:
        print("  extra 'otel' absent : cas non joué")
        controle.saute(f"{nom} (extra 'otel' absent : uv run --extra otel …)")
        return
    if nom == "otel":
        await otel(args, controle)
    elif nom == "masquage":
        await masquage(args, controle)
    elif nom == "clients":
        await clients(args, controle)
    else:
        await bruts(args, controle)


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Les traces d'un run dans un collecteur OpenTelemetry"
    )
    parser.add_argument("--reel", action="store_true", help="vrais modèles")
    parser.add_argument("--cas", action="append", choices=CAS, help="cas à jouer (tous par défaut)")
    parser.add_argument(
        "--collecteur",
        metavar="URL",
        help="collecteur OTLP/HTTP à soi, en plus de celui de l'exemple",
    )
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
    print(f"Clients  : {', '.join(str(tenant.id) for tenant in config.tenants)}")
    controle = Controle()
    for nom in cas:
        try:
            await jouer(nom, args, controle)
        except (ModelConfigError, ConfigError) as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
    verdict = controle.bilan(len(cas))
    return 2 if verdict is None else 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))

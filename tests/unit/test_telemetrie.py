# SPDX-License-Identifier: Apache-2.0
"""Exports et traces : les spans tirés du journal, ce qui en sort, ce qui se relit (J6.1a, J6.2c).

Le journal garde tout ; ce qui se règle, c'est ce qui **sort** vers un
collecteur. Trois choses s'éprouvent ici :

- les spans **sont** l'arbre du journal — mêmes identifiants, mêmes parents,
  un sous-agent sous l'appel qui l'a lancé ;
- en ``metadata``, aucun contenu ne part ; en ``content``, il part masqué, et
  la capture se règle client par client ;
- l'export part à la fin d'un run, une fois, depuis le process qui écrit ;
  un collecteur en panne ne fait jamais échouer un run.

L'adaptateur OpenTelemetry est éprouvé à part, jusqu'au réseau : un petit
collecteur OTLP/HTTP reçoit ce que loom envoie et le décode.

Et la trace qui se **relit** (6.2c) : les mêmes spans, sous-runs compris, par
Python, REST, MCP et ``loom inspect`` — sans contenu sans le droit, en clair
avec, sans masquage par motifs ; un run inachevé dit ce qui reste ouvert ; les
corps bruts n'y entrent jamais ; le droit vaut pour chaque agent de l'arbre.
"""

import asyncio
import json
import logging
import re
import threading
import time
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.util import find_spec
from typing import Any

import pytest
from conftest import (
    HOSTILE,
    HOSTILE_SHOWN,
    MODEL,
    QUESTION,
    TREE_ANSWER,
    TREE_QUESTION,
    ConfigFactory,
)

from loom_ia.access import Loom, UnknownRun
from loom_ia.access.cli import main as cli_main
from loom_ia.adapters.bus import InMemoryBus
from loom_ia.adapters.stores import InMemoryEventStore, NotifyingEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.config.keys import fingerprint, new_api_key
from loom_ia.config.models import LoomConfig
from loom_ia.core import bounded_regex
from loom_ia.core.events import Event, JudgeEvaluated, ModelResponded, contents, redacted
from loom_ia.core.model import (
    DEFAULT_TENANT,
    CriterionScore,
    Message,
    RunId,
    SessionId,
    TenantId,
    ToolOutput,
    Usage,
)
from loom_ia.telemetry import (
    BUILTIN_PATTERNS,
    Redactor,
    RunExporter,
    SpanRecord,
    render_trace,
    run_spans,
    run_trace,
)
from loom_ia.telemetry.inspect import WIDTH
from loom_ia.testing import RunJournal, tool_call_message

sans_otel = pytest.mark.skipif(find_spec("opentelemetry") is None, reason="extra 'otel' absent")
avec_otel = pytest.mark.skipif(
    find_spec("opentelemetry") is not None, reason="extra 'otel' présent"
)

MARTIN = TenantId("martin-chauffage")
DUPONT = TenantId("dupont-plomberie")
COURRIEL = "jeanne.martin@exemple.fr"
TELEPHONE = "06 12 34 56 78"
IBAN = "FR76 3000 6000 0112 3456 7890 189"
SECRET = f"Écrire à {COURRIEL} ou au {TELEPHONE}, IBAN {IBAN}."
VARIABLE = "LOOM_OTLP_ESSAI"
OTEL: dict[str, Any] = {"type": "otel", "endpoint_env": VARIABLE}


def config_of(**telemetry: Any) -> LoomConfig:
    return LoomConfig.model_validate({"version": 1, "telemetry": telemetry})


class Recueil:
    """Un collecteur en mémoire : garde ce qu'on lui remet, lot par lot."""

    def __init__(self, *, casse: bool = False) -> None:
        self.lots: list[list[SpanRecord]] = []
        self.ferme = False
        self.casse = casse

    @property
    def name(self) -> str:
        return "recueil"

    @property
    def spans(self) -> list[SpanRecord]:
        return [span for lot in self.lots for span in lot]

    def export(self, spans: Sequence[SpanRecord]) -> None:
        if self.casse:
            raise ConnectionError("collecteur injoignable")
        self.lots.append(list(spans))

    async def aclose(self, grace: float) -> None:
        self.ferme = True


def exporteur(
    store: NotifyingEventStore | InMemoryEventStore,
    recueil: Recueil,
    *,
    content: bool | dict[TenantId, bool] = False,
) -> RunExporter:
    def content_for(tenant: TenantId) -> bool:
        return content if isinstance(content, bool) else content.get(tenant, False)

    return RunExporter(
        store, [recueil], content_for=content_for, redactor=Redactor.of(["email", "phone", "iban"])
    )


def run_complet(
    *, tenant: TenantId = DEFAULT_TENANT, question: str = QUESTION, session: str = "atelier"
) -> RunJournal:
    journal = RunJournal(session_id=SessionId(session), tenant_id=tenant)
    journal.start(question)
    journal.model_turn(tool_call_message(("c1", "calculer", {"expr": "12*7+3"})))
    journal.tool_results({"c1": ToolOutput.text("87")})
    journal.model_turn(Message.assistant(f"87. {SECRET}"))
    journal.complete()
    return journal


async def evenements(journal: RunJournal) -> list[Event]:
    store = InMemoryEventStore()
    return await store.append(journal.take(), expected_seq=0)


def attributs(spans: Sequence[SpanRecord]) -> list[str]:
    """Toutes les valeurs texte d'un lot de spans, attributs et événements."""
    seen: list[str] = []
    for span in spans:
        seen += [str(v) for v in span.attributes.values()]
        for event in span.events:
            seen += [str(v) for v in event.attributes.values()]
    return seen


# --- La config ---------------------------------------------------------------


def test_by_default_nothing_but_metadata_and_the_four_patterns() -> None:
    config = config_of()
    assert config.telemetry.capture.exports == "metadata"
    assert config.telemetry.redaction.redactor().names == ("secret", "email", "phone", "iban")
    assert config.telemetry.exporters == ()


@pytest.mark.parametrize(
    ("root", "tenants", "named"),
    [
        ({"capture": {"exports": "content"}}, [], "la racine"),
        (
            {},
            [{"id": "martin-chauffage", "telemetry": {"capture": {"exports": "content"}}}],
            "martin",
        ),
    ],
)
def test_content_with_nowhere_to_go_is_refused(
    root: dict[str, Any], tenants: list[dict[str, Any]], named: str
) -> None:
    with pytest.raises(ValueError, match=f"capture 'content' \\({named}.*sans collecteur"):
        LoomConfig.model_validate({"version": 1, "telemetry": root, "tenants": tenants})


def test_a_tenant_overrides_the_capture_and_only_it() -> None:
    config = LoomConfig.model_validate(
        {
            "version": 1,
            "telemetry": {"capture": {"exports": "content"}, "exporters": [{"type": "otel"}]},
            "tenants": [
                {"id": str(MARTIN), "telemetry": {"capture": {"exports": "metadata"}}},
                {"id": str(DUPONT)},
            ],
        }
    )
    assert config.capture_for(MARTIN).exports == "metadata"
    assert config.capture_for(DUPONT).exports == "content"
    # Un client inconnu de la config a la règle commune.
    assert config.capture_for(TenantId("inconnu")).exports == "content"


@pytest.mark.parametrize(
    ("telemetry", "message"),
    [
        ({"bus": {}}, "le bus se déclare dans 'storage.bus'"),
        ({"capture": {"raw_max_bytes": 0}}, "greater than 0"),
        ({"exporters": [{"type": "jsonl"}]}, "seul 'otel' existe"),
        ({"redaction": {"patterns": ["email", "nir"]}}, "motif 'nir' inconnu"),
        ({"redaction": {"patterns": ["email", "email"]}}, "déclaré deux fois : email"),
        ({"redaction": {"patterns": [{"name": "x", "regex": "a*"}]}}, "accepte une chaîne vide"),
        ({"redaction": {"patterns": [{"name": "x", "regex": "("}]}}, "expression invalide"),
        ({"redaction": {"patterns": [{"name": "iban", "regex": "x"}]}}, "est un motif fourni"),
        ({"redaction": {"patterns": [{"name": "secret", "regex": "x"}]}}, "est un motif fourni"),
    ],
)
def test_what_the_telemetry_block_refuses(telemetry: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        config_of(**telemetry)


# --- Le masquage par motifs --------------------------------------------------


def test_the_three_patterns_mask_what_looks_personal() -> None:
    masque = Redactor.of(["email", "phone", "iban"])
    texte = masque.text(f"{SECRET} Ou +33 6 12 34 56 78, ou +44 20 7946 0958.")
    assert COURRIEL not in texte and "06 12" not in texte and "3000 6000" not in texte
    assert "7946" not in texte
    assert texte.count("[phone]") == 3
    assert "[email]" in texte and "[iban]" in texte


def test_business_numbers_are_left_alone() -> None:
    masque = config_of().telemetry.redaction.redactor()
    texte = (
        "Devis D-2026-042, 1 840 €, SIRET 732 829 320 00074, le 12/10/2026 à 14h30, 21000. "
        "Mot de passe oublié ? Réf. sk-8, ticket gh-12, tâche task-2026-042."
    )
    assert masque.text(texte) == texte


def test_masking_walks_json_keys_and_values() -> None:
    masque = Redactor.of(["email", ("devis", r"D-\d{4}-\d{3}")])
    assert masque.json({COURRIEL: ["D-2026-042", 3, None]}) == {"[email]": ["[devis]", 3, None]}


def test_an_address_after_a_long_word_is_masked_without_waiting() -> None:
    """Un long mot sans « @ » ne relance plus la lecture à chacune de ses lettres."""
    masque = Redactor.of(["email"])
    mot = "a" * 40_000
    start = time.perf_counter()
    assert masque.text(f"{mot} écrire à jeanne@exemple.fr") == f"{mot} écrire à [email]"
    assert time.perf_counter() - start < 3


def test_addresses_stuck_together_are_each_masked() -> None:
    assert Redactor.of(["email"]).text("a@b.fr+c@d.fr..e@f.fr") == "[email][email][email]"


@pytest.fixture
def short_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bounded_regex, "REGEX_TIMEOUT", 0.2)


@pytest.mark.usefixtures("short_timeout")
def test_a_pattern_that_does_not_finish_masks_the_whole_text() -> None:
    """Un motif sans réponse n'a pas dit ce qu'il fallait masquer : rien ne sort."""
    masque = Redactor.of(["email", ("lent", "^(a|aa)+$")])
    lent = "a" * 34 + "!"  # ``regex`` y met plus de 2 s, ``re`` plus d'une
    assert masque.text(lent) == "[lent : délai dépassé, texte masqué]"
    assert masque.json({lent: [lent, f"{COURRIEL}"]}) == {
        "[lent : délai dépassé, texte masqué]": ["[lent : délai dépassé, texte masqué]", "[email]"]
    }


def test_a_pattern_of_the_regex_engine_is_accepted_by_the_config() -> None:
    config = config_of(redaction={"patterns": [{"name": "nom", "regex": r"\p{Lu}{3,}"}]})
    assert config.telemetry.redaction.redactor().text("le sieur DUPONT") == "le sieur [nom]"


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-AbCdEf0123456789AbCdEf0123456789",
        "sk-ant-api03-AbCdEf0123456789AbCdEf0123456789",
        # Coupés en deux : un scanner de secrets ne doit pas les prendre pour des vrais.
        "sk_" + "live_0123456789abcdefABCDEF",
        "ghp_" + "0123456789abcdefABCDEF0123456789abcd",
        "github_pat_" + "11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz",
        "AKIAIOSFODNN7EXAMPLE",
        "xoxb-" + "0612345678-0612345678-AbCdEfGhIjKl",
        "AIza" + "Sy0123456789abcdefghijklmnopqrstuvw",
        "Bearer abc.def-ghi_jkl~mno+pqr/stu==",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJl",
        "password=hunter2-hunter2",
        "api_key: 0123456789abcdef",
        "mot de passe : Azerty123!",
    ],
)
def test_a_secret_is_masked_whole_and_before_anything_else(secret: str) -> None:
    """Masqué en entier : ni le téléphone ni l'IBAN n'y mordent pour en laisser filer le reste."""
    masque = config_of().telemetry.redaction.redactor()
    assert masque.text(f"Clé {secret} fin") == "Clé [secret] fin"


def test_an_address_keeps_its_host_but_not_its_credentials() -> None:
    masque = config_of().telemetry.redaction.redactor()
    assert masque.text("postgresql://loom:s3cr3t@localhost/loom") == (
        "postgresql://[secret]@localhost/loom"
    )


def test_a_private_key_is_masked_to_its_end_or_to_the_end_of_the_text() -> None:
    masque = config_of().telemetry.redaction.redactor()
    cle = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\nAAAABG5vbmU=\n"
    fin = "-----END OPENSSH PRIVATE KEY-----"
    assert masque.text(f"Voici :\n{cle}{fin}\nMerci.") == "Voici :\n[secret]\nMerci."
    assert masque.text(f"Coupée :\n{cle}") == "Coupée :\n[secret]"


@pytest.mark.parametrize(
    "numero",
    [
        "0033 6 12 34 56 78",
        "0033612345678",
        "+33 (0)6 12 34 56 78",
        "+33(0)612345678",
        "0033 (0)6.12.34.56.78",
    ],
)
def test_a_french_number_is_masked_whatever_its_prefix(numero: str) -> None:
    assert Redactor.of(["phone"]).text(f"Appeler le {numero}.") == "Appeler le [phone]."


@pytest.mark.parametrize(
    "iban", [IBAN, IBAN.lower(), "Fr76 3000 6000 0112 3456 7890 189", "fr7630006000011234567890189"]
)
def test_an_iban_is_masked_whatever_its_case(iban: str) -> None:
    assert Redactor.of(["iban"]).text(f"IBAN {iban}.") == "IBAN [iban]."


def test_keys_masked_alike_keep_every_entry() -> None:
    """Deux clés qui se masquent pareil ne s'écrasent plus : la seconde est numérotée."""
    masque = Redactor.of(["email"])
    table: dict[str, Any] = {"a@b.fr": 1, "c@d.fr": 2, "[email]": 3, "x": {"e@f.fr": 4}}
    assert masque.json(table) == {"[email]": 1, "[email]#2": 2, "[email]#3": 3, "x": {"[email]": 4}}


def test_the_provided_patterns_always_apply_those_not_named_first() -> None:
    """Ses motifs s'ajoutent aux fournis ; ceux que la liste ne cite pas passent avant elle."""
    devis = {"name": "devis", "regex": r"D-\d{4}-\d{3}"}
    assert config_of().telemetry.redaction.redactor().names == BUILTIN_PATTERNS
    seul = config_of(redaction={"patterns": [devis]}).telemetry.redaction.redactor()
    assert seul.names == ("secret", "email", "phone", "iban", "devis")
    assert seul.text(f"{SECRET} Devis D-2026-042.") == (
        "Écrire à [email] ou au [phone], IBAN [iban]. Devis [devis]."
    )
    place = config_of(redaction={"patterns": ["iban", devis, "email"]}).telemetry.redaction
    assert place.redactor().names == ("secret", "phone", "iban", "devis", "email")


# --- Le contenu d'un événement ------------------------------------------------


async def test_contents_is_exactly_what_redaction_removes() -> None:
    for event in await evenements(run_complet()):
        removed = redacted(event)["payload"]
        assert isinstance(removed, dict)
        assert sorted(contents(event)) == sorted(removed.get("redacted", []))  # type: ignore[arg-type]


async def test_contents_follow_a_path_through_a_list() -> None:
    journal = RunJournal()
    journal.start(QUESTION)
    verdict = JudgeEvaluated(
        judge="fidelite",
        target="output",
        model_id="fake",
        criteria=(
            CriterionScore(name="a", score=1, min_score=0.5, blocking=False, reason="exact"),
            CriterionScore(name="b", score=1, min_score=0.5, blocking=False),
            CriterionScore(name="c", score=1, min_score=0.5, blocking=False, reason="sourcé"),
        ),
        passed=True,
        blocked=False,
    )
    store = InMemoryEventStore()
    [event] = await store.append([journal.scope.draft(verdict)], expected_seq=0)
    # La raison vide ne cache rien : elle n'est pas du contenu à exporter.
    assert contents(event) == {"criteria[].reason": ["exact", "sourcé"]}


# --- Les spans ----------------------------------------------------------------


async def test_metadata_carries_no_content() -> None:
    spans = run_spans(await evenements(run_complet()))
    seen = " ".join(attributs(spans))
    assert not [k for s in spans for e in s.events for k in e.attributes if "content" in k]
    for fragment in (QUESTION, "12*7+3", COURRIEL, TELEPHONE, "87."):
        assert fragment not in seen
    # Les métadonnées, elles, sont là : l'outil, le modèle, l'usage.
    assert "calculer" in seen and "fake-model" in seen


async def test_content_leaves_masked() -> None:
    spans = run_spans(
        await evenements(run_complet()),
        content=True,
        redactor=Redactor.of(["email", "phone", "iban"]),
    )
    seen = " ".join(attributs(spans))
    assert QUESTION in seen and "12*7+3" in seen
    assert COURRIEL not in seen and TELEPHONE not in seen and "3000 6000" not in seen
    assert "[email]" in seen and "[phone]" in seen and "[iban]" in seen


async def test_each_model_call_is_a_span_that_lasts_the_call() -> None:
    journal = run_complet()
    # Un appel qui a duré : ``run_complet`` n'en écrit qu'à durée nulle, où un
    # span qui ignorerait la durée commencerait aussi à l'heure de la réponse.
    journal.model_turn(Message.assistant("Encore."))
    drafts = journal.take()
    [lent] = [i for i, d in enumerate(drafts) if d.type == "model.responded"][-1:]
    payload = drafts[lent].payload
    assert isinstance(payload, ModelResponded)
    drafts[lent] = journal.scope.draft(payload.model_copy(update={"latency_ms": 250.0}))
    events = await InMemoryEventStore().append(drafts, expected_seq=0)
    spans = run_spans(events)
    # Les spans sont rangés par début : on les retrouve par leur identifiant.
    chats = {s.span_id: s for s in spans if s.kind == "chat"}
    responded = [e for e in events if e.type == "model.responded"]
    assert sorted(chats) == sorted(e.event_id for e in responded)
    assert all(chats[e.event_id].parent_span_id == e.span_id for e in responded)
    assert all(chats[e.event_id].end == e.ts for e in responded)
    dernier = chats[responded[-1].event_id]
    assert dernier.start == responded[-1].ts - timedelta(milliseconds=250)
    assert dernier.attributes["loom.latency_ms"] == 250.0
    assert dernier.attributes["gen_ai.request.model"] == "fake-model"


async def test_a_failed_run_names_its_error_type_never_its_message() -> None:
    journal = RunJournal()
    journal.start(QUESTION)
    journal.fail("ValueError", SECRET)
    [run] = [s for s in run_spans(await evenements(journal)) if s.kind == "run"]
    assert run.error == "ValueError"
    assert run.attributes["loom.run.status"] == "failed"
    assert COURRIEL not in " ".join(attributs([run]))


async def test_the_spans_are_the_tree_of_the_journal(demo: ConfigFactory) -> None:
    async with Loom(load_config(demo())) as loom:
        recueil = Recueil()
        export = exporteur(loom.store, recueil)
        with loom.store.listen(export, own=True):
            result = await loom.run("demo", QUESTION)
            await export.aclose(5)
        events = await loom.store.read(DEFAULT_TENANT, SessionId(result.run_id))
    spans = recueil.spans
    by_id = {span.span_id: span for span in spans}
    [run] = [s for s in spans if s.kind == "run"]
    assert run.parent_span_id is None and run.trace_id == result.run_id
    assert run.name == "invoke_agent demo"
    # Chaque span non racine a son parent dans le lot : l'arbre est entier.
    assert all(s.parent_span_id in by_id for s in spans if s is not run)
    # Les identifiants sont ceux du journal.
    assert {e.span_id for e in events} <= set(by_id)
    [tool] = [s for s in spans if s.kind == "tool"]
    assert tool.name == "execute_tool calculer"
    assert by_id[tool.parent_span_id or ""].kind == "step"
    assert all(s.start <= s.end for s in spans)


async def test_a_subagent_hangs_under_the_call_that_launched_it(tree: ConfigFactory) -> None:
    async with Loom(load_config(tree())) as loom:
        recueil = Recueil()
        export = exporteur(loom.store, recueil)
        with loom.store.listen(export, own=True):
            result = await loom.run("demo", "Combien font 2 + 2 ?")
            await export.aclose(5)
    # Deux runs, deux exports, une seule trace.
    assert len(recueil.lots) == 2
    spans = recueil.spans
    assert {s.trace_id for s in spans} == {result.run_id}
    roots = [s for s in spans if s.kind == "run"]
    child = next(s for s in roots if s.parent_span_id is not None)
    call = next(s for s in spans if s.span_id == child.parent_span_id)
    assert call.kind == "tool" and call.name == "execute_tool verifier"


# --- L'export -----------------------------------------------------------------


async def test_a_run_is_exported_once_when_it_ends() -> None:
    store = NotifyingEventStore(InMemoryEventStore())
    recueil = Recueil()
    export = exporteur(store, recueil)
    with store.listen(export, own=True):
        journal = run_complet()
        drafts = journal.take()
        # Tout sauf la clôture : rien ne part.
        await store.append(drafts[:-1], expected_seq=0)
        await asyncio.sleep(0)
        assert export.pending == 0 and recueil.lots == []
        await store.append(drafts[-1:], expected_seq=len(drafts) - 1)
        await export.aclose(5)
    assert len(recueil.lots) == 1 and recueil.ferme
    # Le run est relu au journal : tous ses spans y sont, pas seulement le dernier.
    assert {s.kind for s in recueil.spans} >= {"run", "chat"}


async def test_an_unfinished_run_is_not_exported() -> None:
    store = NotifyingEventStore(InMemoryEventStore())
    recueil = Recueil()
    export = exporteur(store, recueil)
    with store.listen(export, own=True):
        journal = RunJournal(session_id=SessionId("atelier"))
        journal.start(QUESTION)
        journal.model_turn(tool_call_message(("c1", "calculer", {"expr": "1"})))
        await store.append(journal.take(), expected_seq=0)
        await export.aclose(5)
    assert recueil.lots == []


async def test_what_the_bus_brings_is_exported_by_the_process_that_wrote_it() -> None:
    """Deux journaux notifiants sur un même journal : la forme de deux workers."""
    inner = InMemoryEventStore()
    bus = InMemoryBus()
    ici = NotifyingEventStore(inner, bus=bus, source="ici")
    ailleurs = NotifyingEventStore(inner, bus=bus, source="ailleurs")
    recueil = Recueil()
    vus: list[Event] = []
    export = exporteur(ici, recueil)
    following = asyncio.create_task(ici.follow())
    with ici.listen(export, own=True), ici.listen(vus.append):
        for _ in range(100):
            if bus.subscribers:
                break
            await asyncio.sleep(0.01)
        written = await ailleurs.append(run_complet().take(), expected_seq=0)
        for _ in range(20):
            await asyncio.sleep(0.01)
        await export.aclose(5)
    following.cancel()
    await bus.aclose()
    # L'abonné ordinaire a bien vu passer le run de l'autre…
    assert [e.event_id for e in vus] == [e.event_id for e in written]
    # … mais l'export le laisse à celui qui l'a écrit.
    assert recueil.lots == []


async def test_the_capture_is_decided_tenant_by_tenant() -> None:
    store = NotifyingEventStore(InMemoryEventStore())
    recueil = Recueil()
    export = exporteur(store, recueil, content={DUPONT: True})
    with store.listen(export, own=True):
        for tenant in (DUPONT, MARTIN):
            await store.append(
                run_complet(tenant=tenant, session=str(tenant)).take(), expected_seq=0
            )
        await export.aclose(5)
    par_client = {
        lot[0].attributes["loom.tenant_id"]: " ".join(attributs(lot)) for lot in recueil.lots
    }
    assert QUESTION in par_client[DUPONT]
    assert QUESTION not in par_client[MARTIN]


async def test_a_broken_collector_never_fails_a_run(
    demo: ConfigFactory, caplog: pytest.LogCaptureFixture
) -> None:
    async with Loom(load_config(demo())) as loom:
        export = exporteur(loom.store, Recueil(casse=True))
        with loom.store.listen(export, own=True), caplog.at_level(logging.WARNING):
            result = await loom.run("demo", QUESTION)
            await export.aclose(5)
    assert result.ok
    assert "non remis (collecteur injoignable)" in caplog.text


# --- Le montage par la config --------------------------------------------------


def test_without_exporters_nothing_listens(demo: ConfigFactory) -> None:
    loom = Loom(load_config(demo()))
    assert loom.exporter is None and loom.store.listeners == 0


@sans_otel
def test_an_empty_endpoint_variable_mounts_nothing_and_says_so(
    demo: ConfigFactory, caplog: pytest.LogCaptureFixture
) -> None:
    config = load_config(demo(telemetry={"exporters": [OTEL]}))
    with caplog.at_level(logging.WARNING):
        loom = Loom(config, environ={})
    assert loom.exporter is None
    assert f"la variable '{VARIABLE}' est vide ou absente" in caplog.text


@sans_otel
def test_in_prod_an_empty_endpoint_variable_is_refused(demo: ConfigFactory) -> None:
    config = load_config(
        demo(
            profile="prod",
            telemetry={"exporters": [OTEL]},
        )
    )
    with pytest.raises(ConfigError, match=f"Profil prod : .*'{VARIABLE}'"):
        Loom(config, environ={})


@avec_otel
def test_without_the_extra_the_refusal_names_it(demo: ConfigFactory) -> None:
    config = load_config(demo(telemetry={"exporters": [OTEL]}))
    with pytest.raises(ConfigError, match=r"loom-ia\[otel\]"):
        Loom(config, environ={VARIABLE: "http://127.0.0.1:4318"})


# --- Jusqu'au collecteur ---------------------------------------------------------


@contextmanager
def collecteur() -> Generator[tuple[str, list[Any], list[dict[str, str]]]]:
    """Un collecteur OTLP/HTTP minimal : décode ce qu'il reçoit, répond 200."""
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    recus: list[Any] = []
    entetes: list[dict[str, str]] = []

    class Recepteur(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            corps = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            demande = ExportTraceServiceRequest()
            demande.ParseFromString(corps)
            recus.append((self.path, demande))
            entetes.append({k.lower(): v for k, v in self.headers.items()})
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            pass

    serveur = ThreadingHTTPServer(("127.0.0.1", 0), Recepteur)
    fil = threading.Thread(target=serveur.serve_forever, daemon=True)
    fil.start()
    try:
        yield f"http://127.0.0.1:{serveur.server_address[1]}", recus, entetes
    finally:
        serveur.shutdown()
        serveur.server_close()


@sans_otel
async def test_the_spans_reach_an_otlp_collector(demo: ConfigFactory) -> None:
    config = load_config(
        demo(
            telemetry={
                "capture": {"exports": "content"},
                "exporters": [
                    {
                        "type": "otel",
                        "endpoint_env": VARIABLE,
                        "headers_env": "LOOM_OTLP_ENTETES",
                        "service_name": "atelier",
                    }
                ],
            }
        )
    )
    with collecteur() as (adresse, recus, entetes):
        environ = {VARIABLE: adresse, "LOOM_OTLP_ENTETES": "x-cle=s%C3%A9same"}
        async with Loom(config, environ=environ) as loom:
            assert loom.exporter is not None
            result = await loom.run("demo", f"{QUESTION} Réponse à {COURRIEL}")
        # La fermeture a vidé le processeur : tout est arrivé.
    assert recus and all(chemin == "/v1/traces" for chemin, _ in recus)
    assert entetes[0]["x-cle"] == "sésame"
    spans = [
        (resource, span)
        for _, demande in recus
        for resource in demande.resource_spans
        for scope in resource.scope_spans
        for span in scope.spans
    ]
    services = {
        a.value.string_value
        for resource, _ in spans
        for a in resource.resource.attributes
        if a.key == "service.name"
    }
    assert services == {"atelier"}
    [run] = [span for _, span in spans if span.name == "invoke_agent demo"]
    # L'identifiant de trace est celui du run : il se cherche tel quel.
    assert run.trace_id.hex() == result.run_id.replace("-", "")
    contenu = json.dumps(
        [
            a.value.string_value
            for _, span in spans
            for event in span.events
            for a in event.attributes
            if a.key.startswith("loom.content.")
        ],
        ensure_ascii=False,
    )
    assert QUESTION in contenu and COURRIEL not in contenu and "[email]" in contenu


@sans_otel
async def test_the_otel_translation_keeps_ids_parents_and_status() -> None:
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from loom_ia.adapters.telemetry.otel import OtelSpanSink, span_id, trace_id

    journal = RunJournal()
    journal.start(QUESTION)
    journal.fail("ValueError", "raté")
    events = await evenements(journal)
    records = run_spans(events)
    memoire = InMemorySpanExporter()
    sink = OtelSpanSink(memoire)
    sink.export(records)
    await sink.aclose(5)
    [otel] = memoire.get_finished_spans()
    [record] = records
    assert otel.context is not None
    assert otel.context.trace_id == trace_id(record.trace_id)
    assert otel.context.span_id == span_id(record.span_id)
    assert otel.parent is None
    assert otel.status.description == "ValueError"
    assert otel.start_time is not None and otel.end_time is not None
    assert otel.end_time >= otel.start_time
    # Un identifiant qui n'est pas un UUID passe par un condensé, stable.
    assert span_id("relance-42") == span_id("relance-42") != span_id("relance-43")


def _span(i: int, *, tenant: str = "martin", **attributes: Any) -> SpanRecord:
    """Un span d'outil, avec ce qu'il faut d'attributs."""
    from datetime import UTC, datetime

    moment = datetime.now(UTC)
    return SpanRecord(
        trace_id="0190a5b4-1c2d-7e3f-8a9b-0c1d2e3f4a5b",
        span_id=f"span-{i}",
        parent_span_id=None,
        name="execute_tool calculer",
        kind="tool",
        start=moment,
        end=moment,
        attributes={"loom.tenant_id": tenant, **attributes},
    )


def _slow(delay: float) -> Any:
    """Un collecteur en mémoire qui met ``delay`` secondes à prendre chaque lot."""
    from opentelemetry.sdk.trace.export import SpanExportResult
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    class Lent(InMemorySpanExporter):
        def export(self, spans: Sequence[Any]) -> SpanExportResult:
            time.sleep(delay)
            return super().export(spans)

    return Lent()


@sans_otel
async def test_a_burst_of_spans_is_not_dropped_by_the_otel_queue() -> None:
    """5 000 spans d'un coup, un collecteur à 50 ms par lot : la file du SDK (2 048) en jetait."""
    from loom_ia.adapters.telemetry.otel import OtelSpanSink

    lent = _slow(0.05)
    sink = OtelSpanSink(lent, schedule_delay=0.01)
    sink.export([_span(i) for i in range(5_000)])
    await sink.aclose(30)
    assert len(lent.get_finished_spans()) == 5_000


@sans_otel
async def test_the_otel_queue_size_variable_still_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """``OTEL_BSP_MAX_QUEUE_SIZE`` posé, c'est lui qui borne la file."""
    from loom_ia.adapters.telemetry.otel import OtelSpanSink

    monkeypatch.setenv("OTEL_BSP_MAX_QUEUE_SIZE", "600")
    lent = _slow(0.05)
    sink = OtelSpanSink(lent, schedule_delay=0.01)
    sink.export([_span(i) for i in range(1_000)])
    await sink.aclose(30)
    assert len(lent.get_finished_spans()) < 1_000


@sans_otel
async def test_closing_a_sink_whose_collector_hangs_takes_its_grace_not_more() -> None:
    """``shutdown`` attendait le collecteur lot après lot, sans autre borne que son délai."""
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

    from loom_ia.adapters.telemetry.otel import OtelSpanSink

    relache = threading.Event()

    class Muet(SpanExporter):
        def export(self, spans: Sequence[Any]) -> SpanExportResult:
            relache.wait(30)
            return SpanExportResult.SUCCESS

    sink = OtelSpanSink(Muet(), schedule_delay=0.01)
    sink.export([_span(i) for i in range(10)])
    await asyncio.sleep(0.1)
    start = time.perf_counter()
    try:
        await sink.aclose(0.3)
        elapsed = time.perf_counter() - start
    finally:
        relache.set()
    assert elapsed < 2, f"fermeture en {elapsed:.2f} s pour un délai de 0,3 s"


@sans_otel
@pytest.mark.parametrize(("limit", "expected"), [(None, 16_384), ("1000", 1_000)])
async def test_a_long_content_is_cut_and_says_so(
    monkeypatch: pytest.MonkeyPatch, limit: str | None, expected: int
) -> None:
    """``loom.content.*`` partait entier : un résultat d'outil de 50 000 caractères aussi."""
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from loom_ia.adapters.telemetry.otel import OtelSpanSink

    if limit is None:
        monkeypatch.delenv("OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT", raising=False)
    else:
        monkeypatch.setenv("OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT", limit)
    memoire = InMemorySpanExporter()
    sink = OtelSpanSink(memoire)
    long = "x" * 50_000
    sink.export([_span(1, **{"loom.content.output": long, "loom.court": "court"})])
    await sink.aclose(5)
    [otel] = memoire.get_finished_spans()
    assert otel.attributes is not None
    coupe = str(otel.attributes["loom.content.output"])
    assert len(coupe) == expected and coupe.endswith("[coupé : 50000 caractères]")
    assert otel.attributes["loom.court"] == "court"


@sans_otel
def test_a_trace_id_keeps_a_canonical_uuid_and_separates_the_rest() -> None:
    """Un run nommé pareil chez deux clients, ou un UUID écrit autrement, partageaient une trace."""
    from loom_ia.adapters.telemetry.otel import trace_id

    canonique = "0190a5b4-1c2d-7e3f-8a9b-0c1d2e3f4a5b"
    # L'identifiant que loom tire reste la trace : on le cherche tel quel au collecteur.
    assert (
        trace_id(canonique, "martin")
        == trace_id(canonique, "dupont")
        == int(canonique.replace("-", ""), 16)
    )
    autres = [canonique.upper(), f"{{{canonique}}}", canonique.replace("-", "")]
    assert len({trace_id(value, "martin") for value in [canonique, *autres]}) == 4
    assert trace_id("devis-42", "martin") != trace_id("devis-42", "dupont")
    assert trace_id("devis-42", "martin") == trace_id("devis-42", "martin")


@sans_otel
async def test_the_otel_span_carries_the_trace_of_its_client() -> None:
    from dataclasses import replace

    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from loom_ia.adapters.telemetry.otel import OtelSpanSink, trace_id

    memoire = InMemorySpanExporter()
    sink = OtelSpanSink(memoire)
    # Un run nommé par l'appelant : son identifiant n'est pas un UUID.
    nomme = replace(_span(1, tenant="dupont"), trace_id="devis-42")
    sink.export([nomme])
    await sink.aclose(5)
    [otel] = memoire.get_finished_spans()
    assert otel.context is not None
    assert otel.context.trace_id == trace_id("devis-42", "dupont") != trace_id("devis-42", "martin")


# --- Traces à relire : API, MCP, inspect (J6.2c) -----------------------------------

TRACE_SESSION = SessionId("relue")


async def test_a_trace_holds_the_spans_of_the_run_and_its_subruns(tree: ConfigFactory) -> None:
    async with Loom(load_config(tree())) as loom:
        result = await loom.run("demo", TREE_QUESTION)
        trace = await loom.trace(result.run_id)
        events = await loom.events(result.run_id)
        state = await loom.state(result.run_id)

    runs = [span for span in trace.spans if span.kind == "run"]
    assert {span.run_id for span in runs} == {e.run_id for e in events}
    assert len(runs) == 2
    # Ce sont les spans de l'export, run par run.
    for run_id in {e.run_id for e in events}:
        mine = sorted(s.span_id for s in trace.spans if s.run_id == run_id)
        assert mine == sorted(
            s.span_id for s in run_spans([e for e in events if e.run_id == run_id])
        )
    # Le run de l'enfant pend sous l'appel qui l'a lancé.
    enfant = next(span for span in runs if span.run_id != result.run_id)
    appel = next(span for span in trace.spans if span.span_id == enfant.parent_span_id)
    assert appel.kind == "tool" and appel.attributes["gen_ai.tool.name"] == "verifier"
    # L'en-tête est celui du run.
    assert (trace.status, trace.finished) == ("completed", True)
    assert trace.cost_usd == state.cost_usd and trace.usage == state.usage
    assert trace.output == TREE_ANSWER
    assert not any(span.open for span in trace.spans)


async def test_without_the_right_a_trace_has_no_content_at_all(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo()) as loom:
        result = await loom.run("demo", SECRET, session_id=TRACE_SESSION)
        complete = await loom.trace(result.run_id, session_id=TRACE_SESSION)
        nue = await loom.trace(result.run_id, session_id=TRACE_SESSION, content=False)

    evenements = [event for span in nue.spans for event in span.events]
    assert evenements and all(event.content is None for event in evenements)
    assert nue.output is None and nue.content is False
    # La charge reste — sans ses champs de contenu, qu'elle nomme.
    appel = next(e for e in evenements if e.name == "tool.called")
    assert "arguments" not in appel.data and appel.data["redacted"] == ["arguments"]
    assert COURRIEL not in nue.model_dump_json()
    # Avec le droit, le contenu est là, **en clair** : pas de masquage par motifs (6.2c).
    demande = next(e for span in complete.spans for e in span.events if e.name == "message.user")
    assert demande.content is not None and COURRIEL in json.dumps(demande.content)
    assert complete.output is not None


async def test_an_unfinished_run_has_its_trace_and_says_what_is_open(
    atelier: ConfigFactory,
) -> None:
    async with Loom.from_config(atelier()) as loom:
        run = await loom.run("demo", "Relance.", session_id=TRACE_SESSION)
        en_pause = await loom.trace(run.run_id, session_id=TRACE_SESSION)
        await loom.approve(run.run_id, by="denis", session_id=TRACE_SESSION)
        await loom.drain()
        finie = await loom.trace(run.run_id, session_id=TRACE_SESSION)

    assert (en_pause.status, en_pause.finished) == ("paused", False)
    [racine] = [span for span in en_pause.spans if span.kind == "run"]
    assert racine.open and racine.attributes["loom.run.status"] == "unfinished"
    assert finie.finished and not any(span.open for span in finie.spans)


async def test_exchange_bodies_never_enter_a_trace(demo: ConfigFactory) -> None:
    path = demo(telemetry={"capture": {"raw_exchanges": True}})
    async with Loom.from_config(path) as loom:
        result = await loom.run("demo", QUESTION)
        trace = await loom.trace(result.run_id)

    echanges = [e for span in trace.spans for e in span.events if e.name == "model.exchanged"]
    assert echanges
    for echange in echanges:
        assert echange.content == {}
        assert "request_body" not in echange.data and "response_body" not in echange.data


async def test_an_unknown_run_has_no_trace(demo: ConfigFactory) -> None:
    async with Loom.from_config(demo()) as loom:
        with pytest.raises(UnknownRun):
            await loom.trace(RunId("absent"))


def _trace_keys() -> tuple[dict[str, Any], dict[str, str]]:
    """Trois clés : tout lire, lire sans contenu, lire l'agent demo seulement."""
    jetons = {nom: new_api_key() for nom in ("complete", "supervision", "parent")}
    security = {
        "api_keys": [
            {
                "id": "complete",
                "hash": fingerprint(jetons["complete"]),
                "scopes": ["run", "read", "read_content"],
            },
            {
                "id": "supervision",
                "hash": fingerprint(jetons["supervision"]),
                "scopes": ["run", "read"],
            },
            {
                "id": "parent",
                "hash": fingerprint(jetons["parent"]),
                "scopes": ["run", "read", "read_content"],
                "agents": ["demo"],
            },
        ]
    }
    return security, jetons


async def test_a_trace_is_read_over_rest_by_scope(tree: ConfigFactory) -> None:
    pytest.importorskip("fastapi", reason="extra 'http' absent")
    pytest.importorskip("httpx2", reason="client HTTP de test absent")
    import httpx2

    from loom_ia.access.http import create_app

    security, jetons = _trace_keys()
    async with Loom.from_config(tree(security=security)) as loom:
        result = await loom.run("demo", TREE_QUESTION)
        attendue = await loom.trace(result.run_id)
        transport = httpx2.ASGITransport(create_app(loom))
        async with httpx2.AsyncClient(transport=transport, base_url="http://loom.test") as http:

            async def lire(qui: str, run_id: str = result.run_id) -> httpx2.Response:
                cle = {"Authorization": f"Bearer {jetons[qui]}"}
                return await http.get(f"/v1/traces/{run_id}", headers=cle)

            complete = await lire("complete")
            supervision = await lire("supervision")
            parent = await lire("parent")
            absent = await lire("complete", "absent")

    assert complete.status_code == 200
    assert complete.json() == attendue.model_dump(mode="json")
    assert supervision.status_code == 200
    vue = supervision.json()
    assert vue["content"] is False and vue["output"] is None
    assert all(e["content"] is None for span in vue["spans"] for e in span["events"])
    assert len(vue["spans"]) == len(attendue.spans)
    # La trace montre le travail du sous-agent : il faut le droit sur lui aussi.
    assert parent.status_code == 403 and "verificateur" in parent.json()["detail"]
    assert absent.status_code == 404


async def test_a_trace_is_a_resource(tree: ConfigFactory) -> None:
    pytest.importorskip("mcp", reason="extra 'mcp' absent")
    from mcp.shared.exceptions import McpError
    from mcp.shared.memory import create_connected_server_and_client_session as connected
    from mcp.types import TextResourceContents
    from pydantic import AnyUrl

    from loom_ia.access import TEMPLATES, TRACES
    from loom_ia.access.mcp_server import create_server

    async with Loom(load_config(tree())) as loom:
        result = await loom.run("demo", TREE_QUESTION)
        attendue = await loom.trace(result.run_id)
        async with connected(create_server(loom)) as client:
            gabarits = await client.list_resource_templates()
            lue = await client.read_resource(AnyUrl(f"{TRACES}/{result.run_id}"))
            with pytest.raises(McpError, match="introuvable"):
                await client.read_resource(AnyUrl(f"{TRACES}/absent"))

    assert f"{TRACES}/{{run_id}}{{?session_id}}" in [
        t.uriTemplate for t in gabarits.resourceTemplates
    ]
    assert len(TEMPLATES) == len(gabarits.resourceTemplates)
    [contenu] = lue.contents
    assert isinstance(contenu, TextResourceContents)
    assert json.loads(contenu.text) == attendue.model_dump(mode="json")


async def test_inspect_reads_the_tree_the_answer_and_the_tally(
    tree: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tree()
    async with Loom(load_config(path)) as loom:
        result = await loom.run("demo", TREE_QUESTION)
        trace = await loom.trace(result.run_id)

    assert await _cli(["--config", str(path), "inspect", result.run_id]) == 0
    sortie = capsys.readouterr().out
    lignes = sortie.splitlines()
    assert lignes[0].startswith(f"Run        : {result.run_id} (agent demo")
    # L'arbre : le run, l'appel du sous-agent, et sous lui le run de l'enfant, plus loin.
    racine = next(i for i, ligne in enumerate(lignes) if ligne.startswith("run demo — completed"))
    appel = next(i for i, ligne in enumerate(lignes) if "sous-agent verifier" in ligne)
    enfant = next(i for i, ligne in enumerate(lignes) if "run verificateur — completed" in ligne)
    assert racine < appel < enfant
    assert _marge(lignes[racine]) < _marge(lignes[appel]) < _marge(lignes[enfant])
    assert f"Réponse finale :\n  {TREE_ANSWER}" in sortie
    chats = sum(1 for s in trace.spans if s.kind == "chat")
    outils = sum(1 for s in trace.spans if s.kind == "tool")
    assert (
        f"Bilan      : {chats} appel(s) de modèle, {outils} appel(s) d'outil, 1 sous-run(s)"
        in sortie
    )

    assert await _cli(["--config", str(path), "inspect", result.run_id, "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == trace.model_dump(mode="json")
    assert await _cli(["--config", str(path), "inspect", "absent"]) == 2
    assert "introuvable" in capsys.readouterr().err


def test_inspect_cuts_a_long_result_and_says_so_unless_whole() -> None:
    """Un extrait se coupe en le disant ; la réponse finale, jamais."""
    long = "x" * 500
    reponse = "Première ligne de la réponse, " + "très longue " * 20 + "\nSeconde ligne."
    journal = RunJournal()
    journal.start(QUESTION)
    journal.model_turn(tool_call_message(("c1", "calculer", {"expr": "1+1"})))
    journal.tool_results({"c1": ToolOutput.text(long)})
    journal.model_turn(Message.assistant(reponse)).complete()
    events = [d.to_event(i + 1) for i, d in enumerate(journal.take())]
    trace = run_trace(events, events[0].run_id, content=True)

    for full in (False, True):
        lignes = render_trace(trace, full=full)
        debut = lignes.index("Réponse finale :")
        assert lignes[debut + 1 : debut + 3] == [f"  {ligne}" for ligne in reponse.splitlines()]
    coupe = [ligne for ligne in render_trace(trace) if "résultat" in ligne]
    entier = [ligne for ligne in render_trace(trace, full=True) if "résultat" in ligne]
    assert len(coupe) == 1 and coupe[0].endswith("…") and long not in coupe[0]
    assert len(coupe[0].lstrip(" ·")) <= WIDTH
    assert len(entier) == 1 and entier[0].endswith(long)


def test_usage_attributes_follow_the_genai_conventions() -> None:
    """``gen_ai.usage.input_tokens`` compte le cache, et ``cache_*`` en dit la part.

    Test K4 du 21/10 : l'entrée exportée était hors cache (50 tokens pour un run
    de 3 900) et le cache portait des noms propres à loom, qu'aucun outil ne lit.
    """
    journal = RunJournal()
    journal.start(QUESTION)
    journal.model_turn(
        tool_call_message(("c1", "calculer", {"expr": "1+1"})),
        usage=Usage(input_tokens=100, cache_read_tokens=900, output_tokens=20),
    )
    journal.tool_results({"c1": ToolOutput.text("2")})
    journal.model_turn(
        Message.assistant("2."),
        usage=Usage(input_tokens=50, cache_write_tokens=300, output_tokens=10, reasoning_tokens=4),
    )
    journal.complete()
    events = [d.to_event(i + 1) for i, d in enumerate(journal.take())]
    spans = run_spans(events)
    chats = [s.attributes for s in spans if s.kind == "chat"]
    [run] = [s.attributes for s in spans if s.kind == "run"]

    assert [a["gen_ai.usage.input_tokens"] for a in chats] == [1000, 350]
    assert [a["gen_ai.usage.cache_read.input_tokens"] for a in chats] == [900, 0]
    assert [a["gen_ai.usage.cache_write.input_tokens"] for a in chats] == [0, 300]
    assert run["gen_ai.usage.input_tokens"] == 1350
    assert run["gen_ai.usage.output_tokens"] == 30
    assert run["gen_ai.usage.cache_read.input_tokens"] == 900
    assert run["gen_ai.usage.cache_write.input_tokens"] == 300
    assert run["loom.usage.reasoning_tokens"] == 4
    assert not [key for a in [*chats, run] for key in a if key.startswith("loom.usage.cache")]


def test_inspect_counts_the_cache_in_each_call_as_in_the_header() -> None:
    """Les tokens d'un appel comptent son entrée en cache : les lignes font l'en-tête."""
    journal = RunJournal()
    journal.start(QUESTION)
    journal.model_turn(
        tool_call_message(("c1", "calculer", {"expr": "1+1"})),
        usage=Usage(input_tokens=100, cache_read_tokens=900, output_tokens=20),
    )
    journal.tool_results({"c1": ToolOutput.text("2")})
    journal.model_turn(
        Message.assistant("2."),
        usage=Usage(input_tokens=50, cache_write_tokens=300, output_tokens=10),
    )
    journal.complete()
    events = [d.to_event(i + 1) for i, d in enumerate(journal.take())]
    trace = run_trace(events, events[0].run_id, content=True)

    lignes = render_trace(trace)
    appels = [ligne.strip() for ligne in lignes if ligne.strip().startswith("modèle ")]
    assert len(appels) == 2
    assert "1000 → 20 tokens (dont 900 lus en cache)" in appels[0]
    assert "350 → 10 tokens (dont 300 écrits en cache)" in appels[1]
    lus = [re.search(r"(\d+) → (\d+) tokens", appel) for appel in appels]
    entree = sum(int(m.group(1)) for m in lus if m is not None)
    sortie = sum(int(m.group(2)) for m in lus if m is not None)
    assert lignes[3].startswith(f"Usage      : {entree} → {sortie} tokens")
    assert (entree, sortie) == (trace.usage.prompt_tokens, trace.usage.output_tokens)


async def test_inspect_says_a_call_refused_before_running(demo: ConfigFactory) -> None:
    """Un appel refusé avant de partir le dit ; une erreur d'outil n'a pas de marque en double."""
    script: list[dict[str, Any]] = [
        {"tool_calls": [{"name": "calculer", "arguments": {"expression": "1+1"}}]},
        {"tool_calls": [{"name": "calculer", "arguments": {"expr": "1/0"}}]},
        {"text": "Raté deux fois."},
    ]
    path = demo(
        models=[{"id": "FAKE", "sdk": "fake", "model": "fake-1", "params": {"script": script}}]
    )
    async with Loom(load_config(path)) as loom:
        result = await loom.run("demo", QUESTION)
        trace = await loom.trace(result.run_id)

    outils = [span for span in trace.spans if span.kind == "tool"]
    assert len(outils) == 2
    lignes = [ligne.strip() for ligne in render_trace(trace)]
    tetes = [ligne for ligne in lignes if "calculer —" in ligne]
    assert tetes[0] == "appel calculer — refusé avant exécution"
    # Ce qu'il a reçu dit pourquoi ; l'appel suivant, lui, est parti et a échoué.
    raison = lignes[lignes.index(tetes[0]) + 1]
    assert raison.startswith("· résultat  : (erreur) ") and "non conformes" in raison
    assert tetes[1].startswith("outil calculer — ") and tetes[1].endswith(" (erreur)")
    assert not any("[tool.completed]" in ligne for ligne in lignes)
    assert lignes[-1].endswith("2 appel(s) d'outil dont 1 refusé(s) avant exécution")


async def test_inspect_shows_control_characters_without_obeying_them(
    demo: ConfigFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ce que rendent le modèle et les outils se lit sans piloter le terminal, en entier ou non."""
    script: list[dict[str, Any]] = [
        {"tool_calls": [{"name": f"inconnu{HOSTILE}", "arguments": {}}]},
        {"text": HOSTILE},
    ]
    path = demo(
        models=[{**MODEL, "params": {"script": script}}],
        storage={"events": {"backend": "jsonl", "path": "data"}},
    )
    async with Loom(load_config(path)) as loom:
        result = await loom.run("demo", QUESTION)

    for full in ([], ["--full"]):
        assert await _cli(["--config", str(path), "inspect", result.run_id, *full]) == 0
        sortie = capsys.readouterr().out
        assert f"appel inconnu{HOSTILE_SHOWN} — refusé avant exécution" in sortie
        assert f"répond    : {HOSTILE_SHOWN}" in sortie
        assert f"Réponse finale :\n  {HOSTILE_SHOWN}" in sortie
        assert not {"\x1b", "\x07", "\x9b"} & set(sortie)


def _marge(ligne: str) -> int:
    return len(ligne) - len(ligne.lstrip())


async def _cli(argv: list[str]) -> int:
    """La commande lance sa propre boucle : on la fait tourner hors de celle de l'essai."""
    return await asyncio.to_thread(cli_main, argv)

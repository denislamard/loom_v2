# SPDX-License-Identifier: Apache-2.0
"""Une trace, mise en texte pour un humain : ``loom inspect`` (K5, N4, 6.2c).

L'arbre du run se lit de haut en bas : le run, ses étapes, dans chaque étape
les appels de modèle (rôle, durée, tokens — l'entrée cache comprise, comme
l'en-tête —, coût, ce que le modèle a répondu) et les appels d'outil (durée,
arguments, résultat ; un appel refusé avant de partir le dit), sous un rôle
son modèle et son juge, sous un sous-agent son propre run ; puis la réponse
finale en entier, et un bilan compté sur l'arbre.

Arguments et résultats tiennent sur une ligne, coupés à ``WIDTH`` caractères
en le disant (``…``) ; ``full`` les montre en entier. La réponse finale n'est
jamais coupée. Sans contenu dans la trace (``content`` faux), seules les
métadonnées restent, et le texte le dit.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Final, cast

from pydantic import JsonValue

from loom_ia.telemetry.spans import AttributeValue
from loom_ia.telemetry.trace import Trace, TraceEvent, TraceSpan

# Largeur d'un extrait : au-delà, la ligne est coupée en le disant.
WIDTH: Final = 110
_INDENT: Final = "  "
# Événements d'un span qui ne disent rien de plus que le span lui-même.
_SILENT: Final = frozenset(
    {
        "run.started",
        "run.claimed",
        "run.released",
        "run.transitioned",
        "run.completed",
        "message.user",
        "step.started",
        "step.completed",
        "tool.called",
        "tool.completed",
        "model.responded",
        "model.exchanged",
        "judge.evaluated",
        "approval.requested",
    }
)


def render_trace(trace: Trace, *, full: bool = False) -> list[str]:
    """Les lignes de ``loom inspect`` pour cette trace."""
    lines = [
        f"Run        : {trace.run_id} (agent {trace.agent}, client {trace.tenant_id})",
        f"Session    : {trace.session_id}",
        f"Statut     : {_status(trace)}",
        f"Usage      : {trace.usage.prompt_tokens} → {trace.usage.output_tokens} tokens, "
        f"{trace.cost_usd:.6f} $, {_duration(trace.active_ms)} de pilotage",
    ]
    if not trace.content:
        lines.append("Contenu    : absent de cette trace (métadonnées seulement)")
    lines.append("")
    ids = {span.span_id for span in trace.spans}
    children: dict[str | None, list[TraceSpan]] = {}
    for span in trace.spans:
        parent = span.parent_span_id if span.parent_span_id in ids else None
        children.setdefault(parent, []).append(span)
    for root in children.get(None, []):
        lines += _tree(root, children, 0, full)
    lines.append("")
    if trace.output is not None:
        lines.append("Réponse finale :")
        lines += [f"{_INDENT}{line}" for line in trace.output.splitlines() or [""]]
    elif trace.content and trace.finished:
        lines.append("Réponse finale : aucune")
    lines.append(f"Bilan      : {_tally(trace.spans)}")
    return lines


# --- L'arbre -------------------------------------------------------------------


def _tree(
    span: TraceSpan, children: Mapping[str | None, list[TraceSpan]], depth: int, full: bool
) -> list[str]:
    pad = _INDENT * depth
    lines = [f"{pad}{_head(span)}"]
    # Ce qui s'est passé dans le span, marqué, au niveau de ses enfants.
    inner = f"{pad}{_INDENT}· "
    lines += [f"{inner}{line}".replace("\n", "\n" + " " * len(inner)) for line in _body(span, full)]
    for child in children.get(span.span_id, []):
        lines += _tree(child, children, depth + 1, full)
    return lines


def _head(span: TraceSpan) -> str:
    """La ligne d'un span : ce qu'il est, combien de temps, combien d'argent."""
    a = span.attributes
    said: str
    match span.kind:
        case "run":
            status = str(a.get("loom.run.status", "?"))
            said = (
                f"run {a.get('gen_ai.agent.name', '?')} — "
                f"{'inachevé' if status == 'unfinished' else status}, "
                f"{_duration(span.duration_ms)}"
            )
            if "loom.cost_usd" in a:
                said += f", {_cost(a['loom.cost_usd'])}"
        case "step":
            said = f"étape {a.get('loom.step', span.name.removeprefix('step '))}"
        case "chat":
            role = a.get("loom.role", "main")
            said = (
                f"modèle {a.get('gen_ai.request.model', '?')} ({role}) — "
                f"{_duration(span.duration_ms)}, {_tokens(a)}, "
                f"{_cost(a.get('loom.cost_usd', 0))}"
            )
            if a.get("loom.attempts", 1) not in (0, 1):
                said += f", {a['loom.attempts']} tentatives"
        case "tool":
            name = a.get("gen_ai.tool.name", "?")
            if not _executed(span):
                # Refusé avant de partir (arguments non conformes, outil
                # inconnu, approbation refusée…) : rien n'a tourné, la raison
                # est dans le résultat que le modèle a reçu.
                said = f"appel {name} — refusé avant exécution"
            else:
                kind = a.get("loom.tool.kind", "")
                label = {"role": "rôle", "agent": "sous-agent"}.get(str(kind), "outil")
                said = f"{label} {name} — {_duration(span.duration_ms)}"
                if a.get("loom.tool.is_error"):
                    said += " (erreur)"
            # L'erreur d'un appel est dite ci-dessus ; « [tool.completed] » n'ajouterait rien.
            if span.open:
                said += " (ouvert)"
            return said
        case "judge":
            said = f"juge {span.name.removeprefix('judge ')}"
        case "model":
            said = span.name.replace("role ", "appels du rôle ", 1)
        case _:
            said = _other(span)
    if span.open:
        said += " (ouvert)"
    if span.error:
        said += f" [{span.error}]"
    return said


def _body(span: TraceSpan, full: bool) -> list[str]:
    """Ce qui s'est passé dans un span, une ligne par fait."""
    lines: list[str] = []
    for event in span.events:
        lines += _said(span, event, full)
    return lines


def _said(span: TraceSpan, event: TraceEvent, full: bool) -> list[str]:
    data, content = event.data, event.content
    match event.name:
        case "model.responded":
            return _answer(content, full)
        case "tool.called":
            if content is not None and "arguments" in content:
                return [_line("arguments : ", _compact(content["arguments"]), full)]
            return []
        case "tool.completed":
            if content is not None and "output" in content:
                return [_line("résultat  : ", _output(content["output"]), full)]
            return []
        case "judge.evaluated":
            return [_verdict(data)]
        case "approval.requested" if span.kind != "other":
            return [f"approbation demandée pour {data.get('tool_name', '?')}"]
        case "approval.granted" | "approval.rejected" | "approval.expired":
            return [] if span.kind == "other" else [_approval(event)]
        case "guard.checked":
            outcome = data.get("outcome", "?")
            line = f"contrôle {data.get('guard', '?')} : {outcome}"
            if data.get("resolution"):
                line += f" → {data['resolution']}"
            return [line]
        case "policy.decided":
            return [
                f"politique {data.get('policy', '?')} ({data.get('point', '?')}) : "
                f"{data.get('decision', '?')}"
            ]
        case "model.retried":
            return [
                f"tentative {data.get('attempt', '?')} ratée ({data.get('error_kind', '?')}), "
                "nouvel essai"
            ]
        case "model.fell_back":
            return [f"secours : {data.get('from_model', '?')} → {data.get('to_model', '?')}"]
        case "artifact.stored":
            return [f"fichier rangé : {data.get('uri', '?')}"]
        case "run.failed":
            return [f"échec : {data.get('error_type', '?')}"]
        case name if name in _SILENT:
            return []
        case name:
            # Un fait que ce rendu ne connaît pas : on le nomme plutôt que de le taire.
            return [f"{name} ({event.status})"]


def _executed(span: TraceSpan) -> bool:
    """Un appel d'outil est parti s'il a son ``tool.called`` ; un appel refusé n'a que sa fin."""
    return any(event.name == "tool.called" for event in span.events)


def _other(span: TraceSpan) -> str:
    """Un span qui n'est ni run, ni étape, ni appel : une approbation, le plus souvent."""
    requested = next((e for e in span.events if e.name == "approval.requested"), None)
    if requested is None:
        return span.name
    tool = requested.data.get("tool_name", "?")
    decided = next(
        (e for e in span.events if e.name.startswith("approval.") and e is not requested), None
    )
    if decided is None:
        return f"approbation pour {tool} — en attente"
    return f"approbation pour {tool} — {_approval(decided).removeprefix('approbation ')}"


def _approval(event: TraceEvent) -> str:
    by = event.data.get("by")
    match event.name:
        case "approval.granted":
            return f"approbation accordée par {by}"
        case "approval.rejected":
            reason = event.data.get("reason")
            return f"approbation refusée par {by}" + (f" ({reason})" if reason else "")
        case _:
            return "approbation expirée"


# --- Contenus --------------------------------------------------------------------


def _answer(content: Mapping[str, JsonValue] | None, full: bool) -> list[str]:
    """Ce que le modèle a répondu : son texte, puis les outils qu'il appelle."""
    if content is None or not isinstance(content.get("message"), dict):
        return []
    message = cast("dict[str, JsonValue]", content["message"])
    blocks = message.get("blocks")
    if not isinstance(blocks, list):
        return []
    text = " ".join(
        str(block.get("text", ""))
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
    )
    calls = [
        f"{block.get('name', '?')}({_compact(block.get('arguments', {}))})"
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "tool_call"
    ]
    lines = [_line("répond    : ", text, full)] if text else []
    lines += [_line("appelle   : ", call, full) for call in calls]
    return lines


def _output(value: JsonValue) -> str:
    """Le texte d'un résultat d'outil : ses blocs de texte, sinon ses données."""
    if not isinstance(value, dict):
        return _compact(value)
    blocks = value.get("blocks")
    texts = (
        [
            str(block.get("text", ""))
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        if isinstance(blocks, list)
        else []
    )
    said = " ".join(t for t in texts if t) or _compact(value.get("data"))
    return f"(erreur) {said}" if value.get("is_error") else said


def _verdict(data: Mapping[str, JsonValue]) -> str:
    criteria = data.get("criteria")
    notes = (
        ", ".join(
            f"{item.get('name', '?')} {float(cast('float', item.get('score', 0))):.2f}"
            for item in criteria
            if isinstance(item, dict)
        )
        if isinstance(criteria, list)
        else ""
    )
    said = "accepté" if data.get("passed") else "refusé"
    return f"verdict {data.get('judge', '?')} : {notes} — {said} (essai {data.get('attempt', 1)})"


def _line(label: str, text: str, full: bool) -> str:
    """Une ligne d'extrait ; coupée en le disant, sauf en entier."""
    if full:
        return label + text.replace("\n", "\n" + " " * len(label))
    flat = " ".join(text.split())
    room = WIDTH - len(label)
    return label + (flat if len(flat) <= room else flat[: room - 1] + "…")


def _compact(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


# --- Chiffres -----------------------------------------------------------------------


def _status(trace: Trace) -> str:
    said = str(trace.status)
    if trace.error_type:
        said += f" ({trace.error_type})"
    said += f", {trace.iterations} itération(s)"
    if not trace.finished:
        said += " — run inachevé : durées provisoires"
    return said


def _tokens(a: Mapping[str, AttributeValue]) -> str:
    """Tokens d'un appel : l'entrée cache comprise, comme l'en-tête — la part du cache dite."""
    read = int(a.get("loom.usage.cache_read_tokens", 0))
    written = int(a.get("loom.usage.cache_write_tokens", 0))
    prompt = int(a.get("gen_ai.usage.input_tokens", 0)) + read + written
    said = f"{prompt} → {a.get('gen_ai.usage.output_tokens', 0)} tokens"
    cached = [f"{read} lus" if read else "", f"{written} écrits" if written else ""]
    if read or written:
        said += f" (dont {' et '.join(part for part in cached if part)} en cache)"
    return said


def _tally(spans: Sequence[TraceSpan]) -> str:
    """Ce que l'arbre contient, compté sur ses spans."""
    runs = sum(1 for s in spans if s.kind == "run")
    chats = sum(1 for s in spans if s.kind == "chat")
    tools = [s for s in spans if s.kind == "tool"]
    refused = sum(1 for s in tools if not _executed(s))
    approvals = sum(1 for s in spans if s.kind == "other" and s.name == "approval.requested")
    said = f"{chats} appel(s) de modèle, {len(tools)} appel(s) d'outil"
    if refused:
        said += f" dont {refused} refusé(s) avant exécution"
    if approvals:
        said += f", {approvals} approbation(s)"
    if runs > 1:
        said += f", {runs - 1} sous-run(s)"
    return said


def _duration(ms: float | int | str | bool) -> str:
    value = float(ms)
    return f"{value:.0f} ms" if value < 1000 else f"{value / 1000:.1f} s"


def _cost(usd: float | int | str | bool) -> str:
    return f"{float(usd):.6f} $"

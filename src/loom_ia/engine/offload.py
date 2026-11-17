# SPDX-License-Identifier: Apache-2.0
"""Gros résultats d'outils : déport dans le stockage d'artefacts (point 16, fonction D6).

Au-delà du seuil de l'outil (``offload_over``, en caractères de ce que le
modèle verrait), le contenu complet part dans le stockage d'artefacts et le
résultat n'en garde qu'un aperçu : le début d'un texte, ou la structure d'un
JSON (clés, premiers éléments, nombre total). ``tool.completed`` porte
l'aperçu et la référence (``offloaded``), jamais le contenu complet.

Le modèle lit la suite avec l'outil intégré ``artifact_read``, exposé
seulement après un premier déport dans le run, ou transmet le résultat entier
à un rôle par ``$ref`` sans le lire.

Sans stockage d'artefacts, le résultat est simplement tronqué.

Réglages : ``offload_over`` d'un outil, ou ``execution.tools.offload_over``
pour tous (50 000 caractères par défaut).
"""

import json
from collections.abc import AsyncGenerator
from typing import Final

from pydantic import JsonValue

from loom_ia.core.model import (
    ArtifactRefBlock,
    JsonBlock,
    TextBlock,
    ToolOutput,
    ToolSpec,
)
from loom_ia.core.ports import ArtifactNotFound, ToolContext, ToolError
from loom_ia.engine.delegated import DelegatedPayload, DelegatedTool, RunView

DEFAULT_OFFLOAD_OVER: Final = 50_000
"""Seuil de déport par défaut, en caractères (conception §17.6, ``execution.tools.offload_over``)."""
PREVIEW_CHARS: Final = 2_000
"""Taille maximale de l'aperçu, en caractères."""
PREVIEW_KEYS: Final = 20
"""Nombre de clés d'un objet JSON montrées dans l'aperçu."""
PREVIEW_ITEMS: Final = 3
"""Nombre d'éléments d'une liste JSON montrés dans l'aperçu."""

ARTIFACT_READ: Final = "artifact_read"
"""Nom de l'outil intégré qui lit un résultat déporté."""
READ_LIMIT: Final = 20_000
"""Nombre de caractères lus par ``artifact_read`` si ``limit`` n'est pas donné."""
READ_MAX: Final = 50_000
"""Valeur maximale de ``limit`` pour ``artifact_read``."""


def visible_text(output: ToolOutput) -> str:
    """Ce que le modèle verrait d'un résultat : ses textes et ses JSON, ou ``data``.

    C'est sur sa longueur que se mesure le seuil ``offload_over``.

    Args:
        output: Le résultat de l'outil.

    Returns:
        Les textes et les JSON des blocs, une ligne par bloc ; ``data`` en
        JSON si le résultat n'a pas de blocs.
    """
    parts: list[str] = []
    for block in output.blocks:
        match block:
            case TextBlock(text=text):
                parts.append(text)
            case JsonBlock(data=data):
                parts.append(_json(data))
            case _:
                pass
    if not output.blocks and output.data is not None:
        parts.append(_json(output.data))
    return "\n".join(parts)


def full_content(output: ToolOutput) -> tuple[str, str]:
    """Contenu complet à ranger, et son type : ``data`` en JSON s'il existe, sinon le texte.

    Args:
        output: Le résultat de l'outil.

    Returns:
        Le contenu et son type MIME (``application/json`` ou
        ``text/plain``).
    """
    if output.data is not None:
        return _json(output.data), "application/json"
    return visible_text(output), "text/plain"


def offloaded(output: ToolOutput, *, uri: str, content: str, refs: bool) -> ToolOutput:
    """Résultat réduit à un aperçu et à la référence de son contenu complet.

    Args:
        output: Le résultat d'origine.
        uri: Référence du contenu rangé (``artifact://…``).
        content: Le contenu complet, pour en dire la longueur.
        refs: Vrai si le run accepte les références ``$ref`` : la note dit
            alors comment transmettre le résultat entier.

    Returns:
        Un résultat dont le texte est l'aperçu suivi d'une note « [Résultat
        déporté : … outil artifact_read avec ref=…] », avec les fichiers, le
        statut d'erreur et le state d'origine, et ``offloaded`` à ``uri``.
    """
    value: JsonValue = output.data if output.data is not None else content
    kind = "JSON" if output.data is not None else "texte"
    hint = (
        f"[Résultat déporté : {len(content)} caractères ({kind}), seul l'aperçu ci-dessus est "
        f'montré. Pour lire la suite : outil {ARTIFACT_READ} avec ref="{uri}", offset et '
        "limit en caractères"
    )
    if refs:
        hint += " ; pour le transmettre en entier à un outil ou à un rôle : sa référence $ref"
    return ToolOutput(
        blocks=(TextBlock(text=f"{preview(value)}\n\n{hint}.]"), *_files(output)),
        is_error=output.is_error,
        artifacts=output.artifacts,
        offloaded=uri,
        # Lu avant le déport, le state ne vient plus des données : il reste tel quel.
        state=output.state,
        state_refused=output.state_refused,
    )


def truncated(output: ToolOutput, limit: int) -> ToolOutput:
    """Résultat tronqué au seuil, faute de stockage d'artefacts.

    Args:
        output: Le résultat d'origine.
        limit: Nombre de caractères gardés.

    Returns:
        Un résultat dont le texte est coupé à ``limit``, suivi d'une note
        « [Résultat tronqué : …] ».
    """
    text = visible_text(output)
    note = f"\n\n[Résultat tronqué : {limit} caractères montrés sur {len(text)}, aucun stockage d'artefacts pour conserver le reste.]"
    return ToolOutput(
        blocks=(TextBlock(text=text[:limit] + note), *_files(output)),
        is_error=output.is_error,
        artifacts=output.artifacts,
        state=output.state,
        state_refused=output.state_refused,
    )


def preview(value: JsonValue) -> str:
    """Début d'un texte, ou structure d'un JSON.

    Args:
        value: Le contenu.

    Returns:
        Un texte : ses 2 000 premiers caractères ; un objet : ses 20
        premières clés avec la nature de chaque valeur ; une liste : sa
        longueur et ses 3 premiers éléments. Toujours coupé à 2 000
        caractères.
    """
    match value:
        case str():
            return _cut(value, PREVIEW_CHARS)
        case dict():
            lines = [f"Objet JSON, {len(value)} clé(s) :"]
            lines += [f"- {key} : {_summary(item)}" for key, item in list(value.items())[:PREVIEW_KEYS]]
            if len(value) > PREVIEW_KEYS:
                lines.append(f"- … et {len(value) - PREVIEW_KEYS} autre(s) clé(s)")
            return _cut("\n".join(lines), PREVIEW_CHARS)
        case list():
            lines = [f"Liste JSON de {len(value)} élément(s) ; les premiers :"]
            lines += [f"- {_cut(_json(item), 300)}" for item in value[:PREVIEW_ITEMS]]
            return _cut("\n".join(lines), PREVIEW_CHARS)
        case _:
            return _cut(_json(value), PREVIEW_CHARS)


class ArtifactReadTool(DelegatedTool):
    """Outil intégré ``artifact_read`` : lit un résultat déporté par morceaux.

    Arguments du modèle : ``ref`` (référence ``artifact://…``), ``offset``
    (premier caractère, 0 par défaut), ``limit`` (nombre de caractères,
    20 000 par défaut, 50 000 au plus). Exposé seulement si le run a un
    magasin de fichiers et a déjà déporté un résultat.
    """

    def __init__(self) -> None:
        """Construit la description de l'outil."""
        self._spec = ToolSpec(
            name=ARTIFACT_READ,
            description=(
                "Lit une partie d'un résultat d'outil déporté parce qu'il était trop long. Donner la référence indiquée dans le résultat (artifact://…)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "ref": {"type": "string", "description": "Référence du résultat déporté"},
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                        "description": "Premier caractère lu",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": READ_MAX,
                        "default": READ_LIMIT,
                        "description": "Nombre de caractères lus",
                    },
                },
                "required": ["ref"],
                "additionalProperties": False,
            },
            kind="builtin",
            side_effects="none",
            idempotent=True,
        )

    @property
    def spec(self) -> ToolSpec:
        """Description de ``artifact_read`` montrée au modèle."""
        return self._spec

    def available(self, run: RunView) -> bool:
        """Vrai si le run a un magasin de fichiers et au moins un résultat déporté."""
        return run.artifacts is not None and run.state.offloaded

    async def check(self, arguments: dict[str, JsonValue], run: RunView) -> str | None:
        """Refuse une référence qui n'est pas un résultat déporté de ce run.

        Returns:
            « Référence inconnue : … » avec la liste des résultats déportés du
            run ; ``None`` si la référence est connue.
        """
        ref = arguments.get("ref")
        record = run.state.artifact(ref) if isinstance(ref, str) else None
        if record is not None and record.origin == "offload":
            return None
        known = [a.uri for a in run.state.artifacts if a.origin == "offload"]
        return f"Référence inconnue : {ref!r}. Résultats déportés de ce run : {', '.join(known)}."

    async def run(self, arguments: dict[str, JsonValue], context: ToolContext, run: RunView) -> AsyncGenerator[DelegatedPayload | ToolOutput]:
        """Lit le morceau demandé.

        Yields:
            Un seul résultat : « [Caractères <début> à <fin> sur <total> ;
            suite : offset=<fin>] » suivi du morceau, ou « fin du contenu ».

        Raises:
            ToolError: Pas de magasin de fichiers, ou référence introuvable.
        """
        ref = str(arguments["ref"])
        offset = _integer(arguments.get("offset"), 0)
        limit = _integer(arguments.get("limit"), READ_LIMIT)
        if run.artifacts is None:
            raise ToolError("Aucun stockage d'artefacts : rien à lire.")
        try:
            data = await run.artifacts.get(ref)
        except ArtifactNotFound as exc:
            raise ToolError(f"Résultat déporté introuvable : {ref}") from exc
        text = data.decode("utf-8")
        chunk = text[offset : offset + limit]
        end = offset + len(chunk)
        if not chunk:
            yield ToolOutput.text(f"[Rien à lire : offset {offset} au-delà de la fin ({len(text)}).]")
            return
        more = f"suite : offset={end}" if end < len(text) else "fin du contenu"
        yield ToolOutput.text(f"[Caractères {offset} à {end} sur {len(text)} ; {more}]\n{chunk}")


def _files(output: ToolOutput) -> tuple[ArtifactRefBlock, ...]:
    return tuple(block for block in output.blocks if isinstance(block, ArtifactRefBlock))


def _summary(value: JsonValue) -> str:
    match value:
        case dict():
            return f"objet à {len(value)} clé(s)"
        case list():
            return f"liste de {len(value)} élément(s)"
        case str() if len(value) > 80:
            return f"texte de {len(value)} caractères, {_json(value[:80])}…"
        case _:
            return _cut(_json(value), 80)


def _integer(value: JsonValue, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…"


def _json(value: JsonValue) -> str:
    return json.dumps(value, ensure_ascii=False)

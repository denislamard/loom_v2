# SPDX-License-Identifier: Apache-2.0
"""Les modèles de document d'un outil ``word`` ou ``pdf`` : leur texte et leur plan.

Un modèle est un fichier markdown, écrit comme un skill : une description
facultative en tête (``---`` / ``description: …`` / ``---``), puis des consignes
libres. Ses titres de premier niveau (``# Contexte et objectifs``) sont **les
sections que le document doit avoir**, dans cet ordre ; un titre qui finit par
« (facultatif) » peut manquer. Le texte sous un titre est la consigne de la
section : l'outil ne le lit pas, il le rend au modèle de langage.

À l'écriture, le document qui nomme un modèle est comparé à ce plan : ses titres
de niveau 1, dans l'ordre. La comparaison ignore la numérotation (« 1. »), la
casse, les accents et les espaces : « 1. Contexte et Objectifs » est la section
« Contexte et objectifs ».

Module léger, sans disque : le chargeur de config lit les fichiers et appelle
``parse`` ; une faute de modèle se voit dès ``loom validate``.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

MAX_TEMPLATES: Final = 20
MAX_TEMPLATE_CHARS: Final = 20_000
MAX_DESCRIPTION: Final = 200
NAME: Final = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
MAX_NAME: Final = 40
# Le nom qui choisit un document libre, quand l'outil a des modèles.
NONE: Final = "aucun"
OPTIONAL: Final = "(facultatif)"
_HEADING: Final = re.compile(r"^#(?!#)\s*(.*?)\s*#*\s*$")
_FENCE: Final = re.compile(r"^\s*(```|~~~)")
# « 1. », « 2.3 - », « 4) », « a) », « IV. » : la numérotation que le modèle de langage ajoute.
_NUMBERING: Final = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\s*[.)\-\u2013\u2014:]?|[a-z]\)|[ivx]{1,4}[.)])\s+"
)


@dataclass(frozen=True, slots=True)
class Section:
    """Une section du plan : son titre tel qu'écrit dans le modèle, et si elle peut manquer."""

    title: str
    optional: bool = False

    @property
    def key(self) -> str:
        return key(self.title)


@dataclass(frozen=True, slots=True)
class Template:
    """Un modèle lu : son nom, sa description, son texte, son plan."""

    name: str
    description: str | None
    text: str
    sections: tuple[Section, ...]

    def outline(self) -> str:
        """« Contexte et objectifs · Périmètre · Maquettes (facultatif) · … »."""
        return " · ".join(f"{s.title} {OPTIONAL}" if s.optional else s.title for s in self.sections)


def key(title: str) -> str:
    """Un titre comparable : sans numérotation, emphase, casse, accents ni espaces superflus."""
    said = re.sub(r"</?u>", "", title, flags=re.IGNORECASE)
    said = unicodedata.normalize("NFKD", said)
    said = "".join(c for c in said if not unicodedata.combining(c)).casefold()
    said = _NUMBERING.sub("", said, count=1)
    said = re.sub(r"[*_`]", "", said)
    return " ".join(said.split()).strip(" .:;")


def check_name(name: str) -> None:
    if not NAME.match(name) or len(name) > MAX_NAME or name == NONE:
        raise ValueError(
            f"nom de modèle {name!r} : minuscules, chiffres et tirets ({MAX_NAME} caractères "
            f"au plus), et pas « {NONE} », qui choisit un document libre"
        )


def parse(name: str, text: str) -> Template:
    """Le modèle et son plan ; ``ValueError`` qui dit quoi corriger."""
    check_name(name)
    if len(text) > MAX_TEMPLATE_CHARS:
        raise ValueError(f"modèle {name} : {len(text)} caractères, au-delà de {MAX_TEMPLATE_CHARS}")
    description, body = _front(name, text)
    if not body.strip():
        raise ValueError(f"modèle {name} : vide")
    sections: list[Section] = []
    fenced = False
    for line in body.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
            continue
        found = None if fenced else _HEADING.match(line)
        if found is None:
            continue
        title = found.group(1)
        optional = title.casefold().endswith(OPTIONAL)
        if optional:
            title = title[: -len(OPTIONAL)].rstrip()
        if not key(title):
            raise ValueError(f"modèle {name} : titre de section vide")
        sections.append(Section(title, optional))
    keys = [section.key for section in sections]
    doubles = [s.title for i, s in enumerate(sections) if s.key in keys[:i]]
    if doubles:
        raise ValueError(f"modèle {name} : section donnée deux fois : {', '.join(doubles)}")
    return Template(name=name, description=description, text=text, sections=tuple(sections))


def _front(name: str, text: str) -> tuple[str | None, str]:
    """La description de l'en-tête (``---`` … ``---``), et le reste du texte."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    try:
        end = next(i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration:
        raise ValueError(f"modèle {name} : en-tête ouvert par --- et jamais fermé") from None
    description: str | None = None
    for line in lines[1:end]:
        field, _, value = line.partition(":")
        if field.strip() == "description":
            description = value.strip().strip("\"'") or None
        elif line.strip():
            raise ValueError(
                f"modèle {name} : en-tête, seule « description » est lue ({line.strip()!r})"
            )
    if description is not None and len(description) > MAX_DESCRIPTION:
        raise ValueError(f"modèle {name} : description de plus de {MAX_DESCRIPTION} caractères")
    return description, "\n".join(lines[end + 1 :])


def plan_faults(template: Template, headings: Sequence[str]) -> list[str]:
    """Ce qui écarte les titres de niveau 1 d'un document du plan du modèle ; vide s'il le suit."""
    if not template.sections:
        return []
    position = {section.key: index for index, section in enumerate(template.sections)}
    faults: list[str] = []
    found: list[tuple[str, int]] = []
    seen: set[str] = set()
    for heading in headings:
        k = key(heading)
        if k not in position:
            faults.append(f"section hors du modèle « {heading} »")
        elif k in seen:
            faults.append(f"section donnée deux fois « {heading} »")
        else:
            seen.add(k)
            found.append((heading, position[k]))
    missing = [s.title for s in template.sections if not s.optional and s.key not in seen]
    if missing:
        faults.append(f"section(s) absente(s) : {', '.join(f'« {m} »' for m in missing)}")
    for (before, at), (after, then) in pairwise(found):
        if then < at:
            faults.append(f"« {before} » avant « {after} » : le modèle les met dans l'autre ordre")
            break
    return faults

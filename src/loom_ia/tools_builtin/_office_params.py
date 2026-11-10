# SPDX-License-Identifier: Apache-2.0
"""Paramètres communs des outils de documents (``loom.excel``, ``loom.word``, ``loom.pdf``).

Leurs dossiers suivent la forme de ``loom.local_files`` (``name``, ``path``,
``rights``, ``approval``), sans le droit ``delete`` : ces outils ne suppriment
rien. Leur fuseau est un nom IANA, vérifié au chargement.

Module léger — Pydantic, ``local_files_params``, ``_templates`` (la bibliothèque
standard) et ``_style_params`` seulement — : la config
l'importe pour valider une référence, sans importer aucun des outils.
"""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Final, Literal, Self, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, model_validator

from loom_ia.tools_builtin._style_params import Style, StyleField
from loom_ia.tools_builtin._templates import MAX_TEMPLATES, Template, check_name
from loom_ia.tools_builtin._templates import parse as parse_template
from loom_ia.tools_builtin.local_files_params import FolderSpec

DEFAULT_TIMEZONE: Final = "Europe/Paris"


def no_delete(reference: str) -> Callable[[object], object]:
    """Avant les règles communes : ``delete`` dans ``rights`` ou ``approval`` est refusé.

    Sinon, ``approval: { delete: … }`` sans ``delete`` accordé ferait conseiller
    de l'ajouter à ``rights``, où il est refusé. ``reference`` : « loom.excel »…
    """

    def check(value: object) -> object:
        if not isinstance(value, list | tuple):
            return value
        items = cast("Sequence[object]", value)
        deleting: list[str] = []
        for item in items:
            if not isinstance(item, Mapping):
                continue
            folder = cast(Mapping[str, object], item)
            rights, approval = folder.get("rights"), folder.get("approval")
            listed = isinstance(rights, list | tuple) and "delete" in cast(
                "Sequence[object]", rights
            )
            approved = isinstance(approval, Mapping) and "delete" in cast(
                Mapping[str, object], approval
            )
            if listed or approved:
                deleting.append(str(folder.get("name")))
        if deleting:
            raise _refused(reference, deleting)
        return items

    return check


def checked_folders(
    reference: str,
) -> Callable[[tuple[FolderSpec, ...]], tuple[FolderSpec, ...]]:
    """Après la validation des dossiers : au moins un, noms uniques, jamais ``delete``."""

    def check(folders: tuple[FolderSpec, ...]) -> tuple[FolderSpec, ...]:
        if not folders:
            raise ValueError("au moins un dossier est attendu")
        names = [folder.name for folder in folders]
        doubles = sorted({name for name in names if names.count(name) > 1})
        if doubles:
            raise ValueError(f"nom de dossier donné deux fois : {', '.join(doubles)}")
        deleting = [folder.name for folder in folders if "delete" in folder.rights]
        if deleting:
            raise _refused(reference, deleting)
        return folders

    return check


def _refused(reference: str, names: Sequence[str]) -> ValueError:
    return ValueError(
        f"dossier {', '.join(names)} : le droit 'delete' ne sert pas à {reference}, "
        "qui ne supprime rien (droits permis : read, write, overwrite)"
    )


def check_timezone(name: str) -> None:
    """``ValueError`` si ``name`` n'est pas un fuseau IANA connu."""
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:
        raise ValueError(
            f"fuseau inconnu : {name!r} — un nom IANA, par exemple Europe/Paris"
        ) from None


# --- Blocs facultatifs (loom.word, loom.pdf) -----------------------------------------

# Chacun coûte des tokens à chaque appel du modèle : il n'est dans le schéma et la
# description que si la config de l'outil le nomme dans ``blocks``.
type OptionalBlock = Literal["chart", "image", "signature", "toc", "checkboxes"]
OPTIONAL_BLOCKS: Final[tuple[OptionalBlock, ...]] = (
    "chart",
    "image",
    "signature",
    "toc",
    "checkboxes",
)
# Les clés d'avant ``blocks`` (2.0.x), refusées avec la forme qui les remplace.
OLD_KEYS: Final[Mapping[str, str]] = {
    "charts": "blocks: [chart]",
    "images": "blocks: [image]",
    "extra_blocks": "blocks: [signature, toc, checkboxes]",
}


def _unique_blocks(blocks: tuple[OptionalBlock, ...]) -> tuple[OptionalBlock, ...]:
    doubles = sorted({name for name in blocks if blocks.count(name) > 1})
    if doubles:
        raise ValueError(f"bloc nommé deux fois : {', '.join(doubles)}")
    return blocks


type Blocks = Annotated[tuple[OptionalBlock, ...], AfterValidator(_unique_blocks)]


def _keys(value: object) -> set[object]:
    """Les clés d'une table YAML ; aucune pour une autre valeur."""
    return set(cast(Mapping[object, object], value)) if isinstance(value, Mapping) else set()


def no_old_keys(value: object) -> object:
    """Avant la validation : ``charts``, ``images`` et ``extra_blocks`` (2.0.x) refusés,
    avec la forme qui les remplace, plutôt qu'un champ « extra » sans explication."""
    keys = _keys(value)
    found = [f"{key} → {OLD_KEYS[key]}" for key in OLD_KEYS if key in keys]
    if found:
        raise ValueError(
            f"{' ; '.join(found)} : les blocs facultatifs se nomment dans une seule liste, "
            "blocks (chart, image, signature, toc, checkboxes)"
        )
    return value


# --- Modèles de document (loom.word, loom.pdf) ---------------------------------------


class TemplateRef(BaseModel):
    """Un modèle de document : son fichier YAML (relatif à ``resources_dir``), puis son texte.

    Le chargeur de config lit le fichier et range son texte dans ``text``, comme un
    ``skill_file`` dans ``skill`` ; ``file`` n'est alors plus gardé. Le style que nomme le
    modèle (``style:`` dans son fichier) est lu en même temps et rangé dans ``style``. En
    config, le chemin seul : ``note: templates/note.yaml``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: Path | None = None
    text: str | None = None
    # Le style du modèle, lu par le chargeur de config (``loom.word``).
    style: StyleField = None

    @model_validator(mode="before")
    @classmethod
    def _style_in_the_template(cls, value: object) -> object:
        """La forme longue de la 2.0 (``{file, style}``) : le modèle nomme son style."""
        if isinstance(value, Mapping):
            entries = cast(Mapping[object, object], value)
            if "file" in entries and "style" in entries:
                raise ValueError(
                    "le modèle nomme son style dans son fichier depuis 2.1.0 (style: …, à "
                    "côté de sections) : templates.<nom> est le chemin du modèle seul"
                )
            return entries
        return value

    @model_validator(mode="after")
    def _one(self) -> Self:
        if (self.file is None) == (self.text is None):
            raise ValueError("un modèle se donne par son fichier (file) ou son texte (text)")
        return self


def _short_form(value: object) -> object:
    """``nom: fichier.yaml`` : la forme courte d'un modèle."""
    if not isinstance(value, Mapping):
        return value
    entries = cast(Mapping[object, object], value)
    return {
        name: {"file": given} if isinstance(given, str | Path) else given
        for name, given in entries.items()
    }


def _checked_templates(templates: dict[str, TemplateRef]) -> dict[str, TemplateRef]:
    if len(templates) > MAX_TEMPLATES:
        raise ValueError(f"{len(templates)} modèles, au-delà de {MAX_TEMPLATES}")
    for name in templates:
        check_name(name)
    return templates


def read_templates(templates: Mapping[str, TemplateRef], reference: str) -> dict[str, Template]:
    """Les modèles d'une référence, lus et analysés ; ``ValueError`` si l'un n'a pas été lu.

    Le chargeur de config range le texte de chaque fichier dans ``text`` ; une référence
    construite en Python sans passer par lui garde ``file`` : c'est une faute de montage.
    """
    found: dict[str, Template] = {}
    for name, ref in templates.items():
        if ref.text is None:
            raise ValueError(
                f"{reference} : modèle {name} ({ref.file}) non lu — les modèles se lisent au "
                "chargement de la config (load_config), ou se donnent par leur texte"
            )
        found[name] = parse_template(name, ref.text)
    return found


def check_section_styles(template: Template, style: Style, where: str) -> None:
    """Les styles que choisissent les sections (``styles``) : connus du style du modèle, et
    partis de l'élément qu'ils mettent en forme ; ``ValueError`` qui dit lequel."""
    for section in template.sections:
        for element, name in section.spec.styles.items():
            fault = style.element_fault(element, name)
            if fault is not None:
                raise ValueError(f"{where} : « {section.name} ».styles.{element} : {fault}")


def word_only(template: Template, reference: str) -> None:
    """Un modèle de ``loom.pdf`` : ni style, ni mise en page de section, pour l'instant."""
    said = template.layout
    if said:
        raise ValueError(
            f"{reference} : modèle {template.name} : {', '.join(said)} : le style et la mise "
            "en page des sections ne servent qu'à loom.word pour l'instant (loom.pdf écrit "
            "dans son style maison)"
        )


type Templates = Annotated[
    dict[str, TemplateRef], BeforeValidator(_short_form), AfterValidator(_checked_templates)
]

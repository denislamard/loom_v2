# SPDX-License-Identifier: Apache-2.0
"""Ce que ``word`` et ``pdf`` ont en commun une fois montés : dossiers, approbation, appels.

Les fichiers lus et écrits sont désignés comme dans ``loom.local_files`` —
``<dossier>/<chemin>`` — et soumis aux règles de ``_folders`` : dossiers
déclarés, droits, approbation par appel, liens qui sortent du dossier
refusés, écriture atomique, fichier existant remplacé seulement avec
``overwrite``. Chaque format ne fournit que son rendu, sa lecture, et ce que
sa description en dit.
"""

import asyncio
import hashlib
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from typing import ClassVar, Final
from zoneinfo import ZoneInfo

from pydantic import JsonValue

from loom_ia.core.model import SideEffects, ToolOutput, ToolSpec
from loom_ia.core.ports import CallAssessment, ToolContext, ToolError
from loom_ia.tools import to_output
from loom_ia.tools_builtin import Settings
from loom_ia.tools_builtin._document.arguments import (
    MAX_BLOCKS,
    MAX_CATEGORIES,
    MAX_IMAGE_BYTES,
    MAX_IMAGES,
    MAX_IMAGES_BYTES,
    MAX_ITEMS,
    MAX_OUTPUT_BYTES,
    MAX_SLICES,
    MAX_SOURCE_BYTES,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
    DocumentCall,
    Image,
    ReadArguments,
    TemplateArguments,
    WriteArguments,
    input_schema,
    parse,
    variant,
)
from loom_ia.tools_builtin._document.content import Pictures, Prepared, prepare
from loom_ia.tools_builtin._document.picture import Picture, PictureError
from loom_ia.tools_builtin._document.picture import parse as parse_picture
from loom_ia.tools_builtin._folders import Folders, Located, write_bytes
from loom_ia.tools_builtin._templates import NONE as NO_TEMPLATE
from loom_ia.tools_builtin._templates import Template
from loom_ia.tools_builtin.local_files_params import Right

# Le mode d'emploi de l'écriture, commun aux formats ; il ne part qu'à qui peut écrire.
WRITING_HELP: Final = (
    "Blocs ('blocks', dans l'ordre ; 'type' en tête de chacun) : titre (text, level 1 à 3), "
    "paragraphe (text), liste (items : textes, ou {text, items} pour une sous-liste ; "
    "ordered pour une liste numérotée), tableau (columns {header, type}, rows : une liste "
    f"de valeurs par ligne, totals, caption ; {MAX_TABLE_COLUMNS} colonnes et "
    f"{MAX_TABLE_ROWS} lignes au plus), note (text, title : un encadré), citation (text, "
    "source), saut_de_page. 'title' : le titre du document, en tête. Dans un texte : "
    "**gras**, *italique*, et \\n pour aller à la ligne dans le même paragraphe ; pas "
    "d'autre balisage (ni #, ni -, ni |).",
    "Types de colonne : texte, entier, decimal, montant_eur, montant_keur (valeur déjà en "
    "k€), pourcentage (fraction : 0.15 pour 15 %), date (AAAA-MM-JJ), date_heure (ISO "
    "8601), annee, booleen. L'outil écrit les valeurs à la française (1 250,50 €) : donner "
    "des nombres, sans unité ni dans la valeur ni dans l'en-tête. 'totals' : en-tête → "
    "somme, moyenne, nombre, min ou max, calculés par l'outil.",
    "Décrire ce que SONT les blocs, jamais leur apparence : police, tailles, couleurs, "
    "marges et numéros de page sont décidés par l'outil.",
)

# Le bloc graphique, seulement pour un outil monté avec ``charts``.
CHART_HELP: Final = (
    "Graphique ('type': 'graphique') : kind (barres, barres_horizontales, courbe, "
    "secteurs), title, caption ; ses données viennent du tableau qui le précède "
    "(from_table : {categories : en-tête des libellés, values : en-têtes des colonnes de "
    "nombres}), ce qui évite de recopier les nombres, ou sont données en ligne "
    "(categories, series [{name, values}], value_type). Des barres pour comparer, une "
    "courbe pour une évolution, des secteurs pour les parts d'un tout (une série, "
    f"{MAX_SLICES} parts au plus) ; {MAX_CATEGORIES} libellés au plus. Jamais de "
    "graphique dessiné en caractères.",
)

# Signature, sommaire et liste à cocher, seulement pour un outil monté avec ``extra_blocks``.
EXTRA_HELP: Final = (
    "Signature ('type': 'signature') : signers (« Le client », « L'entreprise » ; 4 au plus), "
    "mention (« Bon pour accord »). Sommaire ('type': 'sommaire') : la table des matières, "
    "tirée des titres. Liste à cocher : checkboxes, chaque élément commence par [x] ou [ ].",
)

# Le bloc image, seulement pour un outil monté avec ``images``.
IMAGE_HELP: Final = (
    "Image ('type': 'image') : path, un fichier PNG ou JPEG d'un dossier qui a le droit "
    "read ; width : pleine_largeur (par défaut), moitie, ou petite (un logo) ; caption, la "
    "légende dessous ; alt, ce que montre l'image pour qui ne la voit pas (la légende sinon). "
    "L'image est centrée, dans ses proportions ; l'outil la lit lui-même, ne jamais en "
    f"envoyer le contenu. {MAX_IMAGES} images au plus.",
)


class DocumentTool(ABC):
    """Un outil de document monté avec ses dossiers et son fuseau."""

    NAME: ClassVar[str]
    # Les arguments du format, sans graphique ni image : ``variant`` les élargit.
    CALL: ClassVar[type[DocumentCall]]
    # Le suffixe écrit (« .docx ») et le nom du fichier pour le modèle (« document Word »).
    SUFFIX: ClassVar[str]
    LABEL: ClassVar[str]
    MAX_SOURCE: ClassVar[int] = MAX_SOURCE_BYTES

    def __init__(
        self,
        space: Folders,
        zone: ZoneInfo,
        *,
        charts: bool = False,
        images: bool = False,
        templates: Mapping[str, Template] | None = None,
        extra: bool = False,
    ) -> None:
        self._space = space
        self._zone = zone
        self._charts = charts
        self._images = images
        self._extra = extra
        self._templates = dict(templates or {})
        folders = space.folders
        if self._templates and not any("write" in f.rights for f in folders):
            raise ValueError(
                "templates : des modèles de document, mais aucun dossier n'a le droit write"
            )
        # Le schéma vu par le modèle n'a les blocs graphique et image, et les modèles,
        # que s'ils servent.
        self._call = variant(
            self.CALL,
            charts=charts,
            images=images,
            templates=tuple(self._templates),
            extra=extra,
        )
        # Le pire de ses appels ; un outil qui ne fait que lire n'en a aucun, et se
        # rejoue donc en variante (une lecture sans effet s'y exécute).
        effects: SideEffects = (
            "irreversible"
            if any("overwrite" in f.rights for f in folders)
            else "reversible"
            if any("write" in f.rights for f in folders)
            else "none"
        )
        self._spec = ToolSpec(
            name=self.NAME,
            description=self._description(),
            input_schema=input_schema(self._call),
            kind="python",
            side_effects=effects,
            approval="always" if any(f.asking() for f in folders) else "never",
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def settings(self) -> Settings:
        """Les dossiers, puis les réglages du format, pour ``loom validate``."""
        return Settings(places=self._space.places(), options=self._options())

    def assess_call(self, arguments: Mapping[str, JsonValue]) -> CallAssessment:
        """Approbation et effets de cet appel : les droits qu'il exerce, dossier par dossier."""
        try:
            request = parse(self._call, arguments)
            if isinstance(request, TemplateArguments):
                # Un modèle vient de la config : rien n'est lu sur le disque.
                return CallAssessment(side_effects="none", idempotent=True)
            if isinstance(request, ReadArguments):
                return self._assess_read(self._source(request.path))
            reads: list[tuple[Located, Right]] = [
                (self._image_source(path), "read") for path in _image_paths(request)
            ]
            return self._assess_write(request, self._output(request), reads)
        except ToolError:
            # L'appel sera refusé à l'exécution, pour la même raison : aucun effet.
            return CallAssessment(side_effects="none")

    @staticmethod
    def _assess_read(source: Located) -> CallAssessment:
        """Une lecture : aucun effet ; l'approbation du droit read, si le dossier la demande."""
        if not Folders.asked(source, ("read",)):
            return CallAssessment(side_effects="none", idempotent=True)
        return CallAssessment(
            approval="always",
            side_effects="none",
            idempotent=True,
            reason=f"lire le fichier {source.address} — {Folders.rule([(source, 'read')])}",
        )

    @staticmethod
    def _assess_write(
        request: WriteArguments,
        located: Located,
        reads: Iterable[tuple[Located, Right]] = (),
    ) -> CallAssessment:
        """Les droits de la sortie, et le droit read de chaque image lue."""
        rights: tuple[Right, ...] = ("write", "overwrite") if request.overwrite else ("write",)
        asking: list[tuple[Located, Right]] = [
            (located, right) for right in Folders.asked(located, rights)
        ]
        images = list(reads)
        asking.extend(pair for pair in images if Folders.asked(pair[0], (pair[1],)))
        effects: SideEffects = "irreversible" if request.overwrite else "reversible"
        if not asking:
            return CallAssessment(side_effects=effects, idempotent=True)
        folders = sorted({source.folder.name for source, _ in images})
        return CallAssessment(
            approval="always",
            side_effects=effects,
            idempotent=True,
            reason=f"écrire le fichier {located.address}"
            + (", en remplaçant le fichier s'il existe" if request.overwrite else "")
            + (f", avec {len(images)} image(s) lue(s) dans {', '.join(folders)}" if images else "")
            + f" — {Folders.rule(asking)}",
        )

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        request = parse(self._call, arguments)
        if isinstance(request, TemplateArguments):
            return to_output(self._template(request.name))
        if isinstance(request, ReadArguments):
            source = self._source(request.path)
            data = await asyncio.to_thread(self._load, source)
            found = await self._read(request, source.address, data)
            return to_output({"path": source.address, **found})
        located = self._output(request)
        # Le rendu et le disque bloquent : hors de la boucle asyncio.
        result = await asyncio.to_thread(self._write, request, located)
        return to_output(result)

    # --- Chemins ------------------------------------------------------------------------

    def _output(self, request: WriteArguments) -> Located:
        if not request.path.lower().endswith(self.SUFFIX):
            raise ToolError(f"{request.path} : le {self.LABEL} s'écrit en {self.SUFFIX}.")
        located = self._space.locate(request.path)
        Folders.require(located, ("write", "overwrite") if request.overwrite else ("write",))
        return located

    def _image_source(self, path: str) -> Located:
        """Le fichier d'une image : un dossier qui a le droit read, sans rien lire."""
        located = self._space.locate(path)
        Folders.require(located, ("read",))
        return located

    def _source(self, path: str) -> Located:
        self._check_readable(path)
        located = self._space.locate(path)
        Folders.require(located, ("read",))
        return located

    # --- Exécution ----------------------------------------------------------------------

    def _write(self, request: WriteArguments, located: Located) -> dict[str, JsonValue]:
        followed = (
            self._templates[request.template]
            if request.template is not None and request.template != NO_TEMPLATE
            else None
        )
        prepared = prepare(request, self._zone, self._pictures(request), followed)
        data, facts, warnings = self._render(prepared)
        if len(data) > MAX_OUTPUT_BYTES:
            raise ToolError(f"Fichier de {len(data)} octets, au-delà de {MAX_OUTPUT_BYTES}.")
        try:
            outcome = write_bytes(self._space.resolved(located), located, data, request.overwrite)
        except OSError as exc:
            # Le message du système, sans le chemin réel qu'il citerait.
            raise ToolError(f"{located.address} : {exc.strerror or type(exc).__name__}.") from exc
        return {
            **outcome,
            "sha256": hashlib.sha256(data).hexdigest(),
            **facts,
            "blocks": len(request.blocks),
            **({"images": len(list(_image_blocks(request)))} if self._images else {}),
            **({"template": request.template} if self._templates else {}),
            "warnings": list[JsonValue]([*prepared.warnings, *warnings]),
        }

    def _template(self, name: str) -> dict[str, JsonValue]:
        """Un modèle déclaré : sa description, son plan, et ses consignes, telles qu'écrites."""
        template = self._templates[name]
        return {
            "template": name,
            "description": template.description,
            "plan": [
                f"{section.title} (facultatif)" if section.optional else section.title
                for section in template.sections
            ],
            "text": template.text,
        }

    def _pictures(self, request: WriteArguments) -> Pictures:
        """Chaque image du document, lue et reconnue, ou ce qui l'en empêche.

        Les fautes ne sont pas levées : ``prepare`` les dit avec celles du document,
        au bloc où l'image est demandée. La lecture s'arrête quand les images lues
        dépassent la borne du document : les suivantes ne sont pas lues.
        """
        found: dict[str, Picture | str] = {}
        total = 0
        for path in _image_paths(request):
            if total > MAX_IMAGES_BYTES:
                found[path] = f"{path} non lue : les images précédentes dépassent déjà la borne"
                continue
            try:
                data = self._load(self._image_source(path), MAX_IMAGE_BYTES)
                picture = parse_picture(data)
            except ToolError as exc:
                found[path] = str(exc).rstrip(".")
                continue
            except PictureError as exc:
                found[path] = f"{path} : {exc}"
                continue
            total += len(data)
            found[path] = picture
        return found

    def _load(self, located: Located, limit: int | None = None) -> bytes:
        bound = self.MAX_SOURCE if limit is None else limit
        target = self._space.resolved(located)
        if not target.is_file():
            raise ToolError(f"{located.address} : fichier introuvable.")
        try:
            with target.open("rb") as handle:
                # Un fichier qui grossit entre-temps reste borné.
                data = handle.read(bound + 1)
        except OSError as exc:
            raise ToolError(f"{located.address} : {exc.strerror or type(exc).__name__}.") from exc
        if len(data) > bound:
            raise ToolError(f"{located.address} dépasse {bound} octets.")
        return data

    # --- Ce que chaque format fournit -----------------------------------------------------

    @abstractmethod
    def _render(self, prepared: Prepared) -> tuple[bytes, dict[str, JsonValue], list[str]]:
        """Le fichier, ce que le résultat en dit (pages…), et les avertissements du rendu."""

    @abstractmethod
    async def _read(self, request: ReadArguments, where: str, data: bytes) -> dict[str, JsonValue]:
        """Ce qu'un fichier contient, en JSON ; ``ToolError`` s'il est illisible."""

    @abstractmethod
    def _check_readable(self, path: str) -> None:
        """``ToolError`` si le suffixe n'est pas un format que l'outil lit."""

    @abstractmethod
    def _reading_help(self, folders: str) -> str:
        """La ligne de la description qui dit la lecture."""

    def _writing_notes(self) -> tuple[str, ...]:
        """Ce que le format ajoute au mode d'emploi de l'écriture."""
        return ()

    def _options(self) -> tuple[tuple[str, str], ...]:
        return (
            ("fuseau", self._zone.key),
            ("graphiques", "oui" if self._charts else "non"),
            ("images", "oui" if self._images else "non"),
            ("modèles", ", ".join(self._templates) or "aucun"),
        )

    def _templates_help(self) -> list[str]:
        """Les modèles déclarés : leur nom et leur description, jamais leur texte."""
        if not self._templates:
            return []
        return [
            "Modèles de document : à l'écriture, 'template' nomme le modèle suivi, ou aucun "
            "pour un document libre ; les titres de niveau 1 du document suivent alors le "
            "plan du modèle, dans l'ordre. Avant d'écrire, lire le modèle : action=template, "
            "template=<nom> (ses consignes et son plan).",
            *(
                f"- {name}" + (f" : {template.description}" if template.description else "")
                for name, template in self._templates.items()
            ),
        ]

    def _description(self) -> str:
        space = self._space
        writes = [folder.name for folder in space.folders if "write" in folder.rights]
        reads = [folder.name for folder in space.folders if "read" in folder.rights]
        lines = [
            (
                f"Écrit un {self.LABEL} ({self.SUFFIX}) mis en forme ; avec action=read, lit "
                f"un fichier existant."
                if writes
                else f"Lit un {self.LABEL} existant (action=read)."
            ),
            "Dossiers et droits (un fichier se désigne par « <dossier>/<chemin> ») :",
            *space.described(),
        ]
        if writes:
            lines += [
                f"Le fichier s'écrit dans : {', '.join(writes)} ; un fichier existant n'est "
                "remplacé qu'avec overwrite=true (droit overwrite).",
                *WRITING_HELP,
                *(CHART_HELP if self._charts else ()),
                *(IMAGE_HELP if self._images else ()),
                *(EXTRA_HELP if self._extra else ()),
                *self._templates_help(),
                *self._writing_notes(),
                f"Bornes : {MAX_BLOCKS} blocs, {MAX_ITEMS} éléments par liste. Résultat : le "
                "chemin écrit et les avertissements ; jamais le contenu.",
            ]
        else:
            lines.append("Aucun dossier n'a le droit write : l'outil ne fait que lire.")
        if reads:
            lines.append(self._reading_help(", ".join(reads)))
        else:
            lines.append("Lecture (action=read) : aucun dossier n'a le droit read.")
        return "\n".join(lines)


def _image_blocks(request: WriteArguments) -> Iterable[Image]:
    return (block for block in request.blocks if isinstance(block, Image))


def _image_paths(request: WriteArguments) -> list[str]:
    """Les chemins des images, chacun une fois, dans l'ordre du document."""
    return list(dict.fromkeys(block.path for block in _image_blocks(request)))

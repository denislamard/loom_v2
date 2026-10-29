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
from collections.abc import Mapping
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
    MAX_ITEMS,
    MAX_OUTPUT_BYTES,
    MAX_SLICES,
    MAX_SOURCE_BYTES,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
    DocumentCall,
    ReadArguments,
    WriteArguments,
    input_schema,
    parse,
)
from loom_ia.tools_builtin._document.content import Prepared, prepare
from loom_ia.tools_builtin._folders import Folders, Located, write_bytes

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


class DocumentTool(ABC):
    """Un outil de document monté avec ses dossiers et son fuseau."""

    NAME: ClassVar[str]
    # Les arguments sans le bloc graphique, puis avec (outil monté avec ``charts``).
    CALL: ClassVar[type[DocumentCall]]
    CHART_CALL: ClassVar[type[DocumentCall]]
    # Le suffixe écrit (« .docx ») et le nom du fichier pour le modèle (« document Word »).
    SUFFIX: ClassVar[str]
    LABEL: ClassVar[str]
    MAX_SOURCE: ClassVar[int] = MAX_SOURCE_BYTES

    def __init__(self, space: Folders, zone: ZoneInfo, *, charts: bool = False) -> None:
        self._space = space
        self._zone = zone
        self._charts = charts
        # Le schéma vu par le modèle n'a le bloc graphique que s'il sert.
        self._call = self.CHART_CALL if charts else self.CALL
        folders = space.folders
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
            if isinstance(request, ReadArguments):
                return self._assess_read(self._source(request.path))
            return self._assess_write(request, self._output(request))
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
            reason=f"lire {source.address} : approbation demandée pour read",
        )

    @staticmethod
    def _assess_write(request: WriteArguments, located: Located) -> CallAssessment:
        rights = ("write", "overwrite") if request.overwrite else ("write",)
        asking = Folders.asked(located, rights)
        effects: SideEffects = "irreversible" if request.overwrite else "reversible"
        if not asking:
            return CallAssessment(side_effects=effects, idempotent=True)
        return CallAssessment(
            approval="always",
            side_effects=effects,
            idempotent=True,
            reason=f"écrire {located.address} : approbation demandée pour "
            + ", ".join(f"{right} sur {located.address}" for right in asking),
        )

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        request = parse(self._call, arguments)
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

    def _source(self, path: str) -> Located:
        self._check_readable(path)
        located = self._space.locate(path)
        Folders.require(located, ("read",))
        return located

    # --- Exécution ----------------------------------------------------------------------

    def _write(self, request: WriteArguments, located: Located) -> dict[str, JsonValue]:
        prepared = prepare(request, self._zone)
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
            "warnings": list[JsonValue]([*prepared.warnings, *warnings]),
        }

    def _load(self, located: Located) -> bytes:
        target = self._space.resolved(located)
        if not target.is_file():
            raise ToolError(f"{located.address} : fichier introuvable.")
        try:
            with target.open("rb") as handle:
                # Un fichier qui grossit entre-temps reste borné.
                data = handle.read(self.MAX_SOURCE + 1)
        except OSError as exc:
            raise ToolError(f"{located.address} : {exc.strerror or type(exc).__name__}.") from exc
        if len(data) > self.MAX_SOURCE:
            raise ToolError(f"{located.address} dépasse {self.MAX_SOURCE} octets.")
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
        return (("fuseau", self._zone.key), ("graphiques", "oui" if self._charts else "non"))

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

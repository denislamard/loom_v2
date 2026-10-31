# SPDX-License-Identifier: Apache-2.0
"""L'outil ``excel`` monté : ses dossiers, son fuseau, son recalcul ; et sa fabrique."""

import asyncio
import hashlib
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Final, cast
from zoneinfo import ZoneInfo

from openpyxl.utils import get_column_letter
from pydantic import BaseModel, JsonValue

from loom_ia.core.model import SideEffects, ToolOutput, ToolSpec
from loom_ia.core.ports import CallAssessment, ToolContext, ToolError
from loom_ia.tools import to_output
from loom_ia.tools_builtin import BuiltinContext, Settings
from loom_ia.tools_builtin._document.arguments import MAX_CATEGORIES, MAX_SLICES
from loom_ia.tools_builtin._folders import Folders, Located, mount, write_bytes
from loom_ia.tools_builtin.excel.arguments import (
    MAX_COLUMNS,
    MAX_INLINE_ROWS,
    MAX_OUTPUT_BYTES,
    MAX_READ_ROWS,
    MAX_SOURCE_BYTES,
    NAME,
    READ_ROWS,
    DataSource,
    ExcelArguments,
    ExcelCall,
    ExcelChartCall,
    ReadArguments,
    SheetSpec,
    input_schema,
    parse,
)
from loom_ia.tools_builtin.excel.charts import MAX_POINTS
from loom_ia.tools_builtin.excel.recalc import Recalc, recalculate
from loom_ia.tools_builtin.excel.sources import read_rows
from loom_ia.tools_builtin.excel.workbook import build_workbook, prepare, to_bytes
from loom_ia.tools_builtin.excel_params import ExcelParams
from loom_ia.tools_builtin.local_files_params import Right


class ExcelTool:
    """L'outil ``excel`` monté avec ses dossiers, son fuseau et, s'il y a lieu, son recalcul."""

    def __init__(
        self,
        space: Folders,
        zone: ZoneInfo,
        recalc: Recalc | None,
        unsafe: str | None = None,
        *,
        charts: bool = False,
    ) -> None:
        self._space = space
        self._charts = charts
        # Le schéma vu par le modèle n'a les graphiques que s'ils servent.
        self._call: type[ExcelCall] = ExcelChartCall if charts else ExcelCall
        self._zone = zone
        self._recalc = recalc
        # Pourquoi les classeurs ne se lisent pas ici (versions d'expat ou de lxml).
        self._unsafe = unsafe
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
            name=NAME,
            description=_description(space, zone, recalc is not None, unsafe, charts=charts),
            input_schema=input_schema(self._call),
            kind="python",
            # Le pire des cas, pour ce qui ne regarde pas l'appel lui-même ;
            # chaque appel est évalué à part (``assess_call``).
            side_effects=effects,
            approval="always" if any(f.asking() for f in folders) else "never",
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def settings(self) -> Settings:
        """Les dossiers, puis le fuseau, le recalcul et la lecture, pour ``loom validate``."""
        recalc = "non"
        if self._recalc:
            recalc = f"{self._recalc.soffice} (au plus {self._recalc.timeout:g} s)"
        reading = (
            f"non ({self._unsafe})"
            if self._unsafe
            else "oui"
            if any("read" in folder.rights for folder in self._space.folders)
            else "aucun dossier read"
        )
        return Settings(
            places=self._space.places(),
            options=(
                ("fuseau", self._zone.key),
                ("graphiques", "oui" if self._charts else "non"),
                ("recalcul", recalc),
                ("lecture des classeurs", reading),
            ),
        )

    def assess_call(self, arguments: Mapping[str, JsonValue]) -> CallAssessment:
        """Approbation et effets de cet appel : les droits qu'il exerce, dossier par dossier."""
        try:
            request = parse(arguments, self._call)
            if isinstance(request, ReadArguments):
                return self._assess_read(request)
            located = self._output(request)
            sources = [self._source(data.path) for _, data in _with_data(request)]
        except ToolError:
            # L'appel sera refusé à l'exécution, pour la même raison : aucun effet.
            return CallAssessment(side_effects="none")
        needed: list[tuple[Located, Right]] = [(located, "write")]
        if request.overwrite:
            needed.append((located, "overwrite"))
        needed.extend((source, "read") for source in sources)
        asking = [
            f"{right} sur {where.address}"
            for where, right in needed
            if Folders.asked(where, (right,))
        ]
        effects: SideEffects = "irreversible" if request.overwrite else "reversible"
        if not asking:
            return CallAssessment(side_effects=effects, idempotent=True)
        return CallAssessment(
            approval="always",
            side_effects=effects,
            idempotent=True,
            reason=f"écrire le classeur {located.address} : approbation demandée pour "
            + ", ".join(dict.fromkeys(asking)),
        )

    def _assess_read(self, request: ReadArguments) -> CallAssessment:
        """Une lecture : aucun effet ; l'approbation du droit read, si le dossier la demande."""
        source = self._workbook(request.path)
        if not Folders.asked(source, ("read",)):
            return CallAssessment(side_effects="none", idempotent=True)
        return CallAssessment(
            approval="always",
            side_effects="none",
            idempotent=True,
            reason=f"lire le classeur {source.address} : approbation demandée pour read",
        )

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        request = parse(arguments, self._call)
        if isinstance(request, ReadArguments):
            source = self._workbook(request.path)
            found = await asyncio.to_thread(self._perform_read, request, source)
            return to_output(found)
        located = self._output(request)
        sources = {data.path: self._source(data.path) for _, data in _with_data(request)}
        # openpyxl, le disque et LibreOffice bloquent : hors de la boucle asyncio.
        result = await asyncio.to_thread(self._perform, request, located, sources)
        return to_output(result)

    # --- Chemins ------------------------------------------------------------------------

    def _output(self, request: ExcelArguments) -> Located:
        if not request.path.lower().endswith(".xlsx"):
            raise ToolError(f"{request.path} : le classeur s'écrit en .xlsx.")
        located = self._space.locate(request.path)
        Folders.require(located, ("write", "overwrite") if request.overwrite else ("write",))
        return located

    def _workbook(self, path: str) -> Located:
        """Le classeur à lire : .xlsx ou .xlsm, dans un dossier où ``read`` est accordé."""
        from loom_ia.tools_builtin.excel.reader import check_suffix

        check_suffix(path)
        self._reading_allowed(path)
        return self._source(path)

    def _reading_allowed(self, path: str) -> None:
        if self._unsafe:
            raise ToolError(f"{path} : lecture des classeurs désactivée ici — {self._unsafe}.")

    def _source(self, path: str) -> Located:
        if path.lower().endswith((".xlsx", ".xlsm")):
            self._reading_allowed(path)
        located = self._space.locate(path)
        Folders.require(located, ("read",))
        return located

    # --- Exécution ----------------------------------------------------------------------

    def _perform(
        self, request: ExcelArguments, located: Located, sources: Mapping[str, Located]
    ) -> dict[str, JsonValue]:
        try:
            loaded = {
                data.path: read_rows(
                    self._read(sources[data.path]),
                    Path(data.path).suffix.lower(),
                    sheet,
                    sources[data.path].address,
                    data.sheet,
                )
                for sheet, data in _with_data(request)
            }
            prepared = prepare(request, loaded, self._zone)
            workbook, written, warnings = build_workbook(prepared)
            data = to_bytes(workbook)
            if len(data) > MAX_OUTPUT_BYTES:
                raise ToolError(f"Classeur de {len(data)} octets, au-delà de {MAX_OUTPUT_BYTES}.")
            formulas = [cell for sheet in written for cell in sheet.formulas]
            report: JsonValue = None
            if self._recalc is not None and formulas:
                report = recalculate(data, formulas, self._recalc.soffice, self._recalc.timeout)
            outcome = write_bytes(self._space.resolved(located), located, data, request.overwrite)
        except OSError as exc:
            # Le message du système, sans le chemin réel qu'il citerait.
            raise ToolError(f"{located.address} : {exc.strerror or type(exc).__name__}.") from exc
        summary: dict[str, JsonValue] = {
            **outcome,
            "sha256": hashlib.sha256(data).hexdigest(),
            "sheets": [
                {
                    "name": sheet.name,
                    "rows": sheet.rows,
                    "formulas": len(sheet.formulas),
                    # Seulement s'il y en a : le résumé d'un classeur sans graphique ne change pas.
                    **({"charts": sheet.charts} if sheet.charts else {}),
                }
                for sheet in written
            ],
            "warnings": list[JsonValue](warnings),
        }
        if report is not None:
            summary["recalc"] = report
        return summary

    def _perform_read(self, request: ReadArguments, source: Located) -> dict[str, JsonValue]:
        from loom_ia.tools_builtin.excel.reader import READ_CHARS, overview, read_range

        data = self._read(source)
        if request.sheet is None:
            return {"path": source.address, **overview(data, source.address)}
        found, state = read_range(
            data, source.address, request.sheet, request.range, request.max_rows
        )
        column = get_column_letter(found.first_column)
        truncated = found.next_row is not None
        result: dict[str, JsonValue] = {
            "path": source.address,
            "sheet": request.sheet,
            "start": f"{column}{found.first_row}",
            "rows": cast(JsonValue, found.rows),
            "row_count": len(found.rows),
            "truncated": truncated,
        }
        if truncated:
            result["next_start"] = f"{column}{found.next_row}"
        if state != "visible":
            result["state"] = state
        if found.formulas:
            result["formulas"] = cast(JsonValue, found.formulas)
        notes: list[JsonValue] = []
        if found.without_value:
            notes.append(
                f"{found.without_value} formule(s) sans valeur calculée : le classeur n'a pas été "
                "recalculé depuis son écriture (Excel le fera à l'ouverture)."
            )
        if truncated:
            why = (
                f"la taille du résultat ({READ_CHARS} caractères)"
                if found.budget_hit
                else f"{request.max_rows} lignes"
            )
            notes.append(
                f"Lecture coupée par {why} : la suite commence en {column}{found.next_row} "
                f"(range, max_rows jusqu'à {MAX_READ_ROWS})."
            )
        if found.columns_cut_after:
            notes.append(
                f"Colonnes coupées après {found.columns_cut_after} ({MAX_COLUMNS} au plus) : "
                "demander les suivantes avec range."
            )
        if found.cut_cells:
            notes.append(f"{found.cut_cells} texte(s) coupé(s) à 1 000 caractères.")
        if notes:
            result["notes"] = notes
        return result

    def _read(self, located: Located) -> bytes:
        target = self._space.resolved(located)
        if not target.is_file():
            raise ToolError(f"{located.address} : fichier introuvable.")
        with target.open("rb") as handle:
            # Un fichier qui grossit entre-temps reste borné.
            data = handle.read(MAX_SOURCE_BYTES + 1)
        if len(data) > MAX_SOURCE_BYTES:
            raise ToolError(f"{located.address} dépasse {MAX_SOURCE_BYTES} octets.")
        return data


def _with_data(request: ExcelArguments) -> Iterable[tuple[SheetSpec, DataSource]]:
    return ((sheet, sheet.data) for sheet in request.sheets if sheet.data is not None)


def _description(
    space: Folders, zone: ZoneInfo, recalc: bool, unsafe: str | None, *, charts: bool = False
) -> str:
    writes = [folder.name for folder in space.folders if "write" in folder.rights]
    reads = [folder.name for folder in space.folders if "read" in folder.rights]
    lines = [
        (
            "Écrit un classeur Excel (.xlsx) mis en forme ; avec action=read, lit un classeur "
            "existant. Décrire ce que SONT les données, jamais leur apparence : police, "
            "couleurs, formats, largeurs, volets gelés et filtre sont décidés par l'outil."
            if writes
            else "Lit un classeur Excel existant (action=read)."
        ),
        "Dossiers et droits (un fichier se désigne par « <dossier>/<chemin> ») :",
        *space.described(),
    ]
    if writes:
        # Le mode d'emploi de l'écriture ne part qu'à qui peut écrire : environ
        # 1 500 caractères envoyés à chaque appel de modèle.
        lines += [
            f"Le classeur s'écrit dans : {', '.join(writes)} ; un classeur existant n'est "
            "remplacé qu'avec overwrite=true (droit overwrite).",
            "Onglet : 'name' (31 caractères, sans [ ] : * ? / \\), 'columns' ({header, type}), "
            f"puis 'rows' pour un petit tableau (au plus {MAX_INLINE_ROWS} lignes) ou 'data' "
            "pour un fichier .csv, .json ou .xlsx (droit read ; 'sheet' pour l'onglet d'un "
            ".xlsx) ; 'totals' ajoute une ligne de total (en-tête → somme, moyenne, nombre, min "
            "ou max). La ligne 1 porte les en-têtes : les données commencent ligne 2.",
            "Types de colonne : texte, entier, decimal, montant_eur, montant_keur (valeur déjà "
            "en k€), pourcentage (fraction : 0.15 pour 15 %), date (AAAA-MM-JJ), date_heure "
            f"(ISO 8601 ; un fuseau donné passe en heure de {zone.key}), annee (« 2026 »), "
            "booleen. Montants et pourcentages affichent leur unité (€, k€, %) : ne pas la "
            "répéter dans l'en-tête.",
            "Rôles d'une ligne ('role') ou d'une cellule ({value, role, note}) : saisie (à "
            "remplir par l'utilisateur), hypothese (paramètre cité par les formules ; 'note' "
            "obligatoire, avec sa source), total, note (explication). Une valeur « =… » est une "
            "formule ; dans une ligne saisie ou hypothese, un texte non vide est un libellé.",
            "Formules : en anglais (SUM, IF, INDEX, MATCH…), arguments séparés par « , », "
            "onglet cité entre apostrophes s'il a une espace ou un accent ('Mes "
            "Hypothèses'!B2). Mettre chaque hypothèse dans sa cellule et la citer ($B$2), "
            "plutôt qu'un nombre dans la formule. Pas de XLOOKUP, FILTER, SORT, UNIQUE : "
            "préparer les données avant.",
            *(CHART_HELP if charts else ()),
            "Résultat : le chemin écrit, les onglets et leurs nombres de lignes et de formules, "
            "les avertissements ; jamais le contenu.",
        ]
    else:
        lines.append("Aucun dossier n'a le droit write : l'outil ne fait que lire (action=read).")
    if unsafe:
        lines.append(f"Lecture (action=read) : indisponible ici ({unsafe}).")
    elif reads:
        lines.append(
            f"Lecture (action=read, dans : {', '.join(reads)}) : un .xlsx ou .xlsm. Sans 'sheet', "
            "la liste des onglets et leurs premières lignes ; avec 'sheet', les cellules depuis "
            f"A1 ou depuis 'range', {READ_ROWS} lignes au plus par défaut ('max_rows', jusqu'à "
            f"{MAX_READ_ROWS}). Valeurs en JSON (dates en ISO) ; 'formulas' donne la formule "
            "d'une cellule par son adresse ; 'start' est l'adresse de la 1re cellule rendue."
        )
    else:
        lines.append("Lecture (action=read) : aucun dossier n'a le droit read.")
    if recalc:
        lines.append(
            "Les formules sont recalculées avant l'écriture : une formule en erreur (#DIV/0!, "
            "#NAME?…) fait échouer l'appel, avec les cellules en cause."
        )
    return "\n".join(lines)


CHART_HELP: Final = (
    "Graphiques d'un onglet ('charts', liste) : {kind (barres, barres_horizontales, courbe, "
    "secteurs), title, categories : en-tête de la colonne des libellés, values : en-têtes des "
    "colonnes de nombres, une série chacune}. Le graphique est lié aux cellules de l'onglet "
    "(lignes de données depuis la ligne 2, sans total ni note) et se place à droite des "
    "données : ne pas recopier les nombres ailleurs. Des barres pour comparer, une courbe "
    f"pour une évolution ({MAX_POINTS} points au plus), des secteurs pour les parts d'un tout "
    f"(une série, {MAX_SLICES} parts au plus) ; {MAX_CATEGORIES} libellés au plus sinon. "
    "Pour un graphique sur des données d'un autre onglet, les y mettre.",
)


def build(params: BaseModel, context: BuiltinContext) -> ExcelTool:
    """La fabrique de ``loom.excel`` : les dossiers comme ``local_files``, puis LibreOffice."""
    if not isinstance(params, ExcelParams):
        raise TypeError(f"paramètres de {NAME} attendus, reçu {type(params).__name__}")
    space = Folders(mount(params.folders, context))
    recalc: Recalc | None = None
    if params.recalc:
        given = params.soffice or Path("soffice")
        if not given.is_absolute() and context.base_dir is not None and given.parent != Path():
            given = context.base_dir / given
        found = shutil.which(given)
        if found is None:
            raise ValueError(
                f"recalc: true, mais LibreOffice est introuvable ({given}) — l'installer, "
                "donner son chemin dans 'soffice', ou retirer recalc"
            )
        recalc = Recalc(soffice=Path(found), timeout=params.recalc_timeout)
    unsafe: str | None = None
    if any("read" in folder.rights for folder in space.folders):
        from loom_ia.tools_builtin.excel.reader import xml_safety

        unsafe = xml_safety()
    return ExcelTool(space, ZoneInfo(params.timezone), recalc, unsafe, charts=params.charts)

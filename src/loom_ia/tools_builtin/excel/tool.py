# SPDX-License-Identifier: Apache-2.0
"""L'outil ``excel`` monté : ses dossiers, son fuseau, son recalcul ; et sa fabrique."""

import asyncio
import hashlib
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, JsonValue

from loom_ia.core.model import SideEffects, ToolOutput, ToolSpec
from loom_ia.core.ports import CallAssessment, ToolContext, ToolError
from loom_ia.tools import to_output
from loom_ia.tools_builtin import BuiltinContext
from loom_ia.tools_builtin._folders import Folders, Located, mount, write_bytes
from loom_ia.tools_builtin.excel.arguments import (
    MAX_INLINE_ROWS,
    MAX_OUTPUT_BYTES,
    MAX_SOURCE_BYTES,
    NAME,
    DataSource,
    ExcelArguments,
    SheetSpec,
    input_schema,
    parse,
)
from loom_ia.tools_builtin.excel.recalc import Recalc, recalculate
from loom_ia.tools_builtin.excel.sources import read_rows
from loom_ia.tools_builtin.excel.workbook import build_workbook, prepare, to_bytes
from loom_ia.tools_builtin.excel_params import ExcelParams
from loom_ia.tools_builtin.local_files_params import Right


class ExcelTool:
    """L'outil ``excel`` monté avec ses dossiers, son fuseau et, s'il y a lieu, son recalcul."""

    def __init__(self, space: Folders, zone: ZoneInfo, recalc: Recalc | None) -> None:
        self._space = space
        self._zone = zone
        self._recalc = recalc
        folders = space.folders
        effects: SideEffects = (
            "irreversible" if any("overwrite" in f.rights for f in folders) else "reversible"
        )
        self._spec = ToolSpec(
            name=NAME,
            description=_description(space, zone, recalc is not None),
            input_schema=input_schema(),
            kind="python",
            # Le pire des cas, pour ce qui ne regarde pas l'appel lui-même ;
            # chaque appel est évalué à part (``assess_call``).
            side_effects=effects,
            approval="always" if any(f.asking() for f in folders) else "never",
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def settings_lines(self) -> tuple[str, ...]:
        """Les dossiers, le fuseau, puis le recalcul, pour ``loom validate``."""
        recalc = (
            f"recalcul : {self._recalc.soffice} (au plus {self._recalc.timeout:g} s)"
            if self._recalc
            else "recalcul : non"
        )
        return (*self._space.settings_lines(), f"fuseau : {self._zone.key}", recalc)

    def assess_call(self, arguments: Mapping[str, JsonValue]) -> CallAssessment:
        """Approbation et effets de cet appel : les droits qu'il exerce, dossier par dossier."""
        try:
            request = parse(arguments)
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

    async def invoke(self, arguments: dict[str, JsonValue], context: ToolContext) -> ToolOutput:
        request = parse(arguments)
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

    def _source(self, path: str) -> Located:
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
                {"name": sheet.name, "rows": sheet.rows, "formulas": len(sheet.formulas)}
                for sheet in written
            ],
            "warnings": list[JsonValue](warnings),
        }
        if report is not None:
            summary["recalc"] = report
        return summary

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


def _description(space: Folders, zone: ZoneInfo, recalc: bool) -> str:
    writes = [folder.name for folder in space.folders if "write" in folder.rights]
    lines = [
        "Écrit un classeur Excel (.xlsx) mis en forme. Décrire ce que SONT les données, jamais "
        "leur apparence : police, couleurs, formats, largeurs, volets gelés et filtre sont "
        "décidés par l'outil.",
        "Dossiers et droits (un fichier se désigne par « <dossier>/<chemin> ») :",
        *space.described(),
        f"Le classeur s'écrit dans : {', '.join(writes)} ; un classeur existant n'est remplacé "
        "qu'avec overwrite=true (droit overwrite).",
        "Onglet : 'name' (31 caractères, sans [ ] : * ? / \\), 'columns' ({header, type}), "
        f"puis 'rows' pour un petit tableau (au plus {MAX_INLINE_ROWS} lignes) ou 'data' pour "
        "un fichier .csv ou .json (droit read) ; 'totals' ajoute une ligne de total "
        "(en-tête → somme, moyenne, nombre, min ou max). La ligne 1 porte les en-têtes : les "
        "données commencent ligne 2.",
        "Types de colonne : texte, entier, decimal, montant_eur, montant_keur (valeur déjà en "
        "k€), pourcentage (fraction : 0.15 pour 15 %), date (AAAA-MM-JJ), date_heure (ISO "
        f"8601 ; un fuseau donné passe en heure de {zone.key}), annee (« 2026 »), booleen. "
        "Montants et pourcentages affichent leur unité (€, k€, %) : ne pas la répéter dans "
        "l'en-tête.",
        "Rôles d'une ligne ('role') ou d'une cellule ({value, role, note}) : saisie (à remplir "
        "par l'utilisateur), hypothese (paramètre cité par les formules ; 'note' obligatoire, "
        "avec sa source), total, note (explication). Une valeur « =… » est une formule ; dans "
        "une ligne saisie ou hypothese, un texte non vide est un libellé.",
        "Formules : en anglais (SUM, IF, INDEX, MATCH…), arguments séparés par « , », onglet "
        "cité entre apostrophes s'il a une espace ou un accent ('Mes Hypothèses'!B2). Mettre "
        "chaque hypothèse dans sa cellule et la citer ($B$2), plutôt qu'un nombre dans la "
        "formule. Pas de XLOOKUP, FILTER, SORT, UNIQUE : préparer les données avant.",
        "Résultat : le chemin écrit, les onglets et leurs nombres de lignes et de formules, "
        "les avertissements ; jamais le contenu.",
    ]
    if recalc:
        lines.append(
            "Les formules sont recalculées avant l'écriture : une formule en erreur (#DIV/0!, "
            "#NAME?…) fait échouer l'appel, avec les cellules en cause."
        )
    return "\n".join(lines)


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
    return ExcelTool(space, ZoneInfo(params.timezone), recalc)

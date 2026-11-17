# SPDX-License-Identifier: Apache-2.0
"""Extraction d'une valeur du state de session depuis le résultat d'un outil (state v2).

Le modèle désigne un résultat par l'outil ``loom.state`` ; la règle
d'extraction, elle, est déclarée sur la clé du state dans la config de l'agent
(``extract:``). Le modèle ne transmet aucune donnée : il ne peut ni mal
recopier ni inventer. Voir ``docs/state_v2_analyse.md``.

Trois formes de résultat sont lues, comme des lignes :

- un tableau ``{"columns": [...], "rows": [[...], ...]}`` (``loom.sql``) ;
- une liste d'objets ;
- un objet seul (une ligne).

Trois règles :

- ``value`` : la colonne d'une ligne, telle quelle ;
- ``fields`` : un objet, chaque champ pris dans une colonne d'une ligne ;
- ``columns`` : un objet, chaque champ la liste d'une colonne sur toutes les
  lignes. Refusé sur un résultat tronqué (``"truncated": true``), qui
  donnerait une liste incomplète sans le dire.

Une colonne présente plusieurs fois (``SELECT id, code, *``) est lue quand
toutes ses occurrences ont la même valeur ; sinon (``SELECT d.id, c.id``), refus.

Une ligne se choisit par ``match`` — des colonnes et leurs valeurs, que doit
porter exactement une ligne du résultat (``{"code": "C-261"}``) — ou par ``row``
(à partir de 0) ; sans l'un ni l'autre, le résultat doit avoir exactement une
ligne. ``match`` est le plus sûr : un modèle compte mal les lignes, et une
valeur qu'il lit ne peut désigner qu'une ligne qui la porte vraiment.
``label`` ajoute un champ, rempli par le texte que donne le modèle (un
libellé, jamais une donnée du résultat).

Chaque refus lève ``ExtractError``, dont le message s'adresse au modèle et dit
quoi corriger (ajouter une colonne à ``match``, refaire la requête sans alias…).

Module pur : aucune entrée-sortie, aucune dépendance au reste de loom.

Contenu : ``Extraction`` (la règle d'une clé), ``ModelKey`` (une clé tenue par
le modèle), ``extract`` (appliquer une règle à un résultat) et
``ExtractError``.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from pydantic import JsonValue


class ExtractError(ValueError):
    """Extraction impossible ; le message, destiné au modèle, dit quoi corriger.

    ``loom.state`` la change en ``ToolError`` (« <clé> non posé : <motif> ») :
    le modèle lit le motif comme résultat en erreur, et rien n'est posé dans le
    state.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class Extraction:
    """Règle d'extraction d'une clé du state, déclarée sous ``extract:``.

    Exactement une de ``value``, ``fields`` et ``columns`` est donnée ; la
    config le vérifie au chargement.

    Attributes:
        value: Nom de la colonne dont la valeur, prise dans une ligne, devient
            la valeur de la clé, telle quelle.
        fields: Champ de l'objet produit → colonne où le prendre, dans une
            ligne. Vide par défaut.
        columns: Champ de l'objet produit → colonne dont il reçoit la liste des
            valeurs, sur toutes les lignes. Vide par défaut.
        label: Champ qui reçoit le libellé donné par le modèle (un texte libre,
            jamais une donnée du résultat) ; seulement avec ``fields`` ou
            ``columns``. ``None`` : la clé ne prend pas de libellé.
    """

    value: str | None = None
    fields: Mapping[str, str] = field(default_factory=dict[str, str])
    columns: Mapping[str, str] = field(default_factory=dict[str, str])
    label: str | None = None

    @property
    def single_row(self) -> bool:
        """Vrai si la règle lit une seule ligne (``value``, ``fields``)."""
        return not self.columns


@dataclass(frozen=True, slots=True)
class _Table:
    """Un résultat d'outil lu comme des lignes, quelle que soit sa forme.

    Attributes:
        names: Noms des colonnes, dans l'ordre (doublons compris pour un
            tableau ``columns``/``rows``).
        rows: Les lignes : des listes de valeurs pour un tableau, des objets
            sinon.
        tabular: Vrai pour un tableau ``{"columns": …, "rows": …}``.
        truncated: Vrai si le tableau porte ``"truncated": true``.
    """

    names: tuple[str, ...]
    rows: tuple[Mapping[str, JsonValue] | Sequence[JsonValue], ...]
    tabular: bool
    truncated: bool

    def cell(self, row: int, column: str) -> JsonValue:
        """Valeur d'une colonne dans une ligne.

        Args:
            row: Indice de la ligne, à partir de 0.
            column: Nom de la colonne.

        Returns:
            La valeur de la cellule. Une colonne présente plusieurs fois est
            lue si toutes ses occurrences ont la même valeur.

        Raises:
            ExtractError: La colonne est absente du résultat ou de la ligne, ou
                présente plusieurs fois avec des valeurs différentes (le
                message conseille un alias dans la requête).
        """
        record = self.rows[row]
        if self.tabular:
            assert isinstance(record, Sequence) and not isinstance(record, str)
            indexes = self.indexes(column)
            if indexes[-1] >= len(record):
                raise ExtractError(f"ligne {row} : pas de valeur pour la colonne {column!r}")
            values = [record[i] for i in indexes]
            if any(value != values[0] for value in values[1:]):
                raise ExtractError(
                    f"colonne {column!r} présente {len(indexes)} fois, avec des valeurs différentes (ligne {row}) : donner un alias dans la requête"
                )
            return values[0]
        assert isinstance(record, Mapping)
        if column not in record:
            raise ExtractError(
                f"ligne {row} : colonne {column!r} absente (colonnes : {_listed(record)}) : "
                f"refaire la requête avec la colonne {column!r}, sous ce nom (sans alias)"
            )
        return record[column]

    def indexes(self, column: str) -> list[int]:
        """Positions d'une colonne dans les noms du tableau.

        Une colonne en double (``SELECT id, *``) a plusieurs positions ; ``cell``
        la lit si toutes ses occurrences ont la même valeur, et la refuse sinon.

        Args:
            column: Nom de la colonne.

        Returns:
            Ses positions, dans l'ordre, au moins une.

        Raises:
            ExtractError: La colonne est absente ; le message liste les
                colonnes présentes (20 au plus).
        """
        found = [i for i, name in enumerate(self.names) if name == column]
        if not found:
            raise ExtractError(
                f"colonne {column!r} absente du résultat (colonnes : {_listed(self.names)}) : "
                f"refaire la requête avec la colonne {column!r}, sous ce nom (sans alias)"
            )
        return found


@dataclass(frozen=True, slots=True, kw_only=True)
class ModelKey:
    """Une clé du state que le modèle tient à jour (``writers: [model]``), vue par ``loom.state``.

    Attributes:
        name: Nom de la clé dans le state.
        description: Ce que la clé désigne, repris dans la description de
            l'outil ``loom.state``.
        schema: JSON Schema de la valeur, contrôlé à chaque écriture.
        extraction: La règle qui tire la valeur du résultat désigné.
        required: Vrai si un run ne s'ouvre pas tant que la clé n'a pas de
            valeur (décision D13 du state de session). Faux par défaut.
        clears: Clés que poser celle-ci efface, dans la même écriture
            (``clears:``). Aucune par défaut.
    """

    name: str
    description: str
    schema: Mapping[str, JsonValue]
    extraction: Extraction
    required: bool = False
    clears: tuple[str, ...] = ()


def extract(
    data: JsonValue,
    rule: Extraction,
    *,
    row: int | None = None,
    match: Mapping[str, JsonValue] | None = None,
    label: str | None = None,
) -> JsonValue:
    """Applique une règle d'extraction au résultat d'un outil.

    Avec une règle d'une ligne (``value``, ``fields``), la ligne se choisit par
    ``match``, sinon par ``row``, sinon le résultat doit avoir exactement une
    ligne. Avec ``columns``, toutes les lignes sont lues, et le résultat ne
    doit être ni tronqué ni vide.

    Args:
        data: Données du résultat : un tableau ``{"columns", "rows",
            "truncated"?}``, une liste d'objets ou un objet seul.
        rule: La règle déclarée sur la clé.
        row: Indice de la ligne, à partir de 0 ; exclusif de ``match``.
        match: Colonnes et valeurs que doit porter exactement une ligne. Un
            nombre écrit en texte vaut le nombre (``"42"`` trouve ``42``), un
            booléen ne vaut que lui-même.
        label: Libellé donné par le modèle, rangé dans le champ
            ``rule.label``.

    Returns:
        La valeur à poser : la cellule pour ``value``, un objet pour
        ``fields`` et ``columns``.

    Raises:
        ExtractError: Libellé donné à une clé qui n'en prend pas ; ``row`` et
            ``match`` donnés ensemble ; ``row`` ou ``match`` sur une règle
            ``columns`` ; résultat tronqué, vide, illisible ou sans données
            structurées ; ligne introuvable, ambiguë ou hors du résultat ;
            colonne absente ou en double avec des valeurs différentes.
    """
    table = _table(data)
    if rule.label is None and label is not None:
        raise ExtractError("cette clé ne prend pas de libellé : retirer 'label'")
    if row is not None and match:
        raise ExtractError("'row' ou 'match', l'un des deux")
    if rule.single_row:
        index = _matched(table, match) if match else _row(table, row)
        if rule.value is not None:
            return table.cell(index, rule.value)
        result: dict[str, JsonValue] = {name: table.cell(index, column) for name, column in rule.fields.items()}
    else:
        if row is not None or match:
            raise ExtractError("cette clé prend toutes les lignes du résultat : retirer 'row' et 'match'")
        if table.truncated:
            raise ExtractError("résultat tronqué (truncated) : la liste serait incomplète ; refaire la requête pour qu'elle rende toutes les lignes")
        if not table.rows:
            raise ExtractError("le résultat n'a aucune ligne : liste vide, rien à poser (pour effacer la clé : clear)")
        if table.tabular:
            for column in rule.columns.values():
                table.indexes(column)
        result = {name: [table.cell(i, column) for i in range(len(table.rows))] for name, column in rule.columns.items()}
    if rule.label is not None and label is not None:
        result[rule.label] = label
    return result


def _table(data: JsonValue) -> _Table:
    """Lit un résultat comme des lignes ; ``ExtractError`` s'il n'a pas de données structurées.

    Un tableau ``columns``/``rows`` (``loom.sql``), une liste d'objets (les
    colonnes sont l'union de leurs clés, dans l'ordre d'apparition) ou un objet
    seul (une ligne) sont acceptés ; du texte ou une image ne le sont pas.
    """
    if isinstance(data, dict) and "columns" in data and "rows" in data:
        names, rows = data["columns"], data["rows"]
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise ExtractError("résultat illisible : 'columns' n'est pas une liste de noms")
        if not isinstance(rows, list):
            raise ExtractError("résultat illisible : 'rows' n'est pas une liste de lignes")
        lines = tuple(row for row in rows if isinstance(row, list))
        if len(lines) != len(rows):
            raise ExtractError("résultat illisible : 'rows' n'est pas une liste de lignes")
        return _Table(
            tuple(str(n) for n in names),
            lines,
            tabular=True,
            truncated=data.get("truncated") is True,
        )
    if isinstance(data, list):
        if not all(isinstance(item, dict) for item in data):
            raise ExtractError("résultat illisible : une liste dont les éléments ne sont pas des objets")
        records = tuple(item for item in data if isinstance(item, dict))
        names = tuple(dict.fromkeys(key for record in records for key in record))
        return _Table(names, records, tabular=False, truncated=False)
    if isinstance(data, dict):
        return _Table(tuple(data), (data,), tabular=False, truncated=False)
    raise ExtractError("ce résultat n'a pas de données structurées (texte, image) : rien à extraire")


def _row(table: _Table, row: int | None) -> int:
    """Indice de la ligne à lire : ``row``, ou la seule ligne du résultat.

    Raises:
        ExtractError: ``row`` hors du résultat ; ou, sans ``row``, un résultat
            vide ou de plusieurs lignes (le message propose ``match`` ou
            ``row``).
    """
    count = len(table.rows)
    if row is None:
        if count == 1:
            return 0
        if count == 0:
            raise ExtractError("le résultat n'a aucune ligne : rien à poser")
        raise ExtractError(
            f"le résultat a {count} lignes : désigner la bonne avec 'match' (une colonne et sa valeur, par exemple 'code') ou 'row' (0 à {count - 1})"
        )
    if not 0 <= row < count:
        raise ExtractError(f"ligne {row} hors du résultat ({count} ligne(s) : 0 à {count - 1})")
    return row


def _matched(table: _Table, match: Mapping[str, JsonValue]) -> int:
    """Indice de la seule ligne qui porte ces valeurs ; un nombre écrit en texte vaut le nombre.

    Raises:
        ExtractError: Aucune ligne, ou plusieurs lignes, portent ces valeurs ;
            ou une colonne de ``match`` est absente.
    """
    if table.tabular:
        for column in match:
            table.indexes(column)
    found = [i for i in range(len(table.rows)) if all(_same(table.cell(i, column), wanted) for column, wanted in match.items())]
    said = ", ".join(f"{column} = {wanted!r}" for column, wanted in match.items())
    if not found:
        raise ExtractError(f"aucune ligne du résultat n'a {said}")
    if len(found) > 1:
        raise ExtractError(f"{len(found)} lignes du résultat ont {said} : ajouter une colonne à 'match'")
    return found[0]


def _same(value: JsonValue, wanted: JsonValue) -> bool:
    """Égalité pour ``match`` : un booléen ne vaut que lui-même, ``"42"`` vaut ``42``, espaces de bord ignorés."""
    if value == wanted and type(value) is type(wanted):
        return True
    scalar = (str, int, float)
    if isinstance(value, bool) or isinstance(wanted, bool):
        return value is wanted
    if isinstance(value, scalar) and isinstance(wanted, scalar):
        return str(value).strip() == str(wanted).strip() or value == wanted
    return value == wanted


def _listed(names: Mapping[str, object] | Sequence[str]) -> str:
    shown = list(names)
    return ", ".join(shown[:20]) + (", …" if len(shown) > 20 else "") if shown else "aucune"

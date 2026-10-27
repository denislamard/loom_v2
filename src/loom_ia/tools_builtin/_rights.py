# SPDX-License-Identifier: Apache-2.0
"""Droits accordés et approbation de chacun : la normalisation commune des outils fournis.

Les dossiers de ``loom.local_files`` et ``loom.excel``, comme les tables de
``loom.sql``, se déclarent avec deux clés, une par notion :

    rights: [read, write, overwrite, delete]        # accordés ; le reste est refusé
    approval: { overwrite: never, delete: never }   # facultatif : qui attend un humain

``resolve`` les ramène à la forme que lit l'outil, ``dict[droit, "never" |
"always"]`` : un droit présent est accordé, et ``always`` le met sous
approbation. Un droit absent d'``approval`` garde son approbation par défaut.

L'ancienne écriture, une table droit → approbation dans ``rights``, est
refusée : ``rights: { delete: never }`` se lisait « suppression interdite »
et l'accordait sans rien demander. Le message donne l'écriture équivalente.

Module léger, sans dépendance : la config l'importe, par les modules de
paramètres, sans importer les outils.
"""

from collections.abc import Mapping, Sequence
from typing import Final, Literal, cast

type RightApproval = Literal["never", "always"]
# Comment dire l'écriture équivalente : clés d'un dossier, ou valeur d'une table SQL.
type Form = Literal["folder", "table"]

APPROVALS: Final[tuple[RightApproval, ...]] = ("never", "always")


def granted_line(right: str, approval: RightApproval, *, destructive: bool) -> str:
    """« write : accordé, avec approbation », pour ``loom validate``.

    Un droit destructif qui passe sans approbation le dit : le choix est
    légitime, mais il doit se voir.
    """
    said = "avec approbation" if approval == "always" else "sans approbation"
    flag = " (destructif)" if destructive and approval == "never" else ""
    return f"{right} : accordé, {said}{flag}"


def resolve[R: str](
    rights: object,
    approval: object,
    *,
    known: Sequence[R],
    defaults: Mapping[R, RightApproval],
    where: str,
    form: Form,
) -> dict[str, object]:
    """La table interne droit → approbation ; ``ValueError`` dit ce qui ne va pas.

    ``known`` donne les droits de l'outil dans l'ordre où on les dit : la table
    rendue le suit, quel que soit l'ordre de la config. Un droit inconnu y
    reste, à la fin : le modèle de l'outil le refuse ensuite, en le nommant.
    ``where`` nomme le dossier ou la table dans les messages.
    """
    names: tuple[str, ...] = tuple(known)
    default_of: dict[str, RightApproval] = {str(right): value for right, value in defaults.items()}
    if isinstance(rights, Mapping):
        table = cast(Mapping[object, object], rights)
        if not table:
            # Le contrôle du modèle dit « vide » à sa façon, comme pour une liste vide.
            return {}
        raise ValueError(_legacy(table, known=names, defaults=default_of, where=where, form=form))
    if not isinstance(rights, list | tuple):
        raise ValueError(
            f"{where} : 'rights' attend une liste de droits ({', '.join(names)}), pas {rights!r}"
        )
    given = [str(item) for item in cast("Sequence[object]", rights)]
    doubles = sorted({right for right in given if given.count(right) > 1}, key=given.index)
    if doubles:
        raise ValueError(f"{where} : droit donné deux fois : {', '.join(doubles)}")
    chosen = _approval(approval, given=given, known=names, where=where)
    ordered = [right for right in names if right in given]
    ordered += [right for right in given if right not in names]
    return {right: chosen.get(right, default_of.get(right, "never")) for right in ordered}


def _approval(
    approval: object, *, given: Sequence[str], known: Sequence[str], where: str
) -> dict[str, RightApproval]:
    if approval is None:
        return {}
    if not isinstance(approval, Mapping):
        raise ValueError(
            f"{where} : 'approval' attend une table droit → never ou always, pas {approval!r}"
        )
    chosen: dict[str, RightApproval] = {}
    for key, value in cast(Mapping[object, object], approval).items():
        right = str(key)
        if right not in known:
            raise ValueError(
                f"{where} : approval {right!r} — droit inconnu (droits : {', '.join(known)})"
            )
        if value not in APPROVALS:
            raise ValueError(
                f"{where} : approval {right!r} vaut {value!r} — attendu : never (sans "
                "approbation) ou always (approbation humaine)"
            )
        if right not in given:
            raise ValueError(
                f"{where} : approval {right!r} porte sur un droit non accordé — l'ajouter "
                "à 'rights', ou le retirer d'approval"
            )
        chosen[right] = value
    return chosen


def _legacy(
    table: Mapping[object, object],
    *,
    known: Sequence[str],
    defaults: Mapping[str, RightApproval],
    where: str,
    form: Form,
) -> str:
    """Le refus de l'ancienne table, avec l'écriture équivalente, prête à recopier."""
    said = {str(right): value for right, value in table.items()}
    rights = [right for right in known if right in said]
    rights += [right for right in said if right not in known]
    # Seuls les écarts aux défauts : le reste s'obtient sans rien écrire.
    departures = {
        right: said[right] for right in rights if said[right] != defaults.get(right, "never")
    }
    listed = f"[{', '.join(rights)}]"
    approval = "{ " + ", ".join(f"{right}: {value}" for right, value in departures.items()) + " }"
    if form == "folder":
        written = f"rights: {listed}" + (f" et approval: {approval}" if departures else "")
        subject = "'rights' en table droit → approbation n'est plus accepté"
    else:
        written = f"{{ rights: {listed}, approval: {approval} }}" if departures else listed
        subject = "la table droit → approbation n'est plus acceptée"
    return (
        f"{where} : {subject} (never voulait dire « sans approbation », pas « refusé ») "
        f"— écrire {written}"
    )

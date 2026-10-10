"""Resolution des limites de ressources d'un job.

REGLE : L'HOTE PROPOSE, LA VM PLAFONNE

Un job ne doit pas pouvoir s'octroyer trente minutes de CPU parce que
l'appelant l'a demande. Une valeur superieure au plafond est ramenee au
plafond SANS erreur, et la valeur effectivement appliquee est reportee dans
la reponse (`limits_applied`) : le client voit ce qui s'est reellement passe
plutot que de supposer que sa demande a ete honoree.

Le seul cas qui provoque une erreur est la demande d'une memoire INFERIEURE
au plancher : la ramener silencieusement au plancher masquerait une erreur de
configuration, alors que la laisser passer donnerait un echec illisible (voir
MEM_FLOOR ci-dessous).
"""

from __future__ import annotations

from typing import Any

# Valeurs par defaut d'un job qui ne demande rien.
DEFAULTS: dict[str, int] = {
    "wall_ms": 30_000,
    "cpu_ms": 30_000,
    "mem_bytes": 512 * 1024 * 1024,
    "fsize_bytes": 256 * 1024 * 1024,
    "nofile": 256,
    "nproc": 64,
    "out_files": 128,
}

# Plafonds imposes par la VM.
CEILINGS: dict[str, int] = {
    "wall_ms": 300_000,
    "cpu_ms": 300_000,
    "mem_bytes": 768 * 1024 * 1024,
    "fsize_bytes": 1024 * 1024 * 1024,
    "nofile": 1024,
    "nproc": 256,
    "out_files": 512,
}

# En dessous d'environ 128 MiB, CPython echoue AVANT d'atteindre le code du
# tool : l'echec se produit pendant l'import et le message ne designe rien
# d'utile. Mieux vaut refuser la demande que produire ce diagnostic.
MEM_FLOOR = 128 * 1024 * 1024

# Taille des tampons stdout/stderr conserves. La troncature garde la tete ET
# la queue : sur une sortie tronquee, l'information de diagnostic se trouve
# aux deux extremites, jamais au milieu.
CAPTURE_LIMIT = 256 * 1024

# Taille maximale de result.json. La reponse d'un job (resultat, stdout, stderr,
# manifeste des sorties) voyage dans UN en-tete de trame, borne a 1 MiB
# (protocol.MAX_HEADER) : la moitie au resultat, l'autre aux deux flux
# (CAPTURE_LIMIT chacun). Le fichier est ecrit par le job : execd n'en lit que
# cette taille, car fsize_bytes permet un resultat de 256 Mio, et le lire en
# entier (octets, texte, objets) gonflerait la memoire du service de plus de
# 600 Mo, partagee par toutes les sessions de la VM.
#
# Ce partage suppose un octet de flux par octet de JSON. Ce n'est pas le cas d'un
# flux plein de caracteres de controle (six octets chacun) : Session._send raccourcit
# alors les flux plutot que de couper la connexion.
RESULT_LIMIT = 512 * 1024


class LimitError(Exception):
    """Demande de limite irrecevable (et non simplement trop haute)."""

    def __init__(self, limit: str, value: int, floor: int) -> None:
        super().__init__(f"{limit} = {value} sous le plancher {floor}")
        self.limit = limit
        self.value = value
        self.floor = floor


def resolve(requested: dict[str, Any] | None) -> dict[str, int]:
    """Fusionne la demande avec les defauts, en plafonnant chaque valeur."""
    out = dict(DEFAULTS)
    if not requested:
        return out

    if not isinstance(requested, dict):
        raise LimitError("limits", 0, 0)

    for key, value in requested.items():
        if key not in DEFAULTS:
            # Cle inconnue ignoree plutot que refusee : un client plus recent
            # qui envoie une limite que cette version ignore doit continuer a
            # fonctionner, avec la limite par defaut.
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise LimitError(key, 0, 1)
        out[key] = min(value, CEILINGS[key])

    if out["mem_bytes"] < MEM_FLOOR:
        raise LimitError("mem_bytes", out["mem_bytes"], MEM_FLOOR)

    return out

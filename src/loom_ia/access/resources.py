# SPDX-License-Identifier: Apache-2.0
"""Vocabulaire des ressources ``loom://`` (N5, #32).

Ce que le serveur MCP expose en lecture seule, nommé ici et non dans son
module : la CLI l'annonce (``loom serve``, ``loom validate``) sans pouvoir
importer le SDK MCP, qui est un extra. C'est la même raison qui avait sorti
``Caller`` de l'accès REST en 5.2b.

Deux index se listent, et le reste se construit d'un gabarit — on ne peut pas
énumérer les runs d'un client sans ouvrir chaque journal :

- ``loom://runs`` : ses runs, du plus récemment écrit au plus ancien ;
- ``loom://sessions`` : ses journaux de session, le plus récent d'abord ;
- ``loom://runs/{run_id}{?session_id}`` et ``…/events`` : un run, sa trace ;
- ``loom://sessions/{session_id}`` et ``…/events`` : une session, son journal ;
- ``loom://artifacts/{client}/{session}/{fichier}`` : les octets d'un fichier.

La session est un **paramètre** et non un segment : un run appartient à une
session ou porte son propre identifiant, exactement comme en REST
(``GET /v1/runs/{id}?session_id=…``). Un fichier se lit aussi sous l'URI que
le journal publie (``artifact://…``), pour qu'un lien rendu par loom ne soit
pas un lien mort.
"""

from typing import Final

SCHEME: Final = "loom://"
RUNS: Final = "loom://runs"
SESSIONS: Final = "loom://sessions"
ARTIFACTS: Final = "loom://artifacts/"
EVENTS: Final = "/events"
JSON_TYPE: Final = "application/json"

# Ce que chaque gabarit construit, et ce qu'il rend. L'ordre est celui du
# module : un run, sa trace, une session, son journal, un fichier.
TEMPLATES: Final[tuple[tuple[str, str, str], ...]] = (
    (
        f"{RUNS}/{{run_id}}{{?session_id}}",
        "run",
        "Résultat d'un run : statut, réponse, coût, verdicts, approbations en attente",
    ),
    (
        f"{RUNS}/{{run_id}}{EVENTS}{{?session_id}}",
        "trace du run",
        "Journal d'un run et de ses sous-runs, dans l'ordre du journal",
    ),
    (
        f"{SESSIONS}/{{session_id}}",
        "session",
        "Fiche d'une session : ses runs, et ce qui attend un humain",
    ),
    (
        f"{SESSIONS}/{{session_id}}{EVENTS}",
        "trace de la session",
        "Journal entier d'une session, dans l'ordre du journal",
    ),
    (
        f"{ARTIFACTS}{{client}}/{{session}}/{{fichier}}",
        "fichier",
        "Octets d'un fichier du run (pièce jointe, sortie d'outil, déport)",
    ),
)

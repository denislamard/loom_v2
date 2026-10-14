# SPDX-License-Identifier: Apache-2.0
"""State d'une session : les valeurs que l'application et les outils y ont posées.

Le state n'a pas d'événement à lui : il voyage dans ceux qui existent. Le
``run.started`` d'un run porte ce que l'application a posé en l'ouvrant, et
le ``tool.completed`` d'un appel ce que l'outil a écrit (``output.state``).
Le journal fait foi ; le state en est une projection, recalculée sur tout le
journal, que les marqueurs de session (snapshot, compaction, coupe) ne
changent donc pas.

Ce qui compte, dans l'ordre du journal :

- le state posé par l'application, à l'ouverture de chaque run racine,
  quel que soit le sort du run ;
- les écritures d'un run racine terminé avec succès, à sa clôture, sans
  celles des appels qu'une réparation a retirés de l'historique (D2) ;
- en dernier, les écritures du run en cours, s'il est désigné : il voit
  les siennes avant d'être terminé.

Les sous-runs et les runs de compaction n'y entrent pas (D3), comme dans
l'historique. ``None`` efface une clé (D10). Les valeurs par défaut valent
pour une clé absente ; elles ne sont jamais écrites.
"""

import copy
from collections.abc import Iterable, Mapping

from pydantic import JsonValue

from loom_ia.core.events import Event, RunCompleted, RunStarted
from loom_ia.core.model import RunId, RunState
from loom_ia.core.projections.history import repaired
from loom_ia.core.projections.run_state import apply


def session_state(
    events: Iterable[Event],
    *,
    run_id: RunId | None = None,
    defaults: Mapping[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    """State de la session après ces événements.

    ``run_id`` : run en cours, dont les écritures comptent déjà. ``defaults`` :
    valeur de chaque clé absente du state. Le state rendu est une copie : le
    modifier ne touche ni aux événements lus, ni aux valeurs par défaut.
    """
    journal = list(events)
    started = {e.run_id for e in journal if isinstance(e.payload, RunStarted)}
    states: dict[RunId, RunState] = {}
    values: dict[str, JsonValue] = {}
    for event in journal:
        if event.category == "session" or event.run_id not in started:
            continue
        run = states[event.run_id] = apply(states.get(event.run_id), event)
        if not _counted(run):
            continue
        match event.payload:
            case RunStarted(state=posed):
                _merge(values, posed)
            case RunCompleted():
                _merge(values, _kept(run))
            case _:
                pass
    current = states.get(run_id) if run_id is not None else None
    if current is not None and _counted(current) and not current.finished:
        _merge(values, current.state_delta)
    return copy.deepcopy({**(defaults or {}), **values})


def _counted(run: RunState) -> bool:
    """Run dont le state compte pour la session : un run racine ordinaire (D3)."""
    return run.parent_run_id is None and run.kind == "normal"


def _kept(run: RunState) -> dict[str, JsonValue]:
    """Écritures d'un run terminé que garde l'historique de la session (D2).

    Une réparation retire de l'historique la réponse refusée et les résultats
    de ses appels : leurs écritures sont écartées avec eux, pour que le
    modèle ne lise jamais un state que l'historique ne justifie pas. C'est la
    position de la réponse qui le dit, et non l'identifiant de l'appel, qu'un
    fournisseur peut redonner après la réparation.
    """
    dropped = repaired(run) if run.repairs else set[int]()
    delta: dict[str, JsonValue] = {}
    for write in run.state_writes:
        if write.turn not in dropped:
            delta |= write.values
    return delta


def _merge(values: dict[str, JsonValue], delta: Mapping[str, JsonValue]) -> None:
    """Applique un delta au state : ``None`` efface la clé (D10)."""
    for key, value in delta.items():
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value

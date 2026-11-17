# SPDX-License-Identifier: Apache-2.0
"""Vie d'une session : historique matérialisé, compaction, nettoyage (fonctions F1 à F5 et F7).

- ``snapshot`` : vue matérialisée de l'historique (``session.snapshot``), et
  positions où couper le journal sans couper un run (``Cuts``) ;
- ``compaction`` : résumé des échanges anciens par l'agent interne
  ``_compaction`` (``session.compacted``), et coupe de sécurité
  (``session.trimmed``).

Config : ``sessions`` (``snapshot_every``, ``compaction``, ``media``,
``state``).
"""

from loom_ia.sessions.compaction import (
    CompactionJob,
    CompactionPlan,
    SummaryResolver,
    cut,
    oversized,
    run_starts,
    summarised,
)
from loom_ia.sessions.snapshot import (
    CHARS_PER_TOKEN,
    boundary,
    due,
    estimate_tokens,
    marked,
    snapshot,
    write_snapshot,
)

__all__ = [
    "CHARS_PER_TOKEN",
    "CompactionJob",
    "CompactionPlan",
    "SummaryResolver",
    "boundary",
    "cut",
    "due",
    "estimate_tokens",
    "marked",
    "oversized",
    "run_starts",
    "snapshot",
    "summarised",
    "write_snapshot",
]

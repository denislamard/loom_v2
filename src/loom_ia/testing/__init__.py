# SPDX-License-Identifier: Apache-2.0
"""Kit de test de loom-ia : faux modèles, faux outils, journaux fictifs (O2).

``Bench`` (J6.3c) met un agent de sa config au banc d'essai : faux modèles par
identifiant, faux outils par nom, les contrôles d'un cas d'éval.
``assert_replays`` (J6.3b) rejoue des journaux enregistrés : la non-régression
d'une config se teste comme le reste.
"""

from loom_ia.core.model import message_to_chunks
from loom_ia.testing.bench import Bench, BenchError, RefusedModel
from loom_ia.testing.fake_model import DEFAULT_USAGE, ScriptedModel, ScriptExhausted
from loom_ia.testing.journal import RunJournal, tool_call_message
from loom_ia.testing.replays import assert_replays

__all__ = [
    "DEFAULT_USAGE",
    "Bench",
    "BenchError",
    "RefusedModel",
    "RunJournal",
    "ScriptExhausted",
    "ScriptedModel",
    "assert_replays",
    "message_to_chunks",
    "tool_call_message",
]

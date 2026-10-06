# SPDX-License-Identifier: Apache-2.0
"""Kit de test de loom-ia : faux modèles, faux outils, journaux fictifs (O2).

``assert_replays`` (J6.3b) rejoue des journaux enregistrés : la non-régression
d'une config se teste comme le reste.
"""

from loom_ia.core.model import message_to_chunks
from loom_ia.testing.fake_model import DEFAULT_USAGE, ScriptedModel, ScriptExhausted
from loom_ia.testing.journal import RunJournal, tool_call_message
from loom_ia.testing.replays import assert_replays

__all__ = [
    "DEFAULT_USAGE",
    "RunJournal",
    "ScriptExhausted",
    "ScriptedModel",
    "assert_replays",
    "message_to_chunks",
    "tool_call_message",
]

# SPDX-License-Identifier: Apache-2.0
"""Politiques fournies par loom-ia, référencées par leur nom ``loom.…``.

Un agent les branche par ``hook: loom.<nom>`` dans ``policies``.
"""

from collections.abc import Mapping
from typing import Final

from loom_ia.core.model import CONTINUE, BeforeModel, Decision, Replace
from loom_ia.core.ports import Policy
from loom_ia.policies.function import FunctionPolicy


def _require_tool(subject: BeforeModel) -> Decision:
    """Impose un appel d'outil tant que le run n'en a fait aucun (backlog #012).

    Sans effet pendant la réponse forcée, quand la requête ne propose aucun
    outil, ou quand l'orchestrateur répare une réponse sans outils.

    Args:
        subject: La requête à envoyer au modèle et l'état du run.

    Returns:
        ``Replace`` d'une requête en ``tool_choice: required`` si le run n'a
        encore appelé aucun outil ; ``CONTINUE`` sinon.
    """
    request = subject.request
    if subject.finalizing or not request.tools or request.tool_choice != "auto":
        return CONTINUE
    if any(message.tool_calls for message in subject.state.messages):
        return CONTINUE
    return Replace(
        request.model_copy(update={"tool_choice": "required"}),
        reason="aucun outil appelé dans le run : appel d'outil imposé",
    )


require_tool: Final = FunctionPolicy(
    _require_tool,
    name="loom.require_tool",
    points=["before_model"],
    decisions=["replace"],
    builtin=True,
)
"""Politique ``loom.require_tool`` : point ``before_model``, décision ``replace``."""

BUILTIN_POLICIES: Final[Mapping[str, Policy]] = {require_tool.name: require_tool}
"""Politiques fournies, par nom."""

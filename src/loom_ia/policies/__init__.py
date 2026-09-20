# SPDX-License-Identifier: Apache-2.0
"""Politiques branchées sur les points d'accroche d'un run (#1, #2).

    from loom_ia.policies import BeforeTool, Decision, Deny, CONTINUE, policy

    @policy(points=["before_tool"], decisions=["deny"])
    def pas_le_dimanche(subject: BeforeTool) -> Decision:
        ...

Politiques fournies : ``loom.require_tool`` (appel d'outil imposé tant que le
run n'en a fait aucun).
"""

from loom_ia.core.model import (
    CONTINUE,
    AfterModel,
    AfterTool,
    BeforeModel,
    BeforeTool,
    Continue,
    Decision,
    DecisionKind,
    Deny,
    Fail,
    HookPoint,
    OnOutput,
    Pause,
    PolicyContext,
    PolicySubject,
    Replace,
    Retry,
    Stop,
)
from loom_ia.core.ports import Policy
from loom_ia.policies.builtin import BUILTIN_POLICIES, require_tool
from loom_ia.policies.function import FunctionPolicy, policy

__all__ = [
    "BUILTIN_POLICIES",
    "CONTINUE",
    "AfterModel",
    "AfterTool",
    "BeforeModel",
    "BeforeTool",
    "Continue",
    "Decision",
    "DecisionKind",
    "Deny",
    "Fail",
    "FunctionPolicy",
    "HookPoint",
    "OnOutput",
    "Pause",
    "Policy",
    "PolicyContext",
    "PolicySubject",
    "Replace",
    "Retry",
    "Stop",
    "policy",
    "require_tool",
]

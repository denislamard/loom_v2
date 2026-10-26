# SPDX-License-Identifier: Apache-2.0
"""Politiques de l'exemple de relance (phase 3.1), chargées par `imports` dans loom.yaml.

Le code décide, le LLM propose : chaque politique regarde ce qui se passe à
son point d'accroche et rend une décision. Les agents les branchent dans
leur section ``policies``.
"""

import re
from typing import Final

from loom_ia.core.model import RunState, ToolResultBlock
from loom_ia.policies import (
    CONTINUE,
    AfterTool,
    BeforeModel,
    BeforeTool,
    Decision,
    Deny,
    PolicyContext,
    Replace,
    Retry,
    Stop,
    policy,
)

NUMERO: Final = re.compile(r"D-?(\d{4})-?(\d{3})")


@policy(points=["before_tool"], decisions=["replace", "deny"])
def numero_devis(subject: BeforeTool) -> Decision:
    """Normalise le numéro passé à ``chercher_devis`` ; refuse un numéro mal formé."""
    if subject.spec.name != "chercher_devis":
        return CONTINUE
    brut = str(subject.arguments.get("numero", ""))
    found = NUMERO.fullmatch(brut.strip().upper())
    if found is None:
        return Deny(f"numéro de devis mal formé : {brut!r} (format attendu : D-AAAA-NNN)")
    numero = f"D-{found[1]}-{found[2]}"
    if numero == brut:
        return CONTINUE
    return Replace({**subject.arguments, "numero": numero}, reason=f"{brut!r} → {numero!r}")


@policy(points=["before_model"], decisions=["stop"])
def plafond_appels(subject: BeforeModel, context: PolicyContext) -> Decision:
    """Plus d'outils au-delà de ``max_appels`` appels du modèle : réponse forcée."""
    maximum = context.params.get("max_appels", 6)
    if isinstance(maximum, int) and subject.state.iterations >= maximum:
        return Stop(f"{maximum} appels du modèle atteints")
    return CONTINUE


@policy(points=["after_tool"], decisions=["retry"])
def cite_le_devis(subject: AfterTool) -> Decision:
    """La relance doit citer le numéro du devis trouvé dans le run.

    Le contrôle porte sur **l'e-mail, là où il est produit** : la sortie du
    rôle qui le rédige. Il valait auparavant sur la réponse finale du run,
    ce qui était juste tant que le rôle était terminal — sa sortie *était*
    la réponse. Dès qu'un agent ajoute un outil d'envoi, le rôle cesse de
    l'être et la réponse finale devient un compte rendu : exiger le numéro
    du devis dans « E-mail envoyé à Mme Martin. » n'a pas de sens, et
    demander alors de rédiger à nouveau fait refaire un travail déjà fait —
    au run réel du 21/09, cela a fait partir un second e-mail.

    Ici la réparation rappelle le rôle lui-même, qui est justement ce qu'il
    faut corriger, et aucune réponse finale n'est jamais concernée.
    """
    if subject.spec.name != "rediger_relance":
        return CONTINUE
    numero = devis_trouve(subject.state)
    if numero is None or numero in subject.output.as_text:
        return CONTINUE
    return Retry(f"la relance doit citer le numéro du devis {numero}.")


def devis_trouve(state: RunState) -> str | None:
    """Numéro du dernier devis trouvé par ``chercher_devis`` dans le run."""
    names = {call.call_id: call.name for message in state.messages for call in message.tool_calls}
    for message in reversed(state.messages):
        for block in message.blocks:
            if not isinstance(block, ToolResultBlock) or block.output.is_error:
                continue
            data = block.output.data
            if names.get(block.call_id) == "chercher_devis" and isinstance(data, dict):
                numero = data.get("numero")
                if isinstance(numero, str):
                    return numero
    return None

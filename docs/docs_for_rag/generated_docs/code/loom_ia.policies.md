# loom_ia.policies

Paquet `loom_ia.policies` de loom_v2 (fichier `src/loom_ia/policies/__init__.py`).

Politiques branchées sur les points d'accroche d'un run (points 1 et 2).

    from loom_ia.policies import BeforeTool, Decision, Deny, CONTINUE, policy

    @policy(points=["before_tool"], decisions=["deny"])
    def pas_le_dimanche(subject: BeforeTool) -> Decision:
        ...

Politiques fournies : `loom.require_tool` (appel d'outil imposé tant que le
run n'en a fait aucun).

Points d'accroche (`HookPoint`) : `before_model`, `after_model`,
`before_tool`, `after_tool`, `on_output`. Décisions : `Continue`
(`CONTINUE`), `Replace`, `Retry`, `Deny`, `Pause`, `Stop`,
`Fail` ; chaque point n'en permet que certaines. Un agent branche une
politique dans `agents.<nom>.policies` (`hook`, `points`, `params`,
`timeout`, `on_error`, `max_attempts`) ; leur exécution est décrite dans
`engine/hooks.py`.

# loom_ia.tools_builtin.current_datetime

Module `loom_ia.tools_builtin.current_datetime` de loom_v2 (fichier `src/loom_ia/tools_builtin/current_datetime.py`).

Outil current_datetime : la date et/ou l'heure actuelles, dans le fuseau horaire demandé.

## Fonction `loom_ia.tools_builtin.current_datetime.current_datetime`

```python
@tool(side_effects='none')
def current_datetime(part: Annotated[Literal['date', 'time', 'both'], Field(description="Ce qu'il faut rendre : la date, l'heure, ou les deux.")]='both', timezone: Annotated[str, Field(description='Fuseau horaire IANA, par exemple Europe/Paris ou America/Montreal.')]='Europe/Paris') -> dict[str, str]
```

Donne la date et/ou l'heure actuelles dans un fuseau horaire.

À utiliser dès qu'une réponse dépend du jour ou de l'heure (aujourd'hui, demain,
une échéance, un délai) : le modèle ne connaît pas la date du jour.

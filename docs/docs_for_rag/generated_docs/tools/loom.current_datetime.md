# Outil fourni loom.current_datetime

L'outil `loom.current_datetime` est fourni avec loom_v2. Un agent l'active dans son `agent.yaml` : `- python: loom.current_datetime` dans `tools`.

## Ce que fait loom.current_datetime

Outil current_datetime : la date et/ou l'heure actuelles, dans le fuseau horaire demandé.

## Comment loom.current_datetime est monté

- cible : `loom_ia.tools_builtin.current_datetime:current_datetime`

## Les arguments que le modèle donne à loom.current_datetime

Description de l'outil, telle que le modèle la reçoit :

```text
Donne la date et/ou l'heure actuelles dans un fuseau horaire.

À utiliser dès qu'une réponse dépend du jour ou de l'heure (aujourd'hui, demain,
une échéance, un délai) : le modèle ne connaît pas la date du jour.
```

- `part` (`"date" | "time" | "both"`) : Ce qu'il faut rendre : la date, l'heure, ou les deux.
- `timezone` (`string`) : Fuseau horaire IANA, par exemple Europe/Paris ou America/Montreal.

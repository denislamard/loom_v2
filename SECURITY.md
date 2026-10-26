# Politique de sécurité

## Versions prises en charge

Les correctifs de sécurité sont publiés sur la dernière version mineure de la série 2.x
(`pip install -U loom-ia`). Les versions antérieures ne reçoivent pas de correctif.

## Signaler une vulnérabilité

Ne la publiez pas dans une issue. Utilisez le signalement privé de GitHub :
<https://github.com/denislamard/loom_v2/security/advisories/new>
(onglet *Security* du dépôt, puis *Report a vulnerability*).

Indiquez la version de `loom-ia`, ce qui se passe, comment le reproduire et ce que cela permet.
Une faille confirmée est corrigée dans une version publiée, puis décrite dans `CHANGELOG.md`.

## Ce qui compte comme une faille

- l'API REST et le serveur MCP (`loom serve`) : authentification, clés d'API, cloisonnement
  entre clients (`tenant`) ;
- le journal et les fichiers chiffrés : lecture d'un contenu sans la clé du client ;
- la sandbox Firecracker (`firecracker/`) : sortie de la VM ou lecture d'une autre session ;
- l'outil fourni `loom.sql` : une instruction, une table ou un droit hors de ce que la config
  déclare, qui passerait l'analyse de l'outil ou les droits de la base ;
- toute fuite de secret dans les logs, le journal ou les messages d'erreur.

Une clé d'API de fournisseur de modèles reste à la charge de celui qui l'utilise : ne la
joignez jamais à un signalement.

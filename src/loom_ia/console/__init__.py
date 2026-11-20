# SPDX-License-Identifier: Apache-2.0
"""Le canal vers la console d'administration (docs/analyse-exortateur-loom.md).

Une connexion WSS **sortante**, ouverte par loom, qui porte dans les deux sens :

- vers la console, les événements des runs (``exporter``), au format des
  événements du REST (``translate``) ;
- vers loom, les commandes de la console, qui passent par les mêmes chemins
  que l'API REST et les droits de loom.

``contract`` fixe l'enveloppe et les messages (contrat 1) ; ``channel`` tient
la connexion. Seul ``channel`` importe ``websockets`` (extra ``console``), et
seulement quand une section ``console`` activée le demande.
"""

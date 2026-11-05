# SPDX-License-Identifier: Apache-2.0
"""Le noyau : modèle de domaine, événements, ports, projections."""

from typing import Final

TOLD: Final = "loom_told"
"""Clé ``extra`` d'un log dont l'appelant reçoit aussi le contenu.

Un échec de run (``RunResult.error``), un refus levé (``BudgetExhausted``), une
divergence de rejeu (le rapport) : le log sert l'exploitant d'un service, qui
ne voit pas ce que reçoit le client. Une commande qui le dit elle-même
l'écarte de sa console, pour ne pas le dire deux fois (lot B du 23/10).
"""

SETUP: Final = "loom_setup"
"""Clé ``extra`` d'un avertissement de configuration, dit au montage d'une instance.

Il concerne ce qui tourne (stockages d'un service, collecteurs, sceau) : une
commande qui ne fait que lire le journal l'écarte de sa console — au test H7
du 21/10, ``sessions show`` le répétait à chaque appel. ``validate``, ``run``
et le service le disent toujours.
"""

REPORTED: Final = "loom_reported"
"""Clé ``extra`` d'un avertissement que ``loom validate`` reprend dans son rapport.

``validate`` l'écarte de sa console, pour ne pas le dire deux fois ; ``run`` et
le service, qui n'ont pas de rapport, le disent comme les autres.
"""

AUDIT: Final = "loom_audit"
"""Clé ``extra`` du détail d'un refus d'accès (``access/audit.py``, D-9).

Qui, quoi, par quel accès, pourquoi : le format ``json`` des logs le rend sous
``audit``, pour qu'un collecteur le filtre sans relire le message.
"""

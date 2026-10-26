# SPDX-License-Identifier: Apache-2.0
"""Outil sql : lire et modifier des bases SQL permises, avec le SQL qu'écrit le modèle.

Le modèle écrit ses requêtes ; l'outil les **analyse** (``analysis``, sqlglot)
avant tout envoi — une instruction, une lecture pour ``query``, des
INSERT/UPDATE/DELETE pour ``execute``, seulement les tables et les droits que
la config déclare —, puis les fait exécuter par une base dont les **droits
font foi** : le rôle Postgres (``postgres``), l'authorizer SQLite (``sqlite``).
L'analyse décide aussi de l'approbation de chaque appel (``assess_call``),
sans toucher à la base. Jamais de DDL.

Trois actions : ``schema`` (le DDL des tables permises, avec leurs droits),
``query`` (un SELECT), ``execute`` (jusqu'à 20 écritures en une transaction).

sqlglot et asyncpg viennent de l'extra ``sql`` ; ce paquet n'est importé
qu'au montage d'un agent qui active l'outil, et asyncpg seulement pour une
base Postgres. Ses paramètres sont dans ``sql_params``, module léger que la
config importe seul.
"""

from loom_ia.tools_builtin.sql.arguments import NAME
from loom_ia.tools_builtin.sql.tool import SqlTool, build

__all__ = ["NAME", "SqlTool", "build"]

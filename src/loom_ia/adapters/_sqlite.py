# SPDX-License-Identifier: Apache-2.0
"""Mise en place commune des connexions SQLite des adaptateurs (journal, idempotence)."""

import asyncio
import random
import sqlite3
from typing import Final

import aiosqlite

# Patience laissée à la bascule en WAL : celle du ``busy_timeout`` des adaptateurs.
WAL_PATIENCE: Final = 5.0


async def enable_wal(connection: aiosqlite.Connection) -> None:
    """Met la base en mode WAL, même si un autre process la met en place au même instant.

    ``PRAGMA journal_mode = WAL`` est la seule instruction de mise en place que
    le délai d'occupation (``busy_timeout``) ne couvre pas : sur une base neuve
    que plusieurs process ouvrent ensemble, SQLite répond « database is locked »
    sans attendre. Or le mode WAL se garde dans le fichier : celui qui a perdu
    la course le trouve en place au nouvel essai. On lit donc le mode avant de
    le poser, et on reprend, dans la limite de ``WAL_PATIENCE``, tant que c'est
    un verrou qui refuse. Toute autre erreur (fichier qui n'est pas une base…)
    remonte aussitôt.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + WAL_PATIENCE
    pause = 0.005
    while True:
        if await _journal_mode(connection) == "wal":
            return
        try:
            cursor = await connection.execute("PRAGMA journal_mode = WAL")
            await cursor.close()
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc) or loop.time() >= deadline:
                raise
        await asyncio.sleep(pause + random.uniform(0, pause))
        pause = min(pause * 2, 0.1)


async def _journal_mode(connection: aiosqlite.Connection) -> str:
    cursor = await connection.execute("PRAGMA journal_mode")
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    return str(row[0]).lower() if row is not None else ""

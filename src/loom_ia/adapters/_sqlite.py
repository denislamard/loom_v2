# SPDX-License-Identifier: Apache-2.0
"""Mise en place commune des connexions SQLite des adaptateurs (journal, idempotence)."""

import asyncio
import logging
import random
import sqlite3
from pathlib import Path
from typing import Final

import aiosqlite

logger = logging.getLogger(__name__)

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


async def enable_secure_delete(connection: aiosqlite.Connection) -> None:
    """Fait écraser par des zéros ce que la base supprime (RGPD).

    ``PRAGMA secure_delete`` est propre à la connexion : à poser à chaque ouverture.
    """
    await connection.execute("PRAGMA secure_delete = ON")


async def purge_wal(connection: aiosqlite.Connection, path: Path) -> None:
    """Reporte le WAL dans la base puis le vide, pour qu'une suppression n'y survive pas.

    Sans cela, les pages d'avant la suppression restent dans le ``-wal`` jusqu'au
    prochain point de reprise. Un lecteur ouvert dans un autre process peut
    empêcher le point de reprise d'aboutir : SQLite répond alors par un drapeau
    « occupé », pas par une erreur. La suppression a eu lieu et reste valable ;
    on le dit par un avertissement, car le WAL n'est pas purgé. L'attente est
    celle du ``busy_timeout`` de la connexion.
    """
    try:
        cursor = await connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
    except sqlite3.OperationalError:
        logger.warning("%s : le WAL n'est pas purgé (point de reprise refusé)", path, exc_info=True)
        return
    if row is None or row[0]:
        logger.warning(
            "%s : le WAL n'est pas purgé (un lecteur tient la base) ; les octets supprimés"
            " y restent jusqu'au prochain point de reprise",
            path,
        )


async def _journal_mode(connection: aiosqlite.Connection) -> str:
    cursor = await connection.execute("PRAGMA journal_mode")
    try:
        row = await cursor.fetchone()
    finally:
        await cursor.close()
    return str(row[0]).lower() if row is not None else ""

# SPDX-License-Identifier: Apache-2.0
"""Le SQL du stockage Postgres : tables, index, rôle et politiques (fonction F5, point 5).

Ce module ne contient que du texte. Il ne connaît pas ``asyncpg``, et c'est
voulu : ``loom storage sql`` imprime le DDL sur une machine où le pilote n'est
pas installé, et l'exploitant qui l'applique n'a pas à installer loom.

Le DDL est **rejouable** : chaque instruction se rattrape si son objet existe
déjà, par ``IF NOT EXISTS`` quand Postgres le propose et par un bloc qui
avale ``duplicate_object`` quand il ne le propose pas — c'est le cas des
politiques et des rôles. Il peut donc être appliqué par loom à la première
ouverture, ou par un DBA à la main, ou les deux.

Deux rôles, et c'est là tout l'intérêt de la sécurité au niveau des lignes :

- le **propriétaire** est le rôle qui applique ce DDL. Il possède les tables
  et peut tout faire ;
- le rôle **applicatif** est celui sous lequel loom exécute ses requêtes. Il
  n'a que ``SELECT``, ``INSERT`` et ``DELETE`` sur le journal — pas
  ``UPDATE`` : l'immuabilité du journal cesse d'être une convention de code
  pour devenir un privilège de la base. Et comme il ne possède pas la table,
  la politique s'applique à lui.

Le DDL s'ouvre sur un ``HEADER`` en commentaires : ce que la politique change
pour qui lit la table à la main ou la sauvegarde. Un commentaire SQL ne gêne
pas son exécution, et c'est la seule place où l'exploitant le lira à coup sûr.

La politique compare ``tenant_id`` à ``current_setting('loom.tenant_id')``,
posé par transaction. Sans ce réglage, ``current_setting(..., true)`` rend
NULL, la comparaison est NULL, et **aucune ligne** ne passe : l'oubli est
donc un journal vide, jamais un journal partagé. ``FORCE ROW LEVEL
SECURITY`` étend la politique au propriétaire lui-même, pour que la barrière
tienne même si loom se connecte avec le rôle qui a créé les tables.
"""

import re
from typing import Final

EVENTS_TABLE: Final = "loom_events"
"""Les tables portent leur préfixe plutôt qu'un schéma : elles cohabitent avec
celles de l'application dans la base que le DSN désigne. Un client qui veut
son propre schéma déclare son propre ``dsn_env``, dont le rôle a son
``search_path`` (conception §7.3).
"""
IDEMPOTENCY_TABLE: Final = "loom_idempotency"
"""Table du magasin d'idempotence partagé."""
CONSOLE_CURSOR_TABLE: Final = "loom_console_cursor"
"""Curseur de la console d'administration : jusqu'où chaque session est acquittée (docs/analyse-exortateur-loom.md, § 6.2)."""
SCHEMA_TABLE: Final = "loom_schema"
"""Version du schéma de chaque composant (``events``), pour que le DDL sache ce
qui est déjà appliqué. Elle appartient au propriétaire ; le rôle applicatif
peut la lire, pas l'écrire.
"""

TENANT_SETTING: Final = "loom.tenant_id"
"""Réglage de transaction qui porte le client courant. Le point est obligatoire
: Postgres n'accepte un réglage inconnu de lui que préfixé.
"""

POLICY: Final = "loom_events_tenant"
"""Nom de la politique de lignes de la table des événements."""

CONSOLE_CURSOR_POLICY: Final = "loom_console_cursor_tenant"
"""Nom de la politique de lignes de la table du curseur de la console."""

DEFAULT_ROLE: Final = "loom_app"
"""Rôle applicatif par défaut : celui que crée le DDL et que prend chaque
connexion. ``storage.events.role: null`` s'en passe — la politique tient
quand même, par ``FORCE``, mais le journal redevient modifiable.
"""

_NAME: Final = re.compile(r"[a-z_][a-z0-9_]{0,62}")

HEADER: Final = """\
-- Stockage Postgres de loom (sortie de « loom storage sql »).
--
-- La table des événements est sous politique de lignes, FORCE comprise : une
-- session qui ne pose pas 'loom.tenant_id' ne voit AUCUNE ligne, y compris
-- celle du propriétaire des tables. Pour lire à la main, un client à la fois :
--     SET loom.tenant_id = '<client>';
--
-- Une sauvegarde logique échoue pour la même raison (« query would be affected
-- by row-level security policy »), et pg_dump --enable-row-security la rend
-- partielle sans le dire. Il faut un rôle qui contourne la politique, créé par
-- un superutilisateur — à réserver à l'exploitation :
--     CREATE ROLE loom_admin LOGIN PASSWORD '...' BYPASSRLS;
--     GRANT USAGE ON SCHEMA public TO loom_admin;
--     GRANT SELECT ON ALL TABLES IN SCHEMA public TO loom_admin;
-- Une sauvegarde physique (pg_basebackup, instantané de volume) n'est pas concernée."""
"""En tête du DDL, parce que la politique se remarque le jour où l'on veut lire
la table à la main ou la sauvegarder, et que ce jour-là on cherche loin.
"""


def check_name(name: str) -> str:
    """Refuse un nom de rôle qui ne s'écrit pas sans guillemets.

    Le DDL est du texte, pas une requête paramétrée : un nom venu de la
    config y est recopié tel quel. Plutôt que d'échapper, on n'accepte que ce
    qui n'a pas besoin de l'être.
    """
    if _NAME.fullmatch(name) is None:
        raise ValueError(f"Rôle Postgres {name!r} : lettres minuscules, chiffres et '_' seulement, 63 caractères au plus")
    return name


EVENTS_DDL: Final = f"""\
CREATE TABLE IF NOT EXISTS {EVENTS_TABLE} (
    tenant_id   text        NOT NULL,
    session_id  text        NOT NULL,
    seq         bigint      NOT NULL,
    event_id    text        NOT NULL,
    ts          timestamptz NOT NULL,
    run_id      text        NOT NULL,
    root_run_id text        NOT NULL,
    type        text        NOT NULL,
    category    text        NOT NULL,
    status      text        NOT NULL,
    agent       text,
    role        text,
    facets      jsonb       NOT NULL,
    event       text        NOT NULL,
    PRIMARY KEY (tenant_id, session_id, seq)
);
CREATE INDEX IF NOT EXISTS {EVENTS_TABLE}_run
    ON {EVENTS_TABLE} (tenant_id, run_id, seq);
CREATE INDEX IF NOT EXISTS {EVENTS_TABLE}_event_id
    ON {EVENTS_TABLE} (tenant_id, event_id);
CREATE INDEX IF NOT EXISTS {EVENTS_TABLE}_type
    ON {EVENTS_TABLE} (tenant_id, type, ts);
CREATE INDEX IF NOT EXISTS {EVENTS_TABLE}_facets
    ON {EVENTS_TABLE} USING gin (facets);
ALTER TABLE {EVENTS_TABLE} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {EVENTS_TABLE} FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY {POLICY} ON {EVENTS_TABLE}
        USING (tenant_id = current_setting('{TENANT_SETTING}', true))
        WITH CHECK (tenant_id = current_setting('{TENANT_SETTING}', true));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;"""
"""Table des événements, ses index et sa politique de lignes ; sans effet sur ce qui existe déjà."""

SCHEMA_DDL: Final = f"""\
CREATE TABLE IF NOT EXISTS {SCHEMA_TABLE} (
    component  text        NOT NULL PRIMARY KEY,
    version    integer     NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);"""
"""Table des versions du schéma."""


def _migration(component: str, version: int, body: str) -> str:
    """Une migration, jouée une seule fois : sa version est inscrite avec elle.

    Le test de version et l'inscription sont dans le même bloc, donc dans la
    même transaction que ``body`` : une migration qui échoue ne laisse ni
    changement ni version. ``$mig$`` : ``body`` peut contenir des ``$$``.
    """
    return f"""\
DO $mig$ BEGIN
    IF coalesce((SELECT version FROM {SCHEMA_TABLE} WHERE component = '{component}'), 0)
       < {version} THEN
{body}
        INSERT INTO {SCHEMA_TABLE} (component, version) VALUES ('{component}', {version})
        ON CONFLICT (component) DO UPDATE SET version = {version}, applied_at = now();
    END IF;
END $mig$;"""


IDEMPOTENCY_SCHEMA_VERSION: Final = 2
"""Version du schéma de l'idempotence que ce loom attend : en deçà, l'ouverture migre."""

IDEMPOTENCY_V2: Final = f"""\
        ALTER TABLE {IDEMPOTENCY_TABLE} ADD COLUMN IF NOT EXISTS holder text;"""
"""``holder`` est le jeton du détenteur de la clé : ``complete`` et ``release``
qui en présentent un ne touchent que la ligne encore à lui. La colonne est
nullable. Version 1 : la table de la 2.0, sans elle. Version 2 : la colonne,
ajoutée une seule fois — la version inscrite évite le verrou exclusif de
``ALTER TABLE`` à chaque ouverture.
"""

IDEMPOTENCY_UPGRADE: Final = "\n".join(
    [
        SCHEMA_DDL,
        f"INSERT INTO {SCHEMA_TABLE} (component, version) VALUES ('idempotency', 1) ON CONFLICT (component) DO NOTHING;",
        _migration("idempotency", IDEMPOTENCY_SCHEMA_VERSION, IDEMPOTENCY_V2),
    ]
)
"""Rejouable : c'est ce que loom applique à l'ouverture d'une table qui existe
déjà. Une table sans ligne de version est au moins en version 1.
"""

IDEMPOTENCY_DDL: Final = f"""\
CREATE TABLE IF NOT EXISTS {IDEMPOTENCY_TABLE} (
    key        text        NOT NULL PRIMARY KEY,
    tenant_id  text        NOT NULL,
    session_id text        NOT NULL,
    status     text        NOT NULL,
    result     text,
    expires_at timestamptz NOT NULL,
    holder     text
);
{IDEMPOTENCY_UPGRADE}
CREATE INDEX IF NOT EXISTS {IDEMPOTENCY_TABLE}_owner
    ON {IDEMPOTENCY_TABLE} (tenant_id, session_id);
CREATE INDEX IF NOT EXISTS {IDEMPOTENCY_TABLE}_expiry
    ON {IDEMPOTENCY_TABLE} (status, expires_at);"""
"""Pas de politique ici, et ce n'est pas un oubli : ``IdempotencyStore.get`` ne
nomme pas de client (``get(key)``), et une politique par client rendrait
invisible la ligne qu'il faut relire. Le cadrage vient de la clé — préfixée
par client pour une clé métier (point 49) — et des colonnes, qui portent le
client et la session pour l'oubli RGPD.
"""

EVENTS_SCHEMA_VERSION: Final = 3
"""Version du schéma du journal que ce loom attend : en deçà, l'ouverture migre."""

EVENTS_V2: Final = f"""\
        DROP INDEX IF EXISTS {EVENTS_TABLE}_event_id;
        CREATE UNIQUE INDEX {EVENTS_TABLE}_event_id ON {EVENTS_TABLE} (tenant_id, event_id);"""
"""Version 1 : le schéma de la 2.0 (``EVENTS_DDL``). Version 2 : l'identifiant
d'un événement est unique par client — l'index de la 2.0 ne l'était pas. La
création échoue s'il existe des doublons, et la migration entière s'annule.
"""

EVENTS_V3: Final = f"""\
        ALTER TABLE {EVENTS_TABLE} ADD COLUMN IF NOT EXISTS ingested bigint
            GENERATED ALWAYS AS IDENTITY;
        CREATE INDEX IF NOT EXISTS {EVENTS_TABLE}_ingested
            ON {EVENTS_TABLE} (tenant_id, ingested);"""
"""Version 3 : ``ingested``, le rang d'écriture, que ``after`` suit (l'id d'un
événement est posé avant son écriture, donc pas dans l'ordre où elle se
fait). Les lignes existantes reçoivent leur rang dans l'ordre où la table
les parcourt, ce qui est celui de leur écriture tant qu'aucune n'a été
supprimée.
"""

EVENTS_UPGRADE: Final = "\n".join(
    [
        SCHEMA_DDL,
        f"INSERT INTO {SCHEMA_TABLE} (component, version) VALUES ('events', 1) ON CONFLICT (component) DO NOTHING;",
        _migration("events", 2, EVENTS_V2),
        _migration("events", EVENTS_SCHEMA_VERSION, EVENTS_V3),
    ]
)
"""Rejouable : c'est ce que loom applique à l'ouverture d'une table qui existe
déjà. Une table de la 2.0 n'a pas de ligne de version : elle est au moins en
version 1.
"""


CONSOLE_CURSOR_SCHEMA_VERSION: Final = 1
"""Version du schéma du curseur de la console que ce loom attend."""

CONSOLE_CURSOR_DDL: Final = f"""\
CREATE TABLE IF NOT EXISTS {CONSOLE_CURSOR_TABLE} (
    tenant_id  text        NOT NULL,
    session_id text        NOT NULL,
    acked      bigint      NOT NULL DEFAULT 0,
    deleted    boolean     NOT NULL DEFAULT false,
    flushed_at timestamptz,
    PRIMARY KEY (tenant_id, session_id)
);
ALTER TABLE {CONSOLE_CURSOR_TABLE} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {CONSOLE_CURSOR_TABLE} FORCE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY {CONSOLE_CURSOR_POLICY} ON {CONSOLE_CURSOR_TABLE}
        USING (tenant_id = current_setting('{TENANT_SETTING}', true))
        WITH CHECK (tenant_id = current_setting('{TENANT_SETTING}', true));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
{SCHEMA_DDL}
INSERT INTO {SCHEMA_TABLE} (component, version) VALUES ('console_cursor', {CONSOLE_CURSOR_SCHEMA_VERSION}) ON CONFLICT (component) DO NOTHING;"""
"""Le curseur de la console : une ligne par session (``acked``, ``deleted``), et
une ligne par client à ``session_id`` vide pour ``flushed_at`` — une session n'a
jamais d'identifiant vide (``UnusableId``). Sous la même politique de lignes que
le journal, qu'il suit. Il se modifie (``UPDATE``) : c'est une position, pas un
événement.
"""


def role_ddl(role: str) -> str:
    """Création du rôle applicatif, sans erreur s'il est déjà là."""
    check_name(role)
    return f"""\
DO $$ BEGIN
    CREATE ROLE {role} NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;"""


def grants_ddl(role: str, *, table: str, update: bool) -> str:
    """Droits du rôle applicatif sur une table.

    ``update=False`` pour le journal : un événement écrit ne se modifie pas,
    et le rôle n'en a pas le droit. ``DELETE`` reste nécessaire, c'est la
    suppression RGPD d'une session (conception §11.5).
    """
    check_name(role)
    rights = "SELECT, INSERT, UPDATE, DELETE" if update else "SELECT, INSERT, DELETE"
    return f"GRANT {rights} ON {table} TO {role};"


def membership_ddl(role: str) -> str:
    """Rend le rôle applicatif endossable par celui qui applique ce DDL.

    C'est ce qui permet à loom de se connecter en propriétaire et de prendre
    le rôle applicatif (``SET ROLE``) à chaque connexion. Sans appartenance,
    il faut connecter loom directement avec le rôle applicatif.
    """
    check_name(role)
    return f"GRANT {role} TO CURRENT_USER;"


def ddl(*, role: str | None = DEFAULT_ROLE, events: bool = True, idempotency: bool = True, console_cursor: bool = False) -> str:
    """Le DDL complet, dans l'ordre où il s'applique.

    ``role=None`` laisse les droits au seul propriétaire : les tables et les
    politiques sont créées, mais rien n'est accordé à un rôle applicatif.
    ``console_cursor`` ajoute le curseur de la console, qui n'existe que si la
    config déclare une console active sur un journal Postgres.
    """
    blocks: list[str] = [HEADER]
    if role is not None:
        blocks.extend([role_ddl(role), membership_ddl(role)])
    if events:
        blocks.extend([EVENTS_DDL, EVENTS_UPGRADE])
        if role is not None:
            blocks.append(grants_ddl(role, table=EVENTS_TABLE, update=False))
    if idempotency:
        blocks.append(IDEMPOTENCY_DDL)
        if role is not None:
            blocks.append(grants_ddl(role, table=IDEMPOTENCY_TABLE, update=True))
    if console_cursor:
        blocks.append(CONSOLE_CURSOR_DDL)
        if role is not None:
            blocks.append(grants_ddl(role, table=CONSOLE_CURSOR_TABLE, update=True))
    if role is not None and (events or idempotency or console_cursor):
        # Lecture seule : de quoi vérifier la version sans pouvoir migrer.
        blocks.append(f"GRANT SELECT ON {SCHEMA_TABLE} TO {role};")
    return "\n".join(blocks) + "\n"

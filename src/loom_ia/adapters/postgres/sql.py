# SPDX-License-Identifier: Apache-2.0
"""Le SQL du stockage Postgres : tables, index, rôle et politiques (F5, #5).

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

# Les tables portent leur préfixe plutôt qu'un schéma : elles cohabitent avec
# celles de l'application dans la base que le DSN désigne. Un client qui veut
# son propre schéma déclare son propre ``dsn_env``, dont le rôle a son
# ``search_path`` (§7.3).
EVENTS_TABLE: Final = "loom_events"
IDEMPOTENCY_TABLE: Final = "loom_idempotency"

# Réglage de transaction qui porte le client courant. Le point est
# obligatoire : Postgres n'accepte un réglage inconnu de lui que préfixé.
TENANT_SETTING: Final = "loom.tenant_id"

POLICY: Final = "loom_events_tenant"

# Rôle applicatif par défaut : celui que crée le DDL et que prend chaque
# connexion. ``storage.events.role: null`` s'en passe — la politique tient
# quand même, par ``FORCE``, mais le journal redevient modifiable.
DEFAULT_ROLE: Final = "loom_app"

_NAME: Final = re.compile(r"[a-z_][a-z0-9_]{0,62}")

# En tête du DDL, parce que la politique se remarque le jour où l'on veut lire
# la table à la main ou la sauvegarder, et que ce jour-là on cherche loin.
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


def check_name(name: str) -> str:
    """Refuse un nom de rôle qui ne s'écrit pas sans guillemets.

    Le DDL est du texte, pas une requête paramétrée : un nom venu de la
    config y est recopié tel quel. Plutôt que d'échapper, on n'accepte que ce
    qui n'a pas besoin de l'être.
    """
    if _NAME.fullmatch(name) is None:
        raise ValueError(
            f"Rôle Postgres {name!r} : lettres minuscules, chiffres et '_' seulement, "
            "63 caractères au plus"
        )
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

# Pas de politique ici, et ce n'est pas un oubli : ``IdempotencyStore.get``
# ne nomme pas de client (``get(key)``), et une politique par client rendrait
# invisible la ligne qu'il faut relire. Le cadrage vient de la clé — préfixée
# par client pour une clé métier (#49) — et des colonnes, qui portent le
# client et la session pour l'oubli RGPD.
IDEMPOTENCY_DDL: Final = f"""\
CREATE TABLE IF NOT EXISTS {IDEMPOTENCY_TABLE} (
    key        text        NOT NULL PRIMARY KEY,
    tenant_id  text        NOT NULL,
    session_id text        NOT NULL,
    status     text        NOT NULL,
    result     text,
    expires_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS {IDEMPOTENCY_TABLE}_owner
    ON {IDEMPOTENCY_TABLE} (tenant_id, session_id);
CREATE INDEX IF NOT EXISTS {IDEMPOTENCY_TABLE}_expiry
    ON {IDEMPOTENCY_TABLE} (status, expires_at);"""


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
    suppression RGPD d'une session (§11.5).
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


def ddl(*, role: str | None = DEFAULT_ROLE, events: bool = True, idempotency: bool = True) -> str:
    """Le DDL complet, dans l'ordre où il s'applique.

    ``role=None`` laisse les droits au seul propriétaire : les tables et les
    politiques sont créées, mais rien n'est accordé à un rôle applicatif.
    """
    blocks: list[str] = [HEADER]
    if role is not None:
        blocks.extend([role_ddl(role), membership_ddl(role)])
    if events:
        blocks.append(EVENTS_DDL)
        if role is not None:
            blocks.append(grants_ddl(role, table=EVENTS_TABLE, update=False))
    if idempotency:
        blocks.append(IDEMPOTENCY_DDL)
        if role is not None:
            blocks.append(grants_ddl(role, table=IDEMPOTENCY_TABLE, update=True))
    return "\n".join(blocks) + "\n"

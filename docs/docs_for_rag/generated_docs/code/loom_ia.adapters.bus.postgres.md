# loom_ia.adapters.bus.postgres

Module `loom_ia.adapters.bus.postgres` de loom_v2 (fichier `src/loom_ia/adapters/bus/postgres.py`).

Bus des nouvelles par `LISTEN`/`NOTIFY` de Postgres (point 5, fonction H6).

Le bus de qui a déjà Postgres : il n'y a rien de plus à installer, et le
journal et les nouvelles tiennent sur la même base — donc sur la même panne,
ce qui est une qualité : un service qui perd sa base n'a plus de journal à
suivre de toute façon.

Une **connexion à part**, pas celle du pool du journal : une connexion qui
écoute ne peut rien faire d'autre, et elle doit rester ouverte. Elle ne prend
pas non plus le rôle applicatif — `LISTEN` ne lit aucune table, donc la
politique de lignes n'a rien à y voir, et le contenu, lui, ne passe pas par
là (c'est tout l'intérêt de ne transporter qu'une nouvelle).

La charge utile d'un `NOTIFY` est bornée à 8 000 octets. Une nouvelle en
fait moins de deux cents, et c'est précisément pourquoi elle ne porte que des
repères.

## Constantes de `loom_ia.adapters.bus.postgres`

- `CHANNEL = 'loom_events'` : Le canal d'écoute. Fixe : deux déploiements sur la même base partagent leurs nouvelles, ce qui est sans effet puisque chacun relit son propre journal.

## Classe `loom_ia.adapters.bus.postgres.PostgresBus`

```python
class PostgresBus
```

Nouvelles diffusées par le canal `loom_events` d'une base Postgres.

### Méthode `loom_ia.adapters.bus.postgres.PostgresBus.publish`

```python
async def publish(self, notice: Notice) -> None
```

Annonce un lot écrit par `pg_notify` sur le canal `loom_events`.

La connexion de publication est ouverte au premier besoin ; une connexion
qui échoue est jetée, la suivante en ouvre une autre. Rien n'est publié
après la fermeture.

### Méthode `loom_ia.adapters.bus.postgres.PostgresBus.notices`

```python
async def notices(self) -> AsyncIterator[Notice]
```

Écoute le canal jusqu'à la fermeture du bus.

Si la connexion d'écoute meurt, lève `BusUnavailable` au lieu
d'attendre une nouvelle qui ne viendra plus : c'est à l'appelant de
s'abonner de nouveau, par un autre appel.

### Méthode `loom_ia.adapters.bus.postgres.PostgresBus._lost`

```python
def _lost(self, connection: object) -> None
```

Appelé par asyncpg à la mort d'une connexion ; ne doit rien attendre.

### Méthode `loom_ia.adapters.bus.postgres.PostgresBus._heard`

```python
def _heard(self, _connection: object, _pid: int, _channel: str, payload: object) -> None
```

Appelé par asyncpg à chaque `NOTIFY` ; ne doit rien attendre.

### Méthode `loom_ia.adapters.bus.postgres.PostgresBus.aclose`

```python
async def aclose(self) -> None
```

Ferme le bus : l'écoute s'arrête, les connexions sont fermées.

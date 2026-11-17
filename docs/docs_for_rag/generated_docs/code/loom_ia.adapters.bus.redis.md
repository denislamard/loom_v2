# loom_ia.adapters.bus.redis

Module `loom_ia.adapters.bus.redis` de loom_v2 (fichier `src/loom_ia/adapters/bus/redis.py`).

Bus des nouvelles par la publication/abonnement de Redis (point 5, fonction H6).

Le bus de qui a déjà Redis, ou qui ne veut pas faire porter ses nouvelles par
sa base. Redis publie sans rien garder : un abonné absent au moment de la
publication ne la verra jamais. C'est exactement ce que le port assume — ce
qui est manqué se rattrape à la nouvelle suivante, puisque l'abonné garde sa
position et relit le journal depuis elle.

Une connexion à part pour écouter, comme chez Postgres : un abonnement
occupe sa connexion.

Le SDK `redis` est typé, mais ses méthodes asynchrones laissent des
`Unknown` (des `**kwargs` non typés) : pyright strict s'en plaint à chaque
appel. Plutôt que huit `ignore` en ligne, les trois règles concernées sont
levées pour ce module — qui tient en quatre-vingts lignes et ne fait que
publier et écouter.

## Constantes de `loom_ia.adapters.bus.redis`

- `CHANNEL = 'loom:events'` : Le canal des nouvelles. Fixe, comme le canal Postgres.
- `SOCKET_TIMEOUT = 5.0` : Délai d'une connexion, et d'une réponse à une publication, en secondes. L'écoute n'en a pas (le silence du canal y est normal) : un contrôle de santé la remplace.
- `HEALTH_CHECK = 30` : Intervalle du contrôle de santé de la connexion d'écoute, en secondes.

## Classe `loom_ia.adapters.bus.redis.RedisBus`

```python
class RedisBus
```

Nouvelles diffusées par le canal `loom:events` d'un serveur Redis.

### Méthode `loom_ia.adapters.bus.redis.RedisBus.publish`

```python
async def publish(self, notice: Notice) -> None
```

Annonce un lot écrit sur le canal `loom:events` de Redis ; rien après la fermeture.

### Méthode `loom_ia.adapters.bus.redis.RedisBus.notices`

```python
async def notices(self) -> AsyncIterator[Notice]
```

Écoute le canal jusqu'à la fermeture du bus.

Si la connexion d'écoute tombe, l'erreur du SDK (`ConnectionError`)
sort telle quelle : c'est à l'appelant de s'abonner de nouveau.

### Méthode `loom_ia.adapters.bus.redis.RedisBus.aclose`

```python
async def aclose(self) -> None
```

Ferme le bus : le désabonnement arrête l'écoute, puis les connexions sont fermées.

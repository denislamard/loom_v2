# loom_ia.adapters.bus.memory

Module `loom_ia.adapters.bus.memory` de loom_v2 (fichier `src/loom_ia/adapters/bus/memory.py`).

Bus en mémoire : le défaut, et de quoi éprouver le branchement (point 5).

Il ne franchit aucune frontière de process — dans un seul process, le journal
notifiant remet déjà ses écritures à ses abonnés, donc ce bus ne sert à rien
en exploitation. Il sert à deux choses : garder un montage uniforme (il y a
toujours un bus, même quand il n'y a rien à traverser), et éprouver le
suivi — curseurs, rattrapage, nouvelles ignorées — sans service à installer.

Chaque abonné a sa file, sans borne : un abonné lent retarde sa propre
lecture, jamais la publication.

## Classe `loom_ia.adapters.bus.memory.InMemoryBus`

```python
class InMemoryBus
```

Bus du process : ce qui est publié va aux abonnés de cette instance.

### Méthode `loom_ia.adapters.bus.memory.InMemoryBus.subscribers`

```python
@property
def subscribers(self) -> int
```

Abonnés en cours.

### Méthode `loom_ia.adapters.bus.memory.InMemoryBus.publish`

```python
async def publish(self, notice: Notice) -> None
```

Remet la nouvelle à chaque abonné de cette instance.

### Méthode `loom_ia.adapters.bus.memory.InMemoryBus.notices`

```python
async def notices(self) -> AsyncIterator[Notice]
```

Les nouvelles publiées sur cette instance, jusqu'à la fermeture du bus.

### Méthode `loom_ia.adapters.bus.memory.InMemoryBus.aclose`

```python
async def aclose(self) -> None
```

Ferme le bus : l'écoute de chaque abonné s'arrête.

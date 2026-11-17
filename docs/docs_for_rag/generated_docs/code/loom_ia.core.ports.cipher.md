# loom_ia.core.ports.cipher

Module `loom_ia.core.ports.cipher` de loom_v2 (fichier `src/loom_ia/core/ports/cipher.py`).

Port du sceau : fermer et ouvrir des octets avec la clé d'un client (point 30, conception §11.5, fonction P1).

Le journal garde les contenus complets — c'est lui qui fait foi (point 22). Ce
port permet de les garder **illisibles au repos** : la charge d'un événement
et les octets d'un fichier sont fermés avec la clé du client à qui ils
appartiennent. Effacer cette clé rend tout son journal illisible,
sauvegardes comprises : c'est le *crypto-shredding* du RGPD, en complément
de la suppression physique (point 22).

Une clé par client, et loom ne la détient jamais : elle vit dans les secrets
du client (`SecretProvider`), comme les clés de ses fournisseurs. La
config ne nomme que le secret qui la porte.

`key_id` est une **empreinte** de la clé : elle dit laquelle a fermé un
sceau, sans rien dire de la clé elle-même. C'est ce qui rend le
renouvellement possible — la nouvelle clé ferme, les anciennes ouvrent
encore ce qu'elles ont fermé — et ce qui permet de **nommer** la clé qui
manque au lieu de la deviner.

`aad` (*additional authenticated data*) n'est pas chiffré : il est
authentifié. Un sceau ouvert avec un `aad` différent de celui qui l'a
fermé est refusé, ce qui interdit de déplacer un sceau — d'un journal à un
autre, d'une position à une autre, d'un fichier à un autre.

Implémentation fournie : AES-256-GCM (`adapters/crypto/aesgcm.py`, extra
`crypto`). Le sceau se déclare sous `storage.encryption` (phase 5.5b).

## Classe `loom_ia.core.ports.cipher.SealError`

```python
class SealError(Exception)
```

Un sceau n'a pas pu être fermé ou ouvert ; base de `MissingKey` et `SealBroken`.

## Classe `loom_ia.core.ports.cipher.MissingKey`

```python
class MissingKey(SealError)
```

Aucune clé pour ce client, ou pas celle qui a fermé ce sceau.

Le cas voulu du *crypto-shredding* : la clé effacée, ce qu'elle a fermé
ne s'ouvre plus. Le cas subi d'une variable oubliée lui ressemble trait
pour trait, et la configuration ne peut pas les distinguer — le message
nomme donc le client et l'empreinte attendue, et laisse conclure.

## Classe `loom_ia.core.ports.cipher.SealBroken`

```python
class SealBroken(SealError)
```

Le sceau ne s'ouvre pas avec cette clé : octets ou `aad` altérés.

## Classe `loom_ia.core.ports.cipher.Cipher`

```python
class Cipher(Protocol)
```

Une clé, en service : elle ferme et elle ouvre.

### Méthode `loom_ia.core.ports.cipher.Cipher.key_id`

```python
@property
def key_id(self) -> str
```

Empreinte de la clé : elle dit laquelle, sans la dire.

### Méthode `loom_ia.core.ports.cipher.Cipher.seal`

```python
def seal(self, clear: bytes, aad: bytes) -> bytes
```

Ferme des octets avec la clé.

**Arguments :**

- `clear` : Les octets en clair.
- `aad` : Données authentifiées, pas chiffrées (le journal et la position, le chemin d'un fichier) : il faudra les mêmes pour ouvrir.

**Renvoie :**

Le sceau.

### Méthode `loom_ia.core.ports.cipher.Cipher.unseal`

```python
def unseal(self, sealed: bytes, aad: bytes) -> bytes
```

Ouvre un sceau fermé par cette clé.

**Arguments :**

- `sealed` : Le sceau.
- `aad` : Les mêmes données authentifiées qu'à la fermeture.

**Renvoie :**

Les octets en clair.

**Lève :**

- `SealBroken` : Le sceau ne correspond pas : octets ou `aad` altérés, ou sceau déplacé.

## Classe `loom_ia.core.ports.cipher.Keyring`

```python
class Keyring(Protocol)
```

Les clés des clients, telles que la configuration les désigne.

### Méthode `loom_ia.core.ports.cipher.Keyring.ciphers`

```python
def ciphers(self, tenant_id: TenantId) -> Sequence[Cipher]
```

Clés d'un client : la première ferme, toutes peuvent ouvrir.

**Arguments :**

- `tenant_id` : Le client.

**Renvoie :**

Ses clés, la clé courante en tête, puis les anciennes, gardées
pour ouvrir ce qu'elles ont fermé.

**Lève :**

- `MissingKey` : Le client n'a aucune clé : un trousseau existe parce que la config a demandé le sceau, et écrire en clair sous prétexte qu'une clé manque serait exactement la fuite que le sceau est là pour empêcher. Aussi quand la clé de tête manque alors qu'une clé de queue est là : sceller avec cette dernière, sans rien dire, ferait d'un renouvellement qui n'a pas lieu un succès.

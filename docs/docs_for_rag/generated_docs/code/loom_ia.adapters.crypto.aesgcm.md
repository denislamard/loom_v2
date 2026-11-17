# loom_ia.adapters.crypto.aesgcm

Module `loom_ia.adapters.crypto.aesgcm` de loom_v2 (fichier `src/loom_ia/adapters/crypto/aesgcm.py`).

Sceau AES-256-GCM, et trousseau lu dans les secrets des clients (point 30, conception §11.5).

Un seul algorithme : AES-256-GCM, chiffrement authentifié. Le sceau porte son
nonce (12 octets tirés au hasard à chaque fermeture) suivi des octets fermés ;
l'`aad` est authentifié sans être chiffré, si bien qu'un sceau déplacé ne
s'ouvre pas.

La clé ne vient pas de la configuration mais des **secrets du client** : la
config nomme le secret (`storage.encryption.keys`), et chaque client
redirige ce nom vers sa variable (`secrets: {journal_key: MARTIN_JOURNAL_KEY}`).
C'est le mécanisme de 5.1a, sans rien de neuf — et sa règle joue ici à plein :
une redirection vers une variable **absente** vaut vide, jamais le secret
commun. Effacer la variable d'un client scelle donc son journal pour de bon,
sans que celui du voisin y soit pour quelque chose.

Plusieurs noms sont possibles, dans l'ordre : le premier ferme, tous ouvrent.
C'est le renouvellement — on met la nouvelle clé devant, l'ancienne reste
derrière le temps que les anciens journaux servent encore.

Le premier nom doit avoir sa clé : absent ou vide, le client est refusé comme
s'il n'en avait aucune (`MissingKey`, qui nomme ce secret), et jamais scellé
avec une clé de queue à sa place. Un secret de queue absent est toléré : il ne
sert qu'à ouvrir, et un client arrivé après le renouvellement ne l'a jamais eu.

L'empreinte (`key_id`) est un condensé de la clé, pas la clé : elle tient
dans une ligne de log ou un message d'erreur, et elle suffit à dire laquelle
a fermé un sceau — donc laquelle manque.

## Constantes de `loom_ia.adapters.crypto.aesgcm`

- `KEY_BYTES = 32` : AES-256 : la clé fait 32 octets, et rien d'autre ne passe.
- `NONCE_BYTES = 12` : Nonce de GCM : 96 bits, la taille pour laquelle il est spécifié.
- `FINGERPRINT_CHARS = 12` : Ce que l'empreinte garde du condensé : assez pour distinguer des clés, trop peu pour servir à autre chose.

## Fonction `loom_ia.adapters.crypto.aesgcm.how_to_make_one`

```python
def how_to_make_one() -> str
```

Comment fabriquer une clé, à dire dans un message d'erreur.

## Fonction `loom_ia.adapters.crypto.aesgcm.fingerprint`

```python
def fingerprint(key: bytes) -> str
```

Empreinte d'une clé : elle dit laquelle, sans la dire.

## Fonction `loom_ia.adapters.crypto.aesgcm.decode_key`

```python
def decode_key(value: str, *, what: str) -> bytes
```

Clé de 32 octets tirée d'un secret en base64 ; `SealError` sinon.

Base64 standard ou *urlsafe*, remplissage facultatif : ce qu'un opérateur
obtient de sa commande, quelle qu'elle soit, sans avoir à y penser.

## Classe `loom_ia.adapters.crypto.aesgcm.AesGcmCipher`

```python
class AesGcmCipher
```

Une clé AES-256-GCM en service.

### Méthode `loom_ia.adapters.crypto.aesgcm.AesGcmCipher.key_id`

```python
@property
def key_id(self) -> str
```

Empreinte de la clé : elle dit laquelle, sans la dire.

### Méthode `loom_ia.adapters.crypto.aesgcm.AesGcmCipher.seal`

```python
def seal(self, clear: bytes, aad: bytes) -> bytes
```

Ferme des octets : un nonce aléatoire de 96 bits, puis le chiffré authentifié.

**Arguments :**

- `clear` : Les octets à fermer.
- `aad` : Ce que le sceau authentifie sans le chiffrer (journal, place, URI…).

### Méthode `loom_ia.adapters.crypto.aesgcm.AesGcmCipher.unseal`

```python
def unseal(self, sealed: bytes, aad: bytes) -> bytes
```

Ouvre un sceau fermé par cette clé.

**Arguments :**

- `sealed` : Le nonce suivi du chiffré.
- `aad` : Ce qui a été authentifié à la fermeture.

**Lève :**

- `SealBroken` : Sceau trop court, octets ou `aad` altérés, ou autre clé.

## Classe `loom_ia.adapters.crypto.aesgcm.SecretKeyring`

```python
class SecretKeyring
```

`Keyring` adossé aux secrets des clients.

Les clés sont résolues au premier besoin et gardées : un client garde les
mêmes objets tout du long, et l'empreinte ne se recalcule pas à chaque
événement.

### Méthode `loom_ia.adapters.crypto.aesgcm.SecretKeyring.names`

```python
@property
def names(self) -> tuple[str, ...]
```

Secrets qui portent les clés : le premier ferme, tous ouvrent.

### Méthode `loom_ia.adapters.crypto.aesgcm.SecretKeyring.ciphers`

```python
def ciphers(self, tenant_id: TenantId) -> Sequence[Cipher]
```

Clés d'un client, résolues au premier besoin puis gardées : la première ferme, toutes ouvrent.

**Lève :**

- `MissingKey` : Aucun secret de clé, ou le secret de tête absent ou vide.
- `SealError` : Une clé n'est pas du base64 de 32 octets.

### Méthode `loom_ia.adapters.crypto.aesgcm.SecretKeyring._resolve`

```python
def _resolve(self, tenant_id: TenantId) -> tuple[Cipher, ...]
```

Les clés d'un client, lues dans ses secrets dans l'ordre déclaré ; le secret de tête, qui scelle, doit être là.

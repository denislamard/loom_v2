# loom_ia.config.keys

Module `loom_ia.config.keys` de loom_v2 (fichier `src/loom_ia/config/keys.py`).

Clés d'API : fabrication, empreinte, vérification (points 39 et 50).

La config ne contient jamais la clé, seulement son empreinte. ``loom keys
create`` affiche la clé une fois et donne l'empreinte à coller.

## Constantes de `loom_ia.config.keys`

- `PREFIX = 'lk_'` : Préfixe identifiable, pour repérer une clé dans un journal ou un ticket.
- `FINGERPRINT = re.compile(f'{ALGORITHM}:[0-9a-f]{{64}}')` : La forme d'une empreinte : l'algorithme, puis le condensé en hexadécimal minuscule.

## Fonction `loom_ia.config.keys.new_api_key`

```python
def new_api_key() -> str
```

Nouvelle clé, à n'afficher qu'une fois.

**Renvoie :**

`lk_` suivi de 43 caractères aléatoires (32 octets en base64 URL).

## Fonction `loom_ia.config.keys.fingerprint`

```python
def fingerprint(key: str) -> str
```

Empreinte à écrire dans la config (`sha256:…`).

**Arguments :**

- `key` : La clé en clair.

## Fonction `loom_ia.config.keys.matches`

```python
def matches(key: str, expected: str) -> bool
```

Comparaison à temps constant entre une clé et une empreinte.

Elle porte sur des octets : `compare_digest` refuse deux `str` dès que
l'une n'est pas ASCII, et une empreinte mal formée ferait alors un 500 au
lieu d'un 401. La config ne laisse passer que `FINGERPRINT`, mais cette
comparaison ne compte pas là-dessus.

**Arguments :**

- `key` : La clé présentée, en clair.
- `expected` : L'empreinte déclarée.

**Renvoie :**

Vrai si l'empreinte de la clé est celle attendue.

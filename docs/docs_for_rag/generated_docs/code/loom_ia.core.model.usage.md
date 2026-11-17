# loom_ia.core.model.usage

Module `loom_ia.core.model.usage` de loom_v2 (fichier `src/loom_ia/core/model/usage.py`).

Consommation de tokens et tarifs des modèles (fonctions J1 et J2).

`Usage` compte les tokens d'un appel ou d'un run ; `Pricing` (sous
`models[].pricing`) en tire le coût en dollars.

## Constantes de `loom_ia.core.model.usage`

- `CHARS_PER_TOKEN = 4` : Caractères (de JSON) par token, en approximation.  C'est l'estimation de loom quand le fournisseur ne compte pas (contrôle de la fenêtre de contexte, fonction B7 ; seuils de compaction), et le compte du modèle simulé.
- `TOKENS_PER_UNIT = 1000000` : Tokens auxquels s'applique un prix de la grille : les prix sont par million de tokens.

## Classe `loom_ia.core.model.usage.Usage`

```python
class Usage(DomainModel)
```

Tokens consommés par un appel de modèle, ou cumulés sur un run (fonction J1).

Deux `Usage` s'additionnent avec `+`.

**Attributs :**

- `input_tokens` : Tokens d'entrée hors cache.
- `output_tokens` : Tokens de sortie, raisonnement compris.
- `cache_read_tokens` : Tokens d'entrée lus dans le cache de prompt.
- `cache_write_tokens` : Tokens d'entrée écrits dans le cache de prompt.
- `reasoning_tokens` : Tokens de raisonnement, quand le fournisseur les distingue ; déjà comptés dans `output_tokens`.

### Méthode `loom_ia.core.model.usage.Usage.__add__`

```python
def __add__(self, other: Self) -> Self
```

Somme champ par champ de deux consommations.

### Méthode `loom_ia.core.model.usage.Usage.total_tokens`

```python
@property
def total_tokens(self) -> int
```

Total des tokens : entrée (cache compris) et sortie.

### Méthode `loom_ia.core.model.usage.Usage.prompt_tokens`

```python
@property
def prompt_tokens(self) -> int
```

Tokens d'entrée d'un appel, cache compris : ils choisissent le palier du tarif.

## Classe `loom_ia.core.model.usage.PriceTier`

```python
class PriceTier(DomainModel)
```

Palier de tarif : prix d'un appel dont les tokens d'entrée (cache compris) dépassent `above`.

**Attributs :**

- `above` : Seuil de tokens d'entrée par appel, cache compris.
- `input` : Prix des tokens d'entrée, en dollars par million ; absent : celui du tarif de base.
- `output` : Prix des tokens de sortie ; absent : celui du tarif de base.
- `cache_read` : Prix des tokens lus dans le cache ; absent : celui du tarif de base.
- `cache_write` : Prix des tokens écrits dans le cache ; absent : celui du tarif de base.

## Classe `loom_ia.core.model.usage.Pricing`

```python
class Pricing(DomainModel)
```

Grille tarifaire d'un modèle, en dollars par million de tokens.

Les tokens de raisonnement sont déjà comptés dans `output_tokens` par les
fournisseurs : ils ne sont pas facturés une seconde fois.

Paliers (`tiers`) : certains fournisseurs changent de prix au-delà d'un
nombre de tokens d'entrée par appel (MiniMax-M3 double ses prix au-delà de
512k). Le palier le plus haut dont le seuil est dépassé s'applique à tout
l'appel.

**Attributs :**

- `input` : Prix des tokens d'entrée hors cache, en dollars par million. Défaut : 0.
- `output` : Prix des tokens de sortie. Défaut : 0.
- `cache_read` : Prix des tokens lus dans le cache. Défaut : 0.
- `cache_write` : Prix des tokens écrits dans le cache (celui de la durée de cache choisie). Défaut : 0.
- `tiers` : Paliers, aux seuils `above` strictement croissants.

**Lève :**

- `ValueError` : Les seuils des paliers ne sont pas strictement croissants.

**Exemples :**

Sous `models[]` ::

    pricing:
      input: 0.3
      output: 1.2
      tiers:
      - { above: 512000, input: 0.6, output: 2.4 }

### Méthode `loom_ia.core.model.usage.Pricing._check_tiers`

```python
@model_validator(mode='after')
def _check_tiers(self) -> Self
```

Exige des seuils `above` strictement croissants.

### Méthode `loom_ia.core.model.usage.Pricing.priced`

```python
@property
def priced(self) -> bool
```

Faux pour un modèle sans tarif : ses appels comptent 0 $ (backlog #010).

### Méthode `loom_ia.core.model.usage.Pricing.rates`

```python
def rates(self, usage: Usage) -> Pricing
```

Tarif qui s'applique à un appel : celui du palier le plus haut atteint, sinon le tarif de base.

**Arguments :**

- `usage` : Consommation de l'appel ; ses tokens d'entrée, cache compris, choisissent le palier.

**Renvoie :**

Les prix, chaque prix absent d'un palier repris du tarif de base.

### Méthode `loom_ia.core.model.usage.Pricing.cost`

```python
def cost(self, usage: Usage) -> float
```

Coût en dollars de la consommation d'un appel, au tarif de son palier.

**Arguments :**

- `usage` : Consommation de l'appel.

**Renvoie :**

Le coût : chaque catégorie de tokens à son prix, divisé par un
million. Les tokens de raisonnement ne sont pas comptés à part.

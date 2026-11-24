# Publication : loom-ia 2.0.27, loom-chat 2.0.27, Heddlo 1.0.1

Commencer par loom-ia et loom-chat : Heddlo a besoin de loom-ia 2.0.27 sur PyPI.

## 1. loom-ia et loom-chat (dépôt loom_v2)

Déjà prêt : `version = "2.0.27"` dans `pyproject.toml` et `loom-chat/pyproject.toml`, `loom-ia[anthropic,console]==2.0.27` dans loom-chat, et les deux `uv.lock` à jour.

1. `CHANGELOG.md` : remplacer `## 2.0.27 - non publiée` par `## 2.0.27 - publiée`.
2. Lancer la CI en local :
   ```bash
   uv run --locked ruff check . && uv run --locked ruff format --check . && uv run --locked pyright && uv run --locked lint-imports && uv run --locked pytest
   cd loom-chat && uv run --locked ruff check . && uv run --locked ruff format --check . && uv run --locked pyright && uv run --locked pytest
   ```
3. Commit, push sur `main`, attendre que la CI soit verte.
4. (Facultatif) Essai sur TestPyPI : Actions > Publication > Run workflow.
5. Poser et pousser l'étiquette :
   ```bash
   git tag -a v2.0.27 -m "loom-ia 2.0.27"
   git push origin v2.0.27
   ```
   Le workflow `Publication` relance la CI, vérifie que l'étiquette correspond aux deux versions, puis publie sur PyPI loom-ia et ensuite loom-chat. Valider l'environnement `pypi` s'il le demande.
6. Vérifier :
   ```bash
   uvx --from loom-ia==2.0.27 loom --version     # → loom 2.0.27
   uvx --from loom-chat==2.0.27 loom-chat --help
   ```

## 2. Heddlo 1.0.1 (dépôt heddlo)

Déjà prêt : `version = "1.0.1"` et `__version__ = "1.0.1"`.
Tant que `[tool.uv.sources]` reste dans `pyproject.toml`, la CI GitHub échoue, car elle n'a pas le chemin local.

1. `pyproject.toml` : supprimer le bloc `[tool.uv.sources]` et remplacer `loom-ia[console]>=2.0.26` par `loom-ia[console]>=2.0.27`.
2. `uv lock`, qui prend loom-ia 2.0.27 sur PyPI, puis `uv sync`.
3. `CHANGELOG.md` :
   - remplacer `## 1.0.1 - non publiée` par `## 1.0.1 - publiée` ;
   - remplacer la ligne « Dépendance : loom_v2 en source locale… » par « Dépendance : `loom-ia[console]` 2.0.27 ou plus. »
4. Lancer la CI en local, avec PostgreSQL démarré et les variables `HEDDLO_TEST_DATABASE_*` définies :
   ```bash
   uv run --locked ruff check . && uv run --locked ruff format --check . && uv run --locked pyright
   uv run --locked pytest -m "not integration" --cov=heddlo && uv run --locked pytest -m integration
   ```
5. Commit, push sur `main`, attendre que la CI soit verte, puis poser l'étiquette :
   ```bash
   git tag -a v1.0.1 -m "v1.0.1"
   git push origin v1.0.1
   ```
   Pas de publication sur PyPI : le paquet est privé.
6. Déployer : `heddlo migrate` (applique `011_requests_approvers.sql`), puis redémarrer `heddlo serve`.

## 3. Ensuite

- loom_v2 : ouvrir la 2.0.28. Mettre `version` à 2.0.28 dans les deux pyproject et `loom-ia==2.0.28` dans loom-chat, ajouter `## 2.0.28 - non publiée` dans le CHANGELOG, puis `uv lock`.


# SPDX-License-Identifier: Apache-2.0
"""Fabrique des clients de modèles : le champ ``sdk`` choisit l'adaptateur (#9).

Les SDK sont des extras, importés seulement quand un modèle les utilise. La
clé est lue dans la variable nommée par ``api_key_env`` ; elle n'apparaît
jamais dans les messages d'erreur ni dans les logs.
"""

import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

from loom_ia.core.model import ModelSpec
from loom_ia.core.ports import ModelClient

if TYPE_CHECKING:
    import httpx2

# Clé transmise aux serveurs sans authentification (vLLM, Ollama…).
NO_API_KEY: Final = "not-needed"


class ModelConfigError(ValueError):
    """Définition de modèle inutilisable en l'état (extra manquant, clé absente…)."""


def create_model_client(
    spec: ModelSpec,
    *,
    environ: Mapping[str, str] | None = None,
    http_client: httpx2.AsyncClient | None = None,
) -> ModelClient:
    """Client du modèle ``spec``.

    ``http_client`` remplace le client HTTP du SDK (tests, proxy).
    """
    if spec.sdk == "fake":
        from loom_ia.adapters.models.fake import FakeModel

        return FakeModel(spec)

    api_key = _api_key(spec, os.environ if environ is None else environ)
    if spec.sdk == "anthropic":
        try:
            from loom_ia.adapters.models.anthropic import AnthropicModel
        except ImportError as exc:
            raise _missing_extra(spec, "anthropic") from exc
        return AnthropicModel(spec, api_key=api_key, http_client=http_client)

    if spec.effective_api == "responses":
        try:
            from loom_ia.adapters.models.openai_responses import OpenAIResponsesModel
        except ImportError as exc:
            raise _missing_extra(spec, "openai") from exc
        return OpenAIResponsesModel(spec, api_key=api_key, http_client=http_client)
    try:
        from loom_ia.adapters.models.openai_chat import OpenAIChatModel
    except ImportError as exc:
        raise _missing_extra(spec, "openai") from exc
    return OpenAIChatModel(spec, api_key=api_key, http_client=http_client)


def _api_key(spec: ModelSpec, environ: Mapping[str, str]) -> str:
    if spec.api_key_env is None:
        return NO_API_KEY
    value = environ.get(spec.api_key_env, "").strip()
    if not value:
        raise ModelConfigError(
            f"Modèle {spec.id!r} : la variable d'environnement {spec.api_key_env} "
            "est absente ou vide"
        )
    return value


def _missing_extra(spec: ModelSpec, extra: str) -> ModelConfigError:
    return ModelConfigError(
        f"Modèle {spec.id!r} : le SDK {extra!r} n'est pas installé "
        f"(installer l'extra : loom-ia[{extra}])"
    )

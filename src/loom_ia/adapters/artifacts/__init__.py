# SPDX-License-Identifier: Apache-2.0
"""Adaptateurs du stockage d'artefacts : dossier local et mémoire (G2)."""

from loom_ia.adapters.artifacts.local import LocalArtifactStore
from loom_ia.adapters.artifacts.memory import InMemoryArtifactStore

__all__ = ["InMemoryArtifactStore", "LocalArtifactStore"]

"""Console de chat pour loom-ia : une page, des sessions, des runs, des demandes à l'utilisateur."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("loom-chat")
except PackageNotFoundError:  # lancé depuis les sources, sans installation du paquet
    __version__ = "0.0.0+sources"

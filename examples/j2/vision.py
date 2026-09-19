# SPDX-License-Identifier: Apache-2.0
"""Phase 2.3 : une image jointe à la demande, décrite par un rôle vision.

    uv run --extra mcp python examples/j2/vision.py                        # modèles simulés
    uv run --extra mcp python examples/j2/vision.py --image photo.jpg
    uv run --env-file .env --extra mcp --extra anthropic python examples/j2/vision.py --reel

Même agent et même config que ``client_mcp.py`` (``examples/j2/assistant/``),
avec une image en plus. Sans ``--image``, le script fabrique en mémoire un
PNG (un carré bleu sur fond blanc) : rien n'est écrit sur le disque, hormis
le journal et le stockage d'artefacts de l'exemple.

Ce qui se passe :

- l'image est validée (signature binaire, taille), rangée dans le stockage
  d'artefacts (``data/.artifacts/``, à côté du journal) et jointe à la
  demande en référence : le journal n'en garde que l'URI ;
- l'orchestrateur ne voit pas les images (pas de capacité ``vision``) : il en
  reçoit une mention, et le rôle ``decrire_image`` lui est proposé ;
- le rôle vision reçoit l'image, lue dans le stockage et envoyée en base64 à
  son modèle ; sans image jointe, ce rôle est masqué (``client_mcp.py``).

Avec les modèles simulés, les réponses sont écrites d'avance : elles
décrivent le carré bleu, quelle que soit l'image. ``--reel`` prend l'agent
``assistant_reel`` : MiniMax-M3 orchestre (clé dans ``M3_API_KEY``), Claude
Haiku 4.5 regarde l'image (clé dans ``ANTHROPIC_API_KEY``).
"""

import argparse
import asyncio
import struct
import sys
import zlib
from pathlib import Path

from loom_ia.access.api import Loom
from loom_ia.adapters.models import ModelConfigError
from loom_ia.adapters.stores import JsonlEventStore
from loom_ia.config import ConfigError, load_config
from loom_ia.core.events import (
    ArtifactStored,
    Event,
    ModelResponded,
    ToolCalled,
    ToolCompleted,
    ToolSourceUnavailable,
)
from loom_ia.core.model import DEFAULT_TENANT, Attachment, AttachmentError
from loom_ia.runtime import apply_logging

CONFIG = Path(__file__).parent / "assistant" / "loom.yaml"
QUESTION = (
    "Que montre la photo jointe ? Et au passage : quelle heure est-il à Paris, et "
    "combien d'heures y a-t-il entre le 1er septembre et le 31 décembre 2026 ?"
)


def carre_bleu(taille: int = 200, cote: int = 100) -> bytes:
    """PNG d'un carré bleu centré sur fond blanc, fabriqué en mémoire."""
    debut = (taille - cote) // 2
    blanc, bleu = b"\xff\xff\xff", b"\x1e\x50\xdc"
    lignes = bytearray()
    for y in range(taille):
        lignes.append(0)  # ligne sans filtre
        for x in range(taille):
            dedans = debut <= x < debut + cote and debut <= y < debut + cote
            lignes += bleu if dedans else blanc

    def bloc(nature: bytes, donnees: bytes) -> bytes:
        crc = zlib.crc32(nature + donnees) & 0xFFFFFFFF
        return struct.pack(">I", len(donnees)) + nature + donnees + struct.pack(">I", crc)

    entete = struct.pack(">IIBBBBB", taille, taille, 8, 2, 0, 0, 0)  # 8 bits, RVB
    return (
        b"\x89PNG\r\n\x1a\n"
        + bloc(b"IHDR", entete)
        + bloc(b"IDAT", zlib.compress(bytes(lignes)))
        + bloc(b"IEND", b"")
    )


def describe(event: Event) -> str | None:
    """Une ligne par étape marquante du run."""
    match event.payload:
        case ArtifactStored(origin=origin, name=name, media_type=media, size=size):
            return f"  fichier  {origin} {name or ''} ({media}, {size} octets)"
        case ModelResponded(model_id=model, usage=usage, cost_usd=cost):
            return (
                f"  modèle   [{event.role}] {model} · {usage.input_tokens}/"
                f"{usage.output_tokens} tokens · {cost:.5f} $"
            )
        case ToolSourceUnavailable(source=source, error=error, required=required):
            return f"  serveur  {source} indisponible{' (requis)' if required else ''} : {error}"
        case ToolCalled(tool_name=name, tool_kind=kind, arguments=arguments):
            return f"  appel    {name} ({kind}) {arguments}"
        case ToolCompleted(tool_name=name, output=output):
            shown = output.as_text.replace("\n", " ")[:80]
            return f"  résultat {name}{' (erreur)' if output.is_error else ''} : {shown}"
        case _:
            return None


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Image jointe et rôle vision")
    parser.add_argument("--reel", action="store_true", help="vrais modèles (MiniMax-M3, Haiku)")
    parser.add_argument("--image", type=Path, help="image à joindre (JPEG, PNG, GIF ou WebP)")
    parser.add_argument("--question", default=QUESTION)
    args = parser.parse_args(argv)
    agent = "assistant_reel" if args.reel else "assistant"

    try:
        image = (
            Attachment.from_path(args.image)
            if args.image
            else Attachment(data=carre_bleu(), name="carre_bleu.png")
        )
    except OSError as error:
        print(f"Image illisible : {error}", file=sys.stderr)
        return 2
    try:
        config = load_config(CONFIG)
        loom = Loom(config)
    except ConfigError as error:
        print(f"Configuration : {error}", file=sys.stderr)
        return 2
    apply_logging(config)

    async with loom:
        print(f"> {args.question}\n  [image jointe : {image.name}, {len(image.data)} octets]\n")
        try:
            result = await loom.run(agent, args.question, attachments=[image])
        except AttachmentError as error:
            print(f"Image refusée : {error}", file=sys.stderr)
            return 2
        except (ModelConfigError, ConfigError) as error:
            print(f"Configuration : {error}", file=sys.stderr)
            return 2
        events = await loom.events(result.run_id)

    print(result.text or result.error or "—")
    print("\nDéroulé :")
    for line in filter(None, map(describe, events)):
        print(line)
    print(
        f"\nStatut     : {result.status} · itérations : {result.iterations}"
        f" · coût total : {result.cost_usd:.5f} $"
    )
    for record in result.artifacts:
        print(f"Artefact   : {record.uri}")
    store = loom.store.inner
    if isinstance(store, JsonlEventStore):
        print(f"Journal    : {store.path(DEFAULT_TENANT, result.session_id)}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))

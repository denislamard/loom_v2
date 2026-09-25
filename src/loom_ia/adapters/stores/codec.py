# SPDX-License-Identifier: Apache-2.0
"""Forme d'une ligne de journal, en clair ou scellée (#30, §11.5).

Un store range un événement en texte JSON et le relit. Entre les deux, il
passe par un **codec** : en clair, c'est le JSON de l'événement, tel qu'il a
toujours été écrit ; scellé, c'est la même enveloppe — identifiants,
horodatage, type, catégorie, statut, agent, rôle, facettes, ``seq`` — avec la
**charge fermée** à la place de la charge.

Pourquoi ici, et pas dans un enrobage d'``EventStore`` comme le journal
notifiant : le port parle ``Event``, et un ``Event`` ne peut pas porter une
charge fermée. L'union des charges est discriminée par ``type``, et
l'enveloppe vérifie à chaque relecture que son type, sa catégorie, son
statut et ses facettes sont bien ceux de sa charge. Faire entrer une charge
fermée dans cette union obligerait tout lecteur de charge — moteur,
projections, rejeu, masquage — à la reconnaître et à la narrer. Le sceau vit
donc là où la sérialisation vit déjà : dans les stores, les seuls à voir du
texte.

**Ce que l'enveloppe garde en clair, elle le garde pour de bon.** Les
colonnes et les facettes ``jsonb`` des stores SQL sont remplies avec ces
champs : un journal scellé se filtre exactement comme un journal en clair —
par client, session, run, type, agent, outil, modèle —, et une page de
``GET /events`` ne coûte pas plus cher. Le contenu, lui, est dans la charge,
et une facette est par règle un champ qui n'en porte pas (5.4c).

**Ce que l'AAD couvre :** le journal (client, session), la position (``seq``)
et l'identité (``event_id``). Pas le type ni les facettes — l'enveloppe les
recopie de la charge et les vérifie à la relecture, si bien qu'une facette
changée sous un sceau ouvert est déjà une erreur de validation. Une charge
déplacée d'un journal à un autre, ou d'une position à une autre, ne se
verrait pas : c'est ce que l'AAD refuse.

**Une ligne en clair reste lisible par un codec scellant.** Sceller un
journal qui existe déjà ne le réécrit pas : l'ancien se relit tel quel, et
ce qui s'y ajoute est fermé. L'inverse n'est pas vrai, et c'est le principe :
sans la clé, ce qui a été fermé ne s'ouvre plus.
"""

import json
from base64 import b64decode, b64encode
from binascii import Error as Base64Error
from typing import Any, Final, Protocol, cast

from pydantic import AwareDatetime, ConfigDict, PositiveInt, ValidationError

from loom_ia.core.events import Event
from loom_ia.core.model import DomainModel, EventId, SessionId, TenantId
from loom_ia.core.ports import Cipher, JournalCorrupted, Keyring, MissingKey

# Champ qui distingue une charge fermée d'une charge en clair. Aucune charge
# durable ne porte un champ de ce nom, et un essai le vérifie : le jour où
# l'une en porterait un, une ligne en clair passerait pour un sceau.
SEALED: Final = "sealed"
# Préfixe de l'AAD : il dit de quelle version du sceau il s'agit, et sépare
# ces octets de tout autre usage de la même clé.
AAD_PREFIX: Final = b"loom.seal.v1"


class Seal(DomainModel):
    """Charge fermée, telle qu'elle est rangée à la place de la charge."""

    sealed: str
    key_id: str


class EventMark(DomainModel):
    """Ce qu'une ligne dit sans être ouverte : où elle est, quand, laquelle.

    Assez pour lister des sessions et connaître la longueur d'un journal —
    ce que font ``sessions()`` et ``last_seq()`` — sans la clé du client :
    un journal dont la clé est effacée se laisse encore **voir et
    supprimer**, ce qu'exige la suppression RGPD, sans rien livrer de son
    contenu.
    """

    # Une marque lit une partie d'une ligne : le reste n'est pas un champ
    # inconnu, c'est le reste.
    model_config = ConfigDict(frozen=True, extra="ignore")

    tenant_id: TenantId
    session_id: SessionId
    seq: PositiveInt
    event_id: EventId
    ts: AwareDatetime


class JournalCodec(Protocol):
    """Passage entre un événement et la ligne qui le range."""

    def dumps(self, event: Event) -> str: ...

    def loads(self, line: str | bytes) -> Event:
        """Événement de cette ligne ; ``MissingKey`` si son sceau reste fermé."""
        ...

    def mark(self, line: str | bytes) -> EventMark:
        """Repères de cette ligne, sans l'ouvrir."""
        ...


class PlainCodec:
    """Le JSON de l'événement, tel quel : le journal de toujours."""

    def dumps(self, event: Event) -> str:
        return event.model_dump_json()

    def loads(self, line: str | bytes) -> Event:
        return Event.model_validate_json(line)

    def mark(self, line: str | bytes) -> EventMark:
        return EventMark.model_validate_json(line)

    def __repr__(self) -> str:
        return "PlainCodec()"


PLAIN: Final = PlainCodec()


class SealingCodec:
    """Codec qui ferme la charge avec la clé du client, et l'ouvre avec."""

    def __init__(self, keyring: Keyring) -> None:
        self._keyring = keyring

    def dumps(self, event: Event) -> str:
        cipher = self._sealer(event.tenant_id)
        aad = aad_of(event.tenant_id, event.session_id, event.seq, event.event_id)
        closed = cipher.seal(event.payload.model_dump_json().encode(), aad)
        # Le JSON de l'événement, relu puis recomposé : l'enveloppe rangée est
        # exactement celle que Pydantic écrit, à la charge près.
        shell = cast("dict[str, Any]", json.loads(event.model_dump_json()))
        shell["payload"] = {SEALED: b64encode(closed).decode(), "key_id": cipher.key_id}
        return json.dumps(shell, ensure_ascii=False, separators=(",", ":"))

    def loads(self, line: str | bytes) -> Event:
        shell = _shell(line)
        payload: object = shell.get("payload")
        if not isinstance(payload, dict) or SEALED not in cast("dict[str, Any]", payload):
            # Ligne écrite avant que le sceau soit déclaré.
            return Event.model_validate(shell)
        seal = _seal(cast("dict[str, Any]", payload))
        mark = _mark(shell)
        cipher = self._opener(mark.tenant_id, seal.key_id)
        aad = aad_of(mark.tenant_id, mark.session_id, mark.seq, mark.event_id)
        shell["payload"] = _clear(cipher.unseal(_closed(seal), aad))
        return Event.model_validate(shell)

    def mark(self, line: str | bytes) -> EventMark:
        return _mark(_shell(line))

    def _sealer(self, tenant_id: TenantId) -> Cipher:
        return self._keyring.ciphers(tenant_id)[0]

    def _opener(self, tenant_id: TenantId, key_id: str) -> Cipher:
        try:
            ring = self._keyring.ciphers(tenant_id)
        except MissingKey as exc:
            # Le trousseau dit qu'il n'a rien pour ce client ; la ligne, elle,
            # sait quelle clé l'a fermée. Les deux ensemble disent laquelle
            # remettre — un opérateur qui garde plusieurs clés en a besoin.
            raise MissingKey(f"{exc} ; la charge à ouvrir est scellée par {key_id!r}") from exc
        for cipher in ring:
            if cipher.key_id == key_id:
                return cipher
        raise MissingKey(
            f"Client {tenant_id!r} : la clé {key_id!r} qui a scellé cette charge n'est pas "
            "dans son trousseau — effacée (le contenu est perdu, c'est l'effet voulu) ou "
            "pas encore déclarée"
        )

    def __repr__(self) -> str:
        return f"SealingCodec({self._keyring!r})"


def aad_of(tenant_id: str, session_id: str, seq: int, event_id: str) -> bytes:
    """Ce que le sceau authentifie sans le chiffrer : le journal et la place."""
    return b"\n".join(
        (AAD_PREFIX, tenant_id.encode(), session_id.encode(), str(seq).encode(), event_id.encode())
    )


def _shell(line: str | bytes) -> dict[str, Any]:
    try:
        parsed: object = json.loads(line)
    except ValueError as exc:
        raise JournalCorrupted(f"Ligne de journal illisible : {exc}") from exc
    if not isinstance(parsed, dict):
        raise JournalCorrupted("Ligne de journal illisible : objet JSON attendu")
    return cast("dict[str, Any]", parsed)


def _seal(payload: object) -> Seal:
    try:
        return Seal.model_validate(payload)
    except ValidationError as exc:
        raise JournalCorrupted(f"Charge scellée illisible : {exc}") from exc


def _mark(shell: dict[str, Any]) -> EventMark:
    try:
        return EventMark.model_validate(shell)
    except ValidationError as exc:
        raise JournalCorrupted(f"Enveloppe de journal illisible : {exc}") from exc


def _closed(seal: Seal) -> bytes:
    try:
        return b64decode(seal.sealed, validate=True)
    except (Base64Error, ValueError) as exc:
        raise JournalCorrupted(f"Charge scellée illisible : {exc}") from exc


def _clear(opened: bytes) -> object:
    try:
        return json.loads(opened)
    except ValueError as exc:  # sceau ouvert, contenu non-JSON : scellé ailleurs
        raise JournalCorrupted(f"Charge ouverte illisible : {exc}") from exc

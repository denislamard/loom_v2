# SPDX-License-Identifier: Apache-2.0
"""Expressions régulières à délai : un motif ne gèle plus la boucle d'événements.

Un motif mal écrit — ``(a|aa)+$`` — cherche pendant des secondes, parfois des
minutes, sur quelques dizaines de caractères ; la boucle d'événements attend
avec lui, donc tous les runs de l'hôte. Le module ``re`` ne sait pas s'arrêter.
Le module tiers ``regex`` (dépendance du noyau) le sait : ``timeout=``.

Tout motif qui ne vient pas du code de loom-ia passe par ce module :

- ``must_match`` et ``must_not_match`` d'un contrat de sortie ;
- les motifs de masquage de la télémétrie ;
- les mots-clés de JSON Schema qui cherchent un motif (``pattern``,
  ``patternProperties``, ``additionalProperties``, ``unevaluatedProperties``),
  pour les schémas qu'écrit le modèle (forge) ou que sa sortie doit respecter
  (contrat).

Un motif qui dépasse ``REGEX_TIMEOUT`` lève ``RegexTimeout`` : l'appelant fait
échouer le contrôle (ou masque tout le texte), il ne conclut jamais « absent »
ni « conforme » faute d'avoir pu finir. Le délai est celui d'une recherche, pas
d'une validation entière : un schéma qui porte plusieurs motifs lents peut en
cumuler plusieurs.

``regex`` accepte ce que ``re`` accepte (sa version 0, par défaut) et plus :
classes ``\\p{L}``, quantificateurs possessifs, groupes atomiques. Il va aussi
plus vite que ``re`` sur plusieurs motifs catastrophiques classiques, comme
``(a+)+$`` : un motif lent sous ``re`` ne l'est pas forcément sous ``regex``.
"""

import types
from collections.abc import Callable, Mapping
from functools import cache
from typing import Any, Final, cast

import regex
from jsonschema import (
    Draft202012Validator,
    FormatChecker,
    SchemaError,
    _keywords,  # pyright: ignore[reportPrivateUsage]
    _legacy_keywords,  # pyright: ignore[reportPrivateUsage]
    _utils,  # pyright: ignore[reportPrivateUsage]
    validators,
)
from jsonschema.protocols import Validator
from jsonschema.validators import validator_for
from jsonschema_specifications import (  # pyright: ignore[reportMissingTypeStubs]
    REGISTRY as _SPECIFICATIONS,
)
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

# Les méta-schémas que ``jsonschema`` sait résoudre (le registre qu'il utilise lui-même).
# ``jsonschema_specifications`` est une dépendance de ``jsonschema`` et ne livre pas de types.
SPECIFICATIONS: Final = cast("Registry[Any]", _SPECIFICATIONS)

# Durée maximale d'une recherche ou d'une substitution, en secondes. Un motif
# sain finit en millisecondes ; la marge couvre une machine chargée et un
# texte de plusieurs mégaoctets.
REGEX_TIMEOUT: Final = 1.0

# Ce que lève un motif invalide (la config le contrôle au chargement).
PatternError = regex.error


class RegexTimeout(Exception):
    """Un motif n'a pas fini sa recherche dans le délai : il est trop coûteux."""

    def __init__(self, pattern: str) -> None:
        super().__init__(f"motif trop coûteux : {pattern!r} (délai de {REGEX_TIMEOUT:g} s dépassé)")
        self.pattern = pattern


def compile_pattern(pattern: str) -> regex.Pattern[str]:
    """Motif compilé par le moteur de l'exécution ; ``PatternError`` s'il est invalide."""
    return regex.compile(pattern)


def search(pattern: str | regex.Pattern[str], text: str) -> regex.Match[str] | None:
    """Première occurrence du motif dans le texte ; ``RegexTimeout`` au-delà du délai."""
    compiled = regex.compile(pattern) if isinstance(pattern, str) else pattern
    try:
        return compiled.search(text, timeout=REGEX_TIMEOUT)
    except TimeoutError:
        raise RegexTimeout(compiled.pattern) from None


def substitute(pattern: regex.Pattern[str], replacement: str, text: str) -> str:
    """Texte dont chaque occurrence du motif est remplacée ; ``RegexTimeout`` au-delà du délai."""
    try:
        return pattern.sub(replacement, text, timeout=REGEX_TIMEOUT)
    except TimeoutError:
        raise RegexTimeout(pattern.pattern) from None


# ---------------------------------------------------------------------- #
# JSON Schema
# ---------------------------------------------------------------------- #


class _BoundedRe:
    """Tient lieu du module ``re`` dans les copies des fonctions de ``jsonschema``."""

    search = staticmethod(search)


def _function(module: types.ModuleType, name: str) -> Callable[..., Any]:
    """Une fonction de ``jsonschema`` (ses stubs ne typent pas les paramètres)."""
    return cast(Callable[..., Any], getattr(module, name))


def _bounded[F: Callable[..., Any]](function: F, **names: object) -> F:
    """Copie d'une fonction de ``jsonschema`` dont ``re.search`` passe par ``search``.

    ``jsonschema`` cherche ses motifs avec ``re.search``, dans des fonctions de
    ses modules privés : ``pattern``, ``patternProperties``,
    ``additionalProperties`` (par ``find_additional_properties``) et
    ``unevaluatedProperties`` (par ``find_evaluated_property_keys_by_schema``,
    récursive). La copie a le même code et le même espace de noms, sauf ``re`` ;
    ``names`` redirige vers d'autres copies les fonctions qu'elle appelle.
    """
    original = cast(types.FunctionType, function)
    namespace: dict[str, Any] = {**original.__globals__, "re": _BoundedRe, **names}
    clone = types.FunctionType(
        original.__code__, namespace, original.__name__, original.__defaults__, original.__closure__
    )
    # La récursion appelle la copie, pas l'original.
    namespace[original.__name__] = clone
    return cast(F, clone)


def _clone(
    module: types.ModuleType, name: str, **names: object
) -> tuple[Callable[..., Any], Callable[..., Any]]:
    """Une fonction de ``jsonschema`` et sa copie à délai."""
    original = _function(module, name)
    return original, _bounded(original, **names)


_find_additional = _bounded(_function(_utils, "find_additional_properties"))
_find_evaluated = _bounded(_function(_utils, "find_evaluated_property_keys_by_schema"))
_find_evaluated_2019 = _bounded(
    _function(_legacy_keywords, "find_evaluated_property_keys_by_schema")
)

# Chaque fonction de ``jsonschema`` qui cherche un motif, avec sa copie à délai.
_CLONES: Final[Mapping[Callable[..., Any], Callable[..., Any]]] = dict(
    [
        _clone(_keywords, "pattern"),
        _clone(_keywords, "patternProperties"),
        _clone(_keywords, "additionalProperties", find_additional_properties=_find_additional),
        _clone(
            _keywords,
            "unevaluatedProperties",
            find_evaluated_property_keys_by_schema=_find_evaluated,
        ),
        _clone(
            _legacy_keywords,
            "unevaluatedProperties_draft2019",
            find_evaluated_property_keys_by_schema=_find_evaluated_2019,
        ),
    ]
)


@cache
def _bounded_class(base: type[Validator]) -> type[Validator]:
    """La classe de validation ``base``, dont les mots-clés de motif ont un délai."""
    swapped = {
        word: _CLONES[function] for word, function in base.VALIDATORS.items() if function in _CLONES
    }
    extend = _function(validators, "extend")
    return cast(type[Validator], extend(base, validators=swapped))


def bounded_validator(schema: Mapping[str, Any]) -> Validator:
    """Validateur du schéma dont les motifs ont un délai : ``RegexTimeout`` s'il est dépassé.

    La classe suit ``$schema`` (2020-12 sinon), comme ``validator_for``. Le
    ``RegexTimeout`` sort tel quel de ``validate``, ``iter_errors`` et
    ``is_valid`` : une erreur de validation serait lue par ``not``, ``anyOf`` ou
    ``if`` comme « ne correspond pas », et ferait réussir un contrôle qui n'a
    pas eu lieu.
    """
    return _bounded_class(validator_for(schema, default=Draft202012Validator))(schema)


def check_schema(schema: Mapping[str, Any]) -> None:
    """Contrôle un schéma JSON ; ``SchemaError`` s'il est invalide.

    Comme ``check_schema`` de ``jsonschema``, mais le format ``regex`` se
    compile avec ``regex`` : un motif accepté ici s'exécute à la validation.
    Un ``$schema`` ailleurs qu'à la racine est refusé : ``jsonschema`` change de
    classe de validation à chaque sous-schéma qui en porte un, et reprendrait la
    classe d'origine, sans délai. Une ``$ref`` qui ne se résout pas est refusée
    aussi (``unresolved_references``).
    """
    if _nested_dialect(schema):
        raise SchemaError("« $schema » ne se déclare qu'à la racine du schéma")
    base = validator_for(schema, default=Draft202012Validator)
    meta = validator_for(base.META_SCHEMA, default=base)
    formats = FormatChecker(())
    formats.checkers = {**meta.FORMAT_CHECKER.checkers, "regex": (_is_regex, PatternError)}
    for error in meta(base.META_SCHEMA, format_checker=formats).iter_errors(schema):
        raise SchemaError.create_from(error)
    missing = unresolved_references(schema)
    if missing:
        raise SchemaError(f"« $ref » introuvable dans le schéma : {', '.join(map(repr, missing))}")


def unresolved_references(schema: Mapping[str, Any]) -> list[str]:
    """Les ``$ref`` du schéma qui ne se résolvent pas, dans l'ordre où ils apparaissent.

    Une référence se résout dans le schéma lui-même (pointeur, ancre, ``$id``
    d'un sous-schéma) ou dans les méta-schémas de ``jsonschema``. Rien ne va
    chercher un fichier ni une adresse : la validation d'une sortie ne doit pas
    sortir du process, et un ``$ref: autre.json`` ne se résoudrait pas non plus
    à l'exécution — il ferait échouer le contrôle de la réponse finale, une
    fois le modèle payé.
    """
    root = Resource.from_contents(schema, default_specification=DRAFT202012)
    missing: list[str] = []

    def walk(resolver: Any, node: Resource[Any]) -> None:
        resolver = resolver.in_subresource(node)
        contents = node.contents
        reference = (
            cast(Mapping[str, object], contents).get("$ref")
            if isinstance(contents, Mapping)
            else None
        )
        if isinstance(reference, str):
            try:
                resolver.lookup(reference)
            except Unresolvable:
                if reference not in missing:
                    missing.append(reference)
        for child in node.subresources():
            walk(resolver, child)

    walk(SPECIFICATIONS.resolver_with_root(root), root)
    return missing


def _is_regex(instance: object) -> bool:
    return not isinstance(instance, str) or bool(regex.compile(instance))


def _nested_dialect(schema: Mapping[str, Any]) -> bool:
    """Vrai si un sous-schéma (et non la racine) déclare un ``$schema``."""

    def walk(node: object) -> bool:
        if isinstance(node, dict):
            table = cast(dict[str, object], node)
            return isinstance(table.get("$schema"), str) or any(walk(v) for v in table.values())
        if isinstance(node, list):
            return any(walk(item) for item in cast(list[object], node))
        return False

    return any(walk(value) for value in schema.values())

# SPDX-License-Identifier: Apache-2.0
"""La source ``forge`` : contrôles de l'hôte, catalogue, outils, montage par loom.

Trois étages. Sans execd : un double de session (``FakeJobs``) rend des
comptes rendus écrits d'avance — rien n'est exécuté. Avec execd en socket
Unix (``firecracker/service/``) : le code forgé s'exécute pour de bon. Avec
execd et le faux firecracker : loom monte la source par son point d'entrée,
démarre la « VM » au premier appel et l'arrête avec l'instance.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError
from jsonschema.validators import validator_for

from loom_ia.access import Loom
from loom_ia.adapters.firecracker.forge import (
    CALL,
    FORGE,
    MANIFEST,
    SHOWN,
    Catalog,
    Forged,
    ForgeSource,
    Jobs,
    _same,  # pyright: ignore[reportPrivateUsage]
    check_forged,
    forge_source,
)
from loom_ia.adapters.firecracker.session import ExecdError, Execution, Hello, Output, Session
from loom_ia.adapters.firecracker.vm import VmError
from loom_ia.config import load_config
from loom_ia.core import bounded_regex
from loom_ia.core.events import ToolCompleted
from loom_ia.core.model import RunId, RunStatus, SessionId, TenantId
from loom_ia.core.ports import SourceContext, Tool, ToolContext, ToolError

type MakeVm = Callable[..., Path]

TENANT = TenantId("default")
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"n": {"type": "integer"}},
    "required": ["n"],
}
CODE = "def carres(n):\n    print('calcul', n)\n    return sum(i * i for i in range(n + 1))\n"
EXAMPLES: list[Any] = [{"arguments": {"n": 3}, "expected": 14}]


def forging(**changed: Any) -> dict[str, Any]:
    """Les arguments d'un appel à ``forge``, modifiables champ par champ."""
    return {
        "name": "carres",
        "description": "Somme des carrés de 0 à n.",
        "input_schema": SCHEMA,
        "code": CODE,
        "examples": EXAMPLES,
        **changed,
    }


def source_context(run: str = "r1") -> SourceContext:
    return SourceContext(tenant_id=TENANT, session_id=SessionId("s"), run_id=RunId(run), agent="a")


def tool_context(run: str = "r1", tenant: str = "default") -> ToolContext:
    return ToolContext(
        tenant_id=TenantId(tenant),
        session_id=SessionId("s"),
        run_id=RunId(run),
        call_id="c1",
        agent="a",
    )


def by_name(tools: list[Tool] | Any) -> dict[str, Tool]:
    return {tool.spec.name: tool for tool in tools}


HELLO = Hello(
    protocol=1,
    session_id="faux",
    python="3.12.3",
    runner="0.1.0",
    max_body=8 << 20,
    max_code_bytes=16 << 20,
    max_upload_total=1 << 31,
    ceilings={"wall_ms": 300_000, "mem_bytes": 805_306_368},
)


class FakeJobs:
    """Une session qui n'exécute rien : ``answer`` rend le compte rendu de chaque appel."""

    def __init__(self, answer: Callable[[str, Mapping[str, object]], Execution]) -> None:
        self.answer = answer
        self.puts: list[tuple[str, bytes]] = []
        self.calls: list[tuple[str, dict[str, object], Mapping[str, int] | None]] = []
        self.closed = False
        self.hello = HELLO

    async def put_code(self, path: str, data: bytes) -> None:
        self.puts.append((path, data))

    async def exec(
        self,
        entrypoint: str,
        *,
        args: Mapping[str, object] | None = None,
        limits: Mapping[str, int] | None = None,
        env: Mapping[str, str] | None = None,
        wait: float | None = None,
    ) -> Execution:
        self.calls.append((entrypoint, dict(args or {}), limits))
        return self.answer(entrypoint, args or {})

    async def close(self) -> None:
        self.closed = True


def fourteen(entrypoint: str, args: Mapping[str, object]) -> Execution:
    return Execution(ok=True, result=14)


class FakeRunner:
    """Des sessions ``FakeJobs`` ; ``fail`` fait échouer leur ouverture."""

    def __init__(
        self,
        answer: Callable[[str, Mapping[str, object]], Execution] | None = None,
        fail: Exception | None = None,
    ) -> None:
        self.answer = answer or fourteen
        self.fail = fail
        self.opened: list[FakeJobs] = []
        self.closed = 0

    async def session(self) -> Jobs:
        if self.fail is not None:
            raise self.fail
        jobs = FakeJobs(self.answer)
        self.opened.append(jobs)
        return jobs

    async def aclose(self) -> None:
        self.closed += 1


class UdsRunner:
    """Des sessions sur un execd réel en socket Unix (pas de VM)."""

    def __init__(self, socket: Path) -> None:
        self.socket = socket
        self.opened = 0

    async def session(self) -> Session:
        self.opened += 1
        reader, writer = await asyncio.open_unix_connection(self.socket)
        return await Session.open(reader, writer)

    async def aclose(self) -> None:
        pass


def make_source(
    tmp_path: Path, runner: Any, limits: Mapping[str, int] | None = None
) -> ForgeSource:
    return ForgeSource(
        name="forge", catalog=Catalog(tmp_path / "catalogue"), runner=runner, limits=limits or {}
    )


# ---------------------------------------------------------------------- #
# Contrôles de l'hôte
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("changed", "said"),
    [
        ({"name": "Carres"}, "identifiant Python en minuscules"),
        ({"name": "call"}, "nom réservé"),
        ({"name": "lambda"}, "nom réservé"),
        ({"name": "json", "code": "def json(n):\n    return n\n"}, "bibliothèque standard"),
        ({"input_schema": {"type": "objet"}}, "pas un schéma JSON valide"),
        ({"input_schema": {"type": "array"}}, '"type": "object" attendu'),
        ({"code": "def carres(n)\n    return n\n"}, "erreur de syntaxe ligne 1"),
        ({"code": "def autre(n):\n    return n\n"}, "pas de fonction carres()"),
        ({"code": "class A:\n    def carres(self, n):\n        return n\n"}, "pas de fonction"),
        ({"code": "def carres(n, /):\n    return n\n"}, "par nom"),
        ({"code": "def carres(*n):\n    return n\n"}, "par nom"),
        ({"code": "def carres():\n    return 1\n"}, "ne reçoit pas n"),
        ({"code": "def carres(n, m):\n    return n\n"}, "exige m"),
        ({"examples": [{"arguments": {"n": "trois"}, "expected": 1}]}, "exemple 1 : ses arguments"),
        ({"examples": [{"arguments": {"n": 1}}]}, r"exemple 1 : arguments \(objet\) et expected"),
        ({"examples": []}, r"de 1 à 10 exemples attendus, 0 reçu\(s\)"),
        ({"examples": EXAMPLES * 11}, r"11 reçu\(s\)"),
    ],
)
def test_the_host_refuses_what_it_can_see_without_running(
    changed: dict[str, Any], said: str
) -> None:
    arguments = forging(**changed)
    with pytest.raises(ToolError, match=said):
        check_forged(
            arguments["name"], arguments["input_schema"], arguments["code"], arguments["examples"]
        )


@pytest.mark.parametrize(
    "code",
    [
        "def carres(**arguments):\n    return 1\n",
        "def carres(n, m=2):\n    return n\n",
        "def carres(*, n, m=None):\n    return n\n",
        "async def carres(n):\n    return n\n",
        "def carres(n):\n    return 0\n\ndef carres(n):\n    return n\n",
    ],
)
def test_signatures_that_fit_the_schema_pass(code: str) -> None:
    pairs = check_forged("carres", SCHEMA, code, EXAMPLES)
    assert pairs == [({"n": 3}, 14)]


# Un motif qui ne finit pas sur ce texte (``regex`` y met plus de 2 s, ``re`` plus d'une).
SLOW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"s": {"type": "string", "pattern": "^(a|aa)+$"}},
    "required": ["s"],
}
SLOW_CODE = "def lent(s):\n    return len(s)\n"
SLOW_TEXT = "a" * 34 + "!"


@pytest.fixture
def short_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bounded_regex, "REGEX_TIMEOUT", 0.2)


@pytest.mark.usefixtures("short_timeout")
def test_a_pattern_that_does_not_finish_refuses_the_example() -> None:
    """Le schéma est celui du modèle : son motif ne gèle pas l'hôte, il refuse l'outil."""
    examples: list[Any] = [{"arguments": {"s": SLOW_TEXT}, "expected": 35}]
    with pytest.raises(
        ToolError, match=r"exemple 1 : input_schema ne peut pas être appliqué — motif"
    ):
        check_forged("lent", SLOW_SCHEMA, SLOW_CODE, examples)


def test_a_schema_that_redeclares_its_dialect_is_refused() -> None:
    """``jsonschema`` y reprendrait sa classe d'origine, sans délai sur les motifs."""
    inner = {"$schema": "https://json-schema.org/draft/2020-12/schema", "pattern": "^(a|aa)+$"}
    schema: dict[str, Any] = {"type": "object", "properties": {"s": inner}, "required": ["s"]}
    examples: list[Any] = [{"arguments": {"s": "a"}, "expected": 1}]
    with pytest.raises(ToolError, match=r"pas un schéma JSON valide.*qu'à la racine"):
        check_forged("lent", schema, SLOW_CODE, examples)


def test_a_pattern_of_the_regex_engine_is_accepted() -> None:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"s": {"pattern": r"^\p{Lu}+$"}},
        "required": ["s"],
    }
    examples: list[Any] = [{"arguments": {"s": "ÉCOLE"}, "expected": 5}]
    pairs = check_forged("lent", schema, SLOW_CODE, examples)
    assert pairs == [({"s": "ÉCOLE"}, 5)]


def test_json_values_compare_as_json() -> None:
    assert _same(2, 2.0)
    assert not _same(True, 1)
    assert not _same(0, False)
    assert _same({"a": [1, {"b": None}]}, {"a": [1.0, {"b": None}]})
    assert not _same({"a": 1}, {"a": 1, "b": 2})
    assert not _same([1, 2], [2, 1])
    assert not _same("1", 1)
    assert not _same(None, 0)


# ---------------------------------------------------------------------- #
# Catalogue
# ---------------------------------------------------------------------- #


def forged(name: str = "carres", code: str = CODE) -> Forged:
    return Forged(
        name=name,
        description="Somme des carrés.",
        input_schema=SCHEMA,
        code=code,
        examples=({"arguments": {"n": 3}, "expected": 14},),
        forged_by="r1",
        forged_at="2026-10-07T12:00:00+00:00",
    )


def test_the_catalog_keeps_one_folder_per_tool(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "catalogue")
    catalog.save("default", forged())
    folder = tmp_path / "catalogue" / "default" / "carres"
    assert sorted(p.name for p in folder.iterdir()) == ["carres.py", MANIFEST]
    assert (folder / "carres.py").read_text() == CODE
    manifest = json.loads((folder / MANIFEST).read_text())
    assert set(manifest) == {"description", "input_schema", "examples", "forged_by", "forged_at"}
    assert catalog.get("default", "carres") == forged()
    assert catalog.list("default") == [forged()]
    assert catalog.list("autre") == []


def test_a_tool_forged_again_is_replaced_whole(tmp_path: Path) -> None:
    catalog = Catalog(tmp_path / "catalogue")
    catalog.save("default", forged())
    catalog.save("default", forged(code="def carres(n):\n    return 0\n"))
    tenant = tmp_path / "catalogue" / "default"
    assert [p.name for p in tenant.iterdir()] == ["carres"]
    found = catalog.get("default", "carres")
    assert found is not None and found.code == "def carres(n):\n    return 0\n"


def test_unreadable_entries_are_left_out(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    catalog = Catalog(tmp_path / "catalogue")
    catalog.save("default", forged())
    tenant = tmp_path / "catalogue" / "default"
    (tenant / "sans_manifeste").mkdir()
    (tenant / "sans_manifeste" / "sans_manifeste.py").write_text("x = 1\n")
    (tenant / "casse").mkdir()
    (tenant / "casse" / "casse.py").write_text("x = 1\n")
    (tenant / "casse" / MANIFEST).write_text("{")
    (tenant / "Majuscule").mkdir()
    (tenant / ".cache").mkdir()
    with caplog.at_level(logging.WARNING):
        assert [f.name for f in catalog.list("default")] == ["carres"]
    said = caplog.text
    assert "sans_manifeste" in said and "casse" in said and "Majuscule" in said
    assert ".cache" not in said


def test_a_tenant_name_must_be_a_plain_folder(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="inutilisable"):
        Catalog(tmp_path).folder("../ailleurs")


# ---------------------------------------------------------------------- #
# La source, avec une session qui n'exécute rien
# ---------------------------------------------------------------------- #


async def test_opening_lists_the_tools_without_running_anything(tmp_path: Path) -> None:
    runner = FakeRunner()
    source = make_source(tmp_path, runner)
    Catalog(tmp_path / "catalogue").save("default", forged())
    Catalog(tmp_path / "catalogue").save("autre", forged("pour_autre"))
    async with source.open(source_context()) as tools:
        assert [tool.spec.name for tool in tools] == [FORGE, CALL, "carres"]
        assert by_name(tools)["carres"].spec.input_schema == SCHEMA
        assert by_name(tools)["carres"].spec.description == "Somme des carrés."
    assert runner.opened == []


async def test_a_run_sees_the_tools_forged_before_it_started(tmp_path: Path) -> None:
    source = make_source(tmp_path, FakeRunner())
    before = str(uuid.uuid7())
    await asyncio.sleep(0.01)
    Catalog(tmp_path / "catalogue").save(
        "default", replace(forged(), forged_at=datetime.now(UTC).isoformat())
    )
    await asyncio.sleep(0.01)
    after = str(uuid.uuid7())
    seen: dict[str, list[str]] = {}
    for label, run in (("avant", before), ("après", after), ("autre id", "run-1")):
        async with source.open(source_context(run)) as tools:
            seen[label] = [tool.spec.name for tool in tools]
    assert seen == {
        "avant": [FORGE, CALL],
        "après": [FORGE, CALL, "carres"],
        "autre id": [FORGE, CALL, "carres"],
    }


async def test_forging_runs_each_example_then_saves(tmp_path: Path) -> None:
    runner = FakeRunner()
    source = make_source(tmp_path, runner, {"wall_ms": 2000})
    examples = [{"arguments": {"n": 3}, "expected": 14}, {"arguments": {"n": 3}, "expected": 14.0}]
    async with source.open(source_context()) as tools:
        out = await by_name(tools)[FORGE].invoke(forging(examples=examples), tool_context())
    assert not out.is_error
    assert out.data == {"name": "carres", "examples": 2}
    [jobs] = runner.opened
    assert jobs.puts == [("carres.py", CODE.encode())]
    assert [(entry, args) for entry, args, _ in jobs.calls] == [("carres:carres", {"n": 3})] * 2
    assert jobs.calls[0][2] == {"wall_ms": 2000}
    assert jobs.closed
    saved = Catalog(tmp_path / "catalogue").get("default", "carres")
    assert saved is not None and saved.forged_by == "r1"


@pytest.mark.parametrize(
    ("answer", "said"),
    [
        (Execution(ok=True, result=15), 'exemple 1 {"n": 3} : attendu 14, obtenu 15'),
        (
            Execution(
                ok=False,
                error=ExecdError(
                    "tool_raised",
                    "valeur refusee",
                    {"type": "ValueError", "traceback": ['  File "carres.py", line 2']},
                ),
                stdout="avant",
            ),
            'Échec dans la VM (tool_raised) : valeur refusee\n  File "carres.py", line 2',
        ),
        (
            Execution(ok=False, error=ExecdError("import_error", "dependance absente : 'numpy'")),
            "que la bibliothèque standard",
        ),
    ],
)
async def test_a_failing_example_refuses_the_tool(
    tmp_path: Path, answer: Execution, said: str
) -> None:
    source = make_source(tmp_path, FakeRunner(lambda _e, _a: answer))
    async with source.open(source_context()) as tools:
        out = await by_name(tools)[FORGE].invoke(forging(), tool_context())
    assert out.is_error
    assert "Outil carres refusé" in out.as_text and said in out.as_text
    assert Catalog(tmp_path / "catalogue").get("default", "carres") is None


TEXT_HINT = "(`expected` est le texte JSON de la valeur rendue : donne la valeur elle-même)"


@pytest.mark.parametrize(
    ("result", "expected", "said"),
    [
        # Vu au run réel de MiniMax (08/10) : l'objet attendu donné en texte JSON…
        ({"total": 14.0}, '{"total": 14}', TEXT_HINT),
        # … puis encodé deux fois quand la fonction rend une chaîne.
        ('{"total": 14}', '"{\\"total\\": 14}"', TEXT_HINT),
        (14, "14", TEXT_HINT),
        ({"total": 14}, "quatorze", "(attendu une chaîne, obtenu un objet)"),
        ([14], {"total": 14}, "(attendu un objet, obtenu une liste)"),
        (True, 1, "(attendu un nombre, obtenu un booléen)"),
        (None, "14", "(attendu une chaîne, obtenu null)"),
        (15, "14", "(attendu une chaîne, obtenu un nombre)"),
    ],
)
async def test_a_mismatch_of_type_says_so(
    tmp_path: Path, result: Any, expected: Any, said: str
) -> None:
    source = make_source(tmp_path, FakeRunner(lambda _e, _a: Execution(ok=True, result=result)))
    example = {"arguments": {"n": 3}, "expected": expected}
    async with source.open(source_context()) as tools:
        out = await by_name(tools)[FORGE].invoke(forging(examples=[example]), tool_context())
    assert out.is_error
    assert f"obtenu {json.dumps(result, ensure_ascii=False)} {said}" in out.as_text


@pytest.mark.parametrize(
    ("result", "expected"),
    [(15, 14), ("quinze", "quatorze"), ({"total": 15}, {"total": 14}), ([15], [14])],
)
async def test_a_mismatch_of_value_alone_adds_nothing(
    tmp_path: Path, result: Any, expected: Any
) -> None:
    source = make_source(tmp_path, FakeRunner(lambda _e, _a: Execution(ok=True, result=result)))
    example = {"arguments": {"n": 3}, "expected": expected}
    async with source.open(source_context()) as tools:
        out = await by_name(tools)[FORGE].invoke(forging(examples=[example]), tool_context())
    assert out.is_error
    assert out.as_text.endswith(f"obtenu {json.dumps(result, ensure_ascii=False)}")


def fits(schema: Mapping[str, Any], instance: Mapping[str, Any]) -> bool:
    try:
        validator_for(schema, default=Draft202012Validator)(schema).validate(instance)
    except ValidationError:
        return False
    return True


async def test_schema_and_examples_may_come_as_json_text(tmp_path: Path) -> None:
    runner = FakeRunner()
    source = make_source(tmp_path, runner)
    as_text = forging(input_schema=json.dumps(SCHEMA), examples=json.dumps(EXAMPLES))
    async with source.open(source_context()) as tools:
        forge = by_name(tools)[FORGE]
        schema = forge.spec.input_schema
        assert fits(schema, as_text) and fits(schema, forging())
        assert not fits(schema, forging(input_schema=1))
        out = await forge.invoke(as_text, tool_context())
    assert not out.is_error
    [jobs] = runner.opened
    assert [(entry, args) for entry, args, _ in jobs.calls] == [("carres:carres", {"n": 3})]
    # Au catalogue, des objets : le texte n'était que le transport.
    manifest = json.loads((tmp_path / "catalogue" / "default" / "carres" / MANIFEST).read_text())
    assert manifest["input_schema"] == SCHEMA
    assert manifest["examples"] == EXAMPLES


@pytest.mark.parametrize(
    ("changed", "said"),
    [
        ({"input_schema": '{"type": "object"'}, "input_schema : texte JSON illisible — Expecting"),
        ({"input_schema": "[1]"}, "input_schema : un objet JSON est attendu"),
        ({"examples": "{}"}, "examples : une liste JSON est attendue"),
        ({"examples": "[NaN]"}, "examples : texte JSON illisible — NaN n'est pas une valeur JSON"),
        ({"examples": "[]"}, r"0 reçu\(s\)"),
        ({"examples": '[{"arguments": {"n": "trois"}, "expected": 1}]'}, "exemple 1 : ses arg"),
    ],
)
async def test_json_text_is_checked_like_objects(
    tmp_path: Path, changed: dict[str, Any], said: str
) -> None:
    runner = FakeRunner()
    source = make_source(tmp_path, runner)
    async with source.open(source_context()) as tools:
        with pytest.raises(ToolError, match=said):
            await by_name(tools)[FORGE].invoke(forging(**changed), tool_context())
    assert runner.opened == []


async def test_a_refusal_of_shape_suggests_json_text_for_what_came_as_objects(
    tmp_path: Path,
) -> None:
    source = make_source(tmp_path, FakeRunner())
    broken = {"type": "object", "required": {"n": ""}}
    hint = "(si ton format d'appel déforme les objets imbriqués, donne {} en texte JSON)"
    said: list[str] = []
    async with source.open(source_context()) as tools:
        forge = by_name(tools)[FORGE]
        for arguments in (
            forging(input_schema=broken),
            forging(input_schema=json.dumps(broken)),
            forging(input_schema=json.dumps(broken), examples=json.dumps(EXAMPLES)),
            forging(name="Carres", input_schema=broken),
            forging(input_schema={"type": "object"}),
        ):
            with pytest.raises(ToolError) as refused:
                await forge.invoke(arguments, tool_context())
            said.append(refused.value.message)
    assert said[0].startswith("input_schema n'est pas un schéma JSON valide")
    assert said[0].endswith(hint.format("input_schema et examples"))
    assert said[1].endswith(hint.format("examples"))
    assert "texte JSON" not in said[2] and said[2].startswith("input_schema n'est pas")
    # Un refus qui ne tient pas à la forme ne suggère rien.
    assert "identifiant Python" in said[3] and "texte JSON" not in said[3]
    # L'accord du code avec le schéma est une affaire de forme : required perdu en route.
    assert said[4].startswith("code : carres() exige n, que input_schema ne rend pas")
    assert said[4].endswith(hint.format("input_schema et examples"))


async def test_a_refusal_of_the_host_opens_no_session(tmp_path: Path) -> None:
    runner = FakeRunner()
    source = make_source(tmp_path, runner)
    async with source.open(source_context()) as tools:
        with pytest.raises(ToolError, match="pas de fonction"):
            await by_name(tools)[FORGE].invoke(forging(code="x = 1\n"), tool_context())
    assert runner.opened == []


async def test_call_reads_the_catalog_at_call_time(tmp_path: Path) -> None:
    runner = FakeRunner(lambda _e, args: Execution(ok=True, result={"n": args["n"]}))
    source = make_source(tmp_path, runner)
    async with source.open(source_context()) as tools:
        call = by_name(tools)[CALL]
        with pytest.raises(ToolError, match="'carres' inconnu ; au catalogue : aucun"):
            await call.invoke({"name": "carres", "arguments": {"n": 1}}, tool_context())
        Catalog(tmp_path / "catalogue").save("default", forged())
        with pytest.raises(ToolError, match="refusés par le schéma de carres"):
            await call.invoke({"name": "carres", "arguments": {"n": "un"}}, tool_context())
        out = await call.invoke({"name": "carres", "arguments": {"n": 4}}, tool_context())
    assert not out.is_error
    assert out.data == {"n": 4}
    assert out.as_text == '{"n": 4}'


@pytest.mark.usefixtures("short_timeout")
async def test_call_refuses_arguments_whose_pattern_does_not_finish(tmp_path: Path) -> None:
    runner = FakeRunner()
    source = make_source(tmp_path, runner)
    slow = Forged(
        name="lent",
        description="Longueur d'un mot.",
        input_schema=SLOW_SCHEMA,
        code=SLOW_CODE,
        examples=({"arguments": {"s": "a"}, "expected": 1},),
        forged_by="r1",
        forged_at="2026-10-07T12:00:00+00:00",
    )
    Catalog(tmp_path / "catalogue").save("default", slow)
    async with source.open(source_context()) as tools:
        call = by_name(tools)[CALL]
        with pytest.raises(ToolError, match="arguments refusés par le schéma de lent : motif trop"):
            await call.invoke({"name": "lent", "arguments": {"s": SLOW_TEXT}}, tool_context())
    assert runner.opened == []


async def test_one_session_per_run_and_the_code_sent_once(tmp_path: Path) -> None:
    runner = FakeRunner()
    source = make_source(tmp_path, runner)
    Catalog(tmp_path / "catalogue").save("default", forged())
    async with source.open(source_context()) as tools:
        tool = by_name(tools)["carres"]
        for n in (1, 2, 3):
            assert not (await tool.invoke({"n": n}, tool_context())).is_error
        [jobs] = runner.opened
        jobs.closed = True  # session perdue en route : la suivante est rouverte
        assert not (await tool.invoke({"n": 4}, tool_context())).is_error
    assert len(runner.opened) == 2
    assert [len(jobs.puts) for jobs in runner.opened] == [1, 1]
    assert [len(jobs.calls) for jobs in runner.opened] == [3, 1]
    async with source.open(source_context("r2")) as tools:
        assert not (await by_name(tools)["carres"].invoke({"n": 1}, tool_context())).is_error
    assert len(runner.opened) == 3


async def test_an_unavailable_vm_is_an_error_for_the_model(tmp_path: Path) -> None:
    source = make_source(tmp_path, FakeRunner(fail=VmError("temp : API muette après 60 s")))
    Catalog(tmp_path / "catalogue").save("default", forged())
    async with source.open(source_context()) as tools:
        out = await by_name(tools)["carres"].invoke({"n": 1}, tool_context())
    assert out.is_error
    assert out.as_text == "VM indisponible : VmError: temp : API muette après 60 s"


async def test_limits_over_the_ceilings_are_refused(tmp_path: Path) -> None:
    runner = FakeRunner()
    source = make_source(tmp_path, runner, {"wall_ms": 400_000})
    Catalog(tmp_path / "catalogue").save("default", forged())
    async with source.open(source_context()) as tools:
        with pytest.raises(ToolError, match=r"wall_ms = 400000 \(plafond 300000\)"):
            await by_name(tools)["carres"].invoke({"n": 1}, tool_context())
    [jobs] = runner.opened
    assert jobs.closed and jobs.calls == []


async def test_what_the_model_sees_is_bounded(tmp_path: Path) -> None:
    long = "x" * (SHOWN + 500)
    answer = Execution(
        ok=True,
        result=[1, 2],
        stdout=long,
        stderr="attention",
        stdout_truncated=True,
        outputs=(Output("graphe.png", 2048, "0" * 64),),
    )
    source = make_source(tmp_path, FakeRunner(lambda _e, _a: answer))
    Catalog(tmp_path / "catalogue").save("default", forged())
    async with source.open(source_context()) as tools:
        out = await by_name(tools)["carres"].invoke({"n": 1}, tool_context())
    text = out.as_text
    assert text.startswith("[1, 2]\n\nstdout (tronqué par la VM) :\n")
    assert "[… 500 caractères omis …]" in text
    assert len(text) < SHOWN + 300
    assert "stderr :\nattention" in text
    assert "fichiers produits, non rapatriés : graphe.png (2048 o)" in text
    assert out.data == [1, 2]


def test_loom_waits_as_long_as_execd(tmp_path: Path) -> None:
    source = make_source(tmp_path, FakeRunner(), {"wall_ms": 10_000})
    Catalog(tmp_path / "catalogue").save("default", forged())

    async def specs() -> dict[str, float | None]:
        async with source.open(source_context()) as tools:
            return {tool.spec.name: tool.spec.timeout for tool in tools}

    timeouts = asyncio.run(specs())
    # Boot (60 s), le job (10 s + 5), la marge (30 s) ; dix exemples pour forge.
    assert timeouts == {FORGE: 240.0, CALL: 105.0, "carres": 105.0}


# ---------------------------------------------------------------------- #
# La fabrique
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("params", "said"),
    [
        ({"catalog_dir": "c"}, "'vm_dir' : un chemin est attendu"),
        ({"vm_dir": "vm"}, "'catalog_dir' : un chemin est attendu"),
        ({"vm_dir": "absente", "catalog_dir": "c"}, "'vm_dir' : .*introuvable"),
        ({"vm_dir": "vm", "catalog_dir": "c", "autre": 1}, "params inconnus : autre"),
        ({"vm_dir": "vm", "catalog_dir": "c", "limits": {"duree": 1}}, "'limits.duree' inconnue"),
        ({"vm_dir": "vm", "catalog_dir": "c", "limits": {"wall_ms": True}}, "entier positif"),
        ({"vm_dir": "vm", "catalog_dir": "c", "limits": {"wall_ms": 0}}, "entier positif"),
        ({"vm_dir": "vm", "catalog_dir": "fichier"}, "n'est pas un dossier"),
    ],
)
def test_the_factory_checks_its_params(make_vm: MakeVm, params: dict[str, Any], said: str) -> None:
    base = make_vm().parent
    (base / "fichier").write_text("")
    with pytest.raises(ValueError, match=said):
        forge_source(name="forge", params=params, secrets={}, base_dir=base)


def test_the_factory_resolves_paths_and_starts_nothing(make_vm: MakeVm) -> None:
    vm_dir = make_vm()
    base = vm_dir.parent
    source = forge_source(
        name="forge",
        params={"vm_dir": "vm", "catalog_dir": "catalogue", "limits": {"wall_ms": 5000}},
        secrets={},
        base_dir=base,
    )
    assert source.catalog.root == base / "catalogue"
    assert source.limits == {"wall_ms": 5000}
    assert not (base / "catalogue").exists()
    assert not (vm_dir / "runtime").exists()


# ---------------------------------------------------------------------- #
# Avec execd : le code forgé s'exécute
# ---------------------------------------------------------------------- #


async def test_forged_for_real_then_called_both_ways(tmp_path: Path, execd: Path) -> None:
    runner = UdsRunner(execd)
    source = make_source(tmp_path, runner, {"wall_ms": 5000})
    async with source.open(source_context()) as tools:
        out = await by_name(tools)[FORGE].invoke(forging(), tool_context())
        assert not out.is_error, out.as_text
        called = await by_name(tools)[CALL].invoke(
            {"name": "carres", "arguments": {"n": 10}}, tool_context()
        )
    assert called.data == 385
    assert called.as_text == "385\n\nstdout :\ncalcul 10\n"
    async with source.open(source_context("r2")) as tools:
        out = await by_name(tools)["carres"].invoke({"n": 4}, tool_context("r2"))
    assert out.data == 30
    assert runner.opened == 2


async def test_real_refusals_say_what_went_wrong(tmp_path: Path, execd: Path) -> None:
    source = make_source(tmp_path, UdsRunner(execd), {"wall_ms": 2000})
    cases = {
        "attendu 15, obtenu 14": forging(examples=[{"arguments": {"n": 3}, "expected": 15}]),
        "dependance absente : 'numpy'": forging(code="import numpy\n" + CODE),
        'File "carres.py", line 2, in carres': forging(
            code="def carres(n):\n    raise ValueError('non')\n"
        ),
        "bad_result": forging(code="def carres(n):\n    return {1, 2}\n"),
        "timeout": forging(code="import time\ndef carres(n):\n    time.sleep(5)\n"),
    }
    async with source.open(source_context()) as tools:
        for said, arguments in cases.items():
            out = await by_name(tools)[FORGE].invoke(arguments, tool_context())
            assert out.is_error and said in out.as_text, out.as_text
    assert Catalog(tmp_path / "catalogue").list("default") == []


# ---------------------------------------------------------------------- #
# Montée par loom : point d'entrée, préfixe, VM démarrée et arrêtée
# ---------------------------------------------------------------------- #

FORGE_CALL = {
    "name": "forge__forge",
    "arguments": forging(),
}
CALL_CALL = {"name": "forge__call", "arguments": {"name": "carres", "arguments": {"n": 10}}}
USE_CALL = {"name": "forge__carres", "arguments": {"n": 4}}


def config_file(base: Path, vm_dir: Path) -> Path:
    (base / "agents").mkdir(parents=True)
    script = [
        {"with_text": "Forge", "text": "Je forge.", "tool_calls": [FORGE_CALL]},
        {"with_text": "Forge", "text": "Je l'appelle.", "tool_calls": [CALL_CALL]},
        {"with_text": "Forge", "text": "Fait."},
        {"with_text": "Utilise", "text": "Je l'utilise.", "tool_calls": [USE_CALL]},
        {"with_text": "Utilise", "text": "30."},
    ]
    config = {
        "version": 1,
        "models": [{"id": "FAKE", "sdk": "fake", "model": "fake-1", "params": {"script": script}}],
        "storage": {"events": {"backend": "jsonl", "path": "data"}},
        "tool_sources": [
            {
                "name": "forge",
                "entry_point": "forge",
                "params": {
                    "vm_dir": str(vm_dir),
                    "catalog_dir": "catalogue",
                    "limits": {"wall_ms": 5000},
                },
            }
        ],
    }
    (base / "loom.yaml").write_text(yaml.safe_dump(config, allow_unicode=True))
    agent = {
        "name": "atelier",
        "main": {"model": "FAKE", "system": "Tu forges."},
        "max_iterations": 5,
        "tools": [{"source": "forge"}],
    }
    (base / "agents" / "atelier.yaml").write_text(yaml.safe_dump(agent))
    return base / "loom.yaml"


async def test_loom_mounts_the_source_and_the_vm_lives_with_it(
    tmp_path: Path, make_vm: MakeVm, execd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAUX_EXECD", str(execd))
    vm_dir = make_vm()
    from loom_ia.adapters.firecracker import Vm

    vm = Vm.load(vm_dir)
    path = config_file(tmp_path / "config", vm_dir)
    async with Loom(load_config(path)) as loom:
        assert not vm.is_running()
        first = await loom.run("atelier", "Forge un outil.")
        assert first.status == RunStatus.COMPLETED
        assert vm.is_running()
        second = await loom.run("atelier", "Utilise-le.")
        assert second.status == RunStatus.COMPLETED
        events = [*await loom.events(first.run_id), *await loom.events(second.run_id)]
    assert not vm.is_running()
    completed = [e.payload for e in events if isinstance(e.payload, ToolCompleted)]
    assert [c.tool_name for c in completed] == ["forge__forge", "forge__call", "forge__carres"]
    assert [c.output.data for c in completed][1:] == [385, 30]
    assert (tmp_path / "config" / "catalogue" / "default" / "carres" / "carres.py").is_file()


async def test_a_vm_already_running_is_left_running(
    tmp_path: Path, make_vm: MakeVm, execd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAUX_EXECD", str(execd))
    vm_dir = make_vm()
    from loom_ia.adapters.firecracker import Vm

    vm = Vm.load(vm_dir)
    await vm.start(wait=10)
    try:
        path = config_file(tmp_path / "config", vm_dir)
        async with Loom(load_config(path)) as loom:
            first = await loom.run("atelier", "Forge un outil.")
            assert first.status == RunStatus.COMPLETED
        assert vm.is_running()
    finally:
        await vm.stop(grace=5, wait=5)


async def test_validate_and_replay_start_no_vm(
    tmp_path: Path, make_vm: MakeVm, execd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAUX_EXECD", str(execd))
    vm_dir = make_vm()
    from loom_ia.adapters.firecracker import Vm

    vm = Vm.load(vm_dir)
    path = config_file(tmp_path / "config", vm_dir)
    async with Loom(load_config(path)) as loom:
        first = await loom.run("atelier", "Forge un outil.")
    assert not vm.is_running()
    assert (tmp_path / "config" / "catalogue" / "default" / "carres").is_dir()
    async with Loom(load_config(path)) as loom:
        # Le catalogue a changé depuis le run (carres y est) : le rejeu voit
        # pourtant les outils du run, et n'exécute rien.
        report = await loom.replay(first.run_id)
        assert not vm.is_running()
    assert report.identical, report.divergence
    assert report.tool_calls == (2, 2)
    assert not vm.is_running()

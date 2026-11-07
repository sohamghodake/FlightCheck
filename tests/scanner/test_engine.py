"""Rule engine: registry, crash isolation, ordering, filtering, provider gating, downgrade."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from flightcheck.core.config import FlightCheckConfig
from flightcheck.core.errors import ConfigError
from flightcheck.core.models import Confidence, Finding, ScanRequest, Severity
from flightcheck.orchestrator import run_scan
from flightcheck.scanner.detect import detect
from flightcheck.scanner.engine import (
    RuleContext,
    RuleMeta,
    load_builtin_rules,
    rule,
    run_rules,
    select_rules,
    sort_key,
)
from flightcheck.scanner.parser import RepoIndex, build_index, parse_source

# Phase 3 engine tests check BharatCode behaviour only; FC-GEN/FC-SEC rules also fire on these
# tmp files (no try/except, no timeout), so the tests narrow to the BC rules.
BC_IDS = [f"FC-BC-00{n}" for n in range(1, 9)]

BC_CLIENT = (
    "import os\n"
    "from openai import OpenAI\n"
    'client = OpenAI(base_url="https://bharatcode.ai/api/model/v1")\n'
)


def _meta(rule_id: str, providers: frozenset[str] = frozenset({"*"})) -> RuleMeta:
    return RuleMeta(
        id=rule_id,
        title="t",
        category="robustness",
        default_severity=Severity.HIGH,
        default_confidence=Confidence.HIGH,
        providers=providers,
        doc_source=None,
        remediation="fix it",
        priority="P0",
    )


def _ctx(root: Path, mode: str = "auto") -> tuple[RepoIndex, RuleContext]:
    index = build_index(root)
    return index, RuleContext(index=index, detection=detect(index), bharatcode_mode=mode)


def _write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text, encoding="utf-8")


# --- registry -------------------------------------------------------------------------------


def test_duplicate_rule_id_fails() -> None:
    registry: dict = {}
    rule(_meta("X-001"), registry)(lambda index, ctx: [])
    with pytest.raises(RuntimeError, match="duplicate rule id X-001"):
        rule(_meta("X-001"), registry)(lambda index, ctx: [])


def test_builtin_registry_has_bc_rules_with_doc_source() -> None:
    registry = load_builtin_rules()
    for rule_id, (meta, _) in registry.items():
        if not rule_id.startswith("FC-BC-"):
            continue
        assert meta.id == rule_id
        assert meta.doc_source == "https://bharatcode.ai/build"
        assert meta.remediation
        assert meta.providers == frozenset({"bharatcode"})


def test_builtin_gen_and_sec_rules_run_on_every_provider() -> None:
    registry = load_builtin_rules()
    others = {rule_id: meta for rule_id, (meta, _) in registry.items() if rule_id[:6] != "FC-BC-"}
    assert others
    for rule_id, meta in others.items():
        assert meta.id == rule_id and meta.remediation
        assert meta.providers == frozenset({"*"})
        assert meta.category == ("security" if rule_id.startswith("FC-SEC-") else "robustness")


def test_unknown_rule_id_lists_valid_ids() -> None:
    with pytest.raises(ConfigError) as info:
        select_rules(["FC-BC-999"], load_builtin_rules())
    assert info.value.exit_code == 2
    assert "FC-BC-999" in info.value.message and "FC-BC-001" in info.value.message


def test_rule_ids_are_case_insensitive_and_keep_registry_order() -> None:
    assert select_rules(["fc-bc-003", "FC-BC-001"], load_builtin_rules()) == [
        "FC-BC-001",
        "FC-BC-003",
    ]


# --- execution ------------------------------------------------------------------------------


def test_crashing_rule_is_isolated(tmp_path: Path) -> None:
    _write(tmp_path, "app.py", BC_CLIENT + 'client.responses.create(model="m", input="x")\n')
    index, ctx = _ctx(tmp_path)
    registry: dict = {}

    @rule(_meta("X-CRASH"), registry)
    def crash(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
        raise ValueError("boom")
        yield  # pragma: no cover

    @rule(_meta("X-OK"), registry)
    def ok(index: RepoIndex, ctx: RuleContext) -> Iterator[Finding]:
        for call in ctx.call_sites_for(_meta("X-OK")):
            yield ctx.finding(_meta("X-OK"), call, "seen")

    findings = run_rules(index, ctx, registry=registry)
    assert [f.rule_id for f in findings] == ["X-OK"]
    assert [(d.level, d.code) for d in ctx.diagnostics] == [("error", "rule_crashed")]
    assert "X-CRASH" in ctx.diagnostics[0].message


def test_findings_sorted_by_path_line_rule(tmp_path: Path) -> None:
    calls = 'client.responses.create(model="gpt-4o", input="x", store=True)\n'
    _write(tmp_path, "b.py", BC_CLIENT + calls)
    _write(tmp_path, "a.py", BC_CLIENT + "\n" + calls + calls)
    index, ctx = _ctx(tmp_path)
    findings = run_rules(index, ctx, rule_ids=BC_IDS)
    keys = [(f.location.path, f.location.line, f.rule_id) for f in findings]
    assert keys == sorted(keys)
    assert keys[0] == ("a.py", 5, "FC-BC-001")
    assert {k[2] for k in keys} == {"FC-BC-001", "FC-BC-006"}
    assert findings == sorted(findings, key=sort_key)


def test_min_severity_and_rule_filter(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", BC_CLIENT + 'client.responses.create(model="gpt-4o", input="x")\n')
    index, ctx = _ctx(tmp_path)
    assert {f.rule_id for f in run_rules(index, ctx, rule_ids=BC_IDS)} == {"FC-BC-006", "FC-BC-007"}
    medium = run_rules(index, ctx, rule_ids=BC_IDS, min_severity=Severity.MEDIUM)
    assert [f.rule_id for f in medium] == ["FC-BC-006"]
    only = run_rules(index, ctx, rule_ids=["FC-BC-007"])
    assert [f.rule_id for f in only] == ["FC-BC-007"]


def test_finding_ids_are_stable(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", BC_CLIENT + 'client.responses.create(model="m", input="x")\n')
    first = run_rules(*_ctx(tmp_path), rule_ids=BC_IDS)
    second = run_rules(*_ctx(tmp_path), rule_ids=BC_IDS)
    assert [f.id for f in first] == [f.id for f in second]
    assert first[0].id == Finding.make_id("FC-BC-006", "a.py", 4, 0)


# --- provider gating and confidence downgrade -----------------------------------------------


OTHER = (
    "from openai import OpenAI\n"
    'client = OpenAI(base_url="https://api.example.com/v1")\n'
    'client.responses.create(model="gpt-4o", input="x", store=True)\n'
)


def test_bc_rules_skip_non_bharatcode_call_sites(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", OTHER)
    assert run_rules(*_ctx(tmp_path), rule_ids=BC_IDS) == []


def test_forced_bharatcode_runs_everywhere_without_downgrade(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", OTHER)
    findings = run_rules(*_ctx(tmp_path, "forced"), rule_ids=BC_IDS)
    assert {f.rule_id for f in findings} == {"FC-BC-001", "FC-BC-006"}


def test_disabled_bharatcode_runs_nothing(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", BC_CLIENT + 'client.responses.create(model="m", store=True)\n')
    assert run_rules(*_ctx(tmp_path, "disabled"), rule_ids=BC_IDS) == []


def test_low_provider_confidence_downgrades_one_level(tmp_path: Path) -> None:
    _write(tmp_path, "clients.py", BC_CLIENT)
    _write(
        tmp_path,
        "app.py",
        "def ask(client, model):\n"
        "    return client.responses.create(model=model, input='x', store=True)\n",
    )
    index, ctx = _ctx(tmp_path)
    call = next(c for c in index.call_sites if c.location.path == "app.py")
    assert ctx.provider_of(call).confidence is Confidence.LOW
    findings = run_rules(index, ctx, rule_ids=BC_IDS)
    assert [(f.rule_id, f.confidence) for f in findings] == [("FC-BC-001", Confidence.MEDIUM)]


def test_provider_override_via_run_scan(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", OTHER)
    config = FlightCheckConfig()
    forced = run_scan(
        ScanRequest(root=tmp_path, rule_ids=BC_IDS, provider_override="bharatcode"), config
    )
    assert {f.rule_id for f in forced.findings} == {"FC-BC-001", "FC-BC-006"}
    _write(tmp_path, "a.py", BC_CLIENT + 'client.responses.create(model="m", store=True)\n')
    off = run_scan(
        ScanRequest(root=tmp_path, rule_ids=BC_IDS, provider_override="openai_compatible"), config
    )
    assert off.findings == []
    with pytest.raises(ConfigError):
        run_scan(ScanRequest(root=tmp_path, rule_ids=BC_IDS, provider_override="nope"), config)


def test_run_scan_missing_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Path not found: .* pass a directory containing"):
        run_scan(ScanRequest(root=tmp_path / "missing"), FlightCheckConfig())


# --- parser one-hop literal lookup (additive Phase 3 change) --------------------------------


def test_one_hop_module_and_function_literals() -> None:
    index = parse_source(
        BC_CLIENT + "TOOLS = [{'type': 'web_search'}]\n"
        "def f():\n"
        "    items = [{'type': 'input_file'}]\n"
        "    client.responses.create(model='m', tools=TOOLS, input=items, store=False)\n",
        "a.py",
    )
    (call,) = index.call_sites
    assert call.one_hop == {"tools": [{"type": "web_search"}], "input": [{"type": "input_file"}]}
    assert type(call.params["tools"]).__name__ == "Unknown"


def test_one_hop_ignores_non_literals_and_rebinding() -> None:
    index = parse_source(
        BC_CLIENT + "TOOLS = [{'type': 'web_search'}]\n"
        "TOOLS = make()\n"
        "client.responses.create(model='m', tools=TOOLS, store=False)\n",
        "a.py",
    )
    assert index.call_sites[0].one_hop == {}


def test_one_hop_raw_post_json_keys() -> None:
    index = parse_source(
        "import httpx\n"
        "TOOLS = [{'type': 'mcp'}]\n"
        "httpx.post('https://bharatcode.ai/api/model/v1/responses',"
        " json={'model': 'm', 'tools': TOOLS})\n",
        "a.py",
    )
    assert index.call_sites[0].one_hop == {"tools": [{"type": "mcp"}]}


SHADOW_HEAD = BC_CLIENT + "TOOLS = [{'type': 'web_search'}]\n"
SHADOW_CALL = "    client.responses.create(model='m', tools=TOOLS, store=False)\n"


@pytest.mark.parametrize(
    "body",
    [
        "def f():\n    TOOLS = make()\n" + SHADOW_CALL,
        "def f(TOOLS):\n" + SHADOW_CALL,
        "def f(*TOOLS):\n" + SHADOW_CALL,
        "def f(**TOOLS):\n" + SHADOW_CALL,
        "def f():\n    TOOLS, other = make()\n" + SHADOW_CALL,
        "def f():\n    for TOOLS in make():\n    " + SHADOW_CALL,
        "def f():\n    with make() as TOOLS:\n    " + SHADOW_CALL,
        "def f():\n    TOOLS = []\n    TOOLS += make()\n" + SHADOW_CALL,
    ],
    ids=["reassigned", "param", "vararg", "kwarg", "tuple", "for", "with", "augassign"],
)
def test_one_hop_respects_local_shadowing(body: str) -> None:
    (call,) = parse_source(SHADOW_HEAD + body, "a.py").call_sites
    assert call.one_hop == {}


def test_one_hop_still_sees_outer_literals_when_not_shadowed() -> None:
    body = "def outer():\n    def inner(TOOLS):\n        pass\n" + SHADOW_CALL
    (call,) = parse_source(SHADOW_HEAD + body, "a.py").call_sites
    assert call.one_hop == {"tools": [{"type": "web_search"}]}


def test_shadowed_tools_produce_no_finding(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", SHADOW_HEAD + "def f(TOOLS):\n" + SHADOW_CALL)
    assert [f.rule_id for f in run_rules(*_ctx(tmp_path), rule_ids=BC_IDS)] == ["FC-BC-006"]


def test_base_url_constant_respects_local_shadowing() -> None:
    src = (
        "from openai import OpenAI\n"
        "BASE = 'https://bharatcode.ai/api/model/v1'\n"
        "def f(BASE):\n"
        "    return OpenAI(base_url=BASE)\n"
        "def g():\n"
        "    return OpenAI(base_url=BASE)\n"
    )
    shadowed, outer = parse_source(src, "a.py").clients
    assert shadowed.base_url_fragments == []
    assert outer.base_url_fragments == ["https://bharatcode.ai/api/model/v1"]

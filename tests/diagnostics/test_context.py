"""Context selection, snippets, contract excerpts (drift) and request construction."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from diag_support import FAKE_KEY, check, config, exchange, finding, report, scenario, service

from flightcheck.core.config import BHARATCODE_DEFAULT_MODEL
from flightcheck.core.models import CheckStatus, FindingKind, Severity
from flightcheck.diagnostics import contract_excerpts as ce
from flightcheck.diagnostics.context import (
    INSTANCES_PER_GROUP,
    SNIPPET_MAX_LINES,
    SNIPPET_TOTAL_CHARS,
    select,
)
from flightcheck.diagnostics.prompt import INSTRUCTIONS, build_request, preview_payload
from flightcheck.diagnostics.sanitize import Sanitizer
from flightcheck.diagnostics.schema import SCHEMA_NAME
from flightcheck.diagnostics.service import ExplainOptions
from flightcheck.orchestrator import check_catalogue
from flightcheck.providers.bharatcode import BharatCodeAdapter
from flightcheck.runtime.validators import CHECKS

CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "BHARATCODE_CONTRACT.md"


# --- selection --------------------------------------------------------------------------------


def test_groups_ordered_by_severity_then_count_then_rule_id():
    rep = report(
        [
            finding("FC-GEN-004", severity=Severity.MEDIUM, line=1),
            finding("FC-GEN-005", severity=Severity.MEDIUM, line=2),
            finding("FC-GEN-005", severity=Severity.MEDIUM, line=3),
            finding("FC-BC-002", severity=Severity.HIGH, line=4),
            finding("FC-BC-001", severity=Severity.HIGH, line=5),
            finding("FC-GEN-003", severity=Severity.LOW, line=6),
        ]
    )
    sel = select(rep, max_groups=10)
    assert [g.rule_id for g in sel.groups] == [
        "FC-BC-001",
        "FC-BC-002",
        "FC-GEN-005",
        "FC-GEN-004",
        "FC-GEN-003",
    ]


def test_group_and_instance_limits():
    rep = report(
        [finding("FC-BC-001", line=n) for n in range(1, 6)]
        + [finding(f"FC-GEN-00{n}", severity=Severity.LOW, line=n) for n in range(1, 7)]
    )
    sel = select(rep)  # default 5 groups
    assert len(sel.groups) == 5 and sel.dropped_groups == 2
    first = sel.groups[0]
    assert first.total == 5 and len(first.instances) == INSTANCES_PER_GROUP
    assert first.instance_ids == [f.id for f in rep.findings[:3]]
    assert len(select(rep, max_groups=2).groups) == 2


def test_selects_failed_unmirrored_and_needs_review_checks_only():
    mirrored = check("FC-RT-012", "S04", status=CheckStatus.FAILED, severity=Severity.HIGH)
    unmirrored = check("FC-RT-013", "S04", status=CheckStatus.FAILED, severity=Severity.HIGH)
    review = check("FC-RT-041", "S02")
    ignored = [
        check("FC-RT-001", "S01", status=status)
        for status in (
            CheckStatus.PASSED,
            CheckStatus.SKIPPED,
            CheckStatus.UNSUPPORTED,
            CheckStatus.ERROR,
        )
    ]
    runtime = finding("FC-RT-012", kind=FindingKind.RUNTIME, path=None, check_ref=mirrored.id)
    rep = report(
        [runtime, finding("FC-BC-007", kind=FindingKind.HYPOTHESIS, severity=Severity.LOW)],
        [
            scenario("S04", [mirrored, unmirrored]),
            scenario("S02", [review]),
            scenario("S01", ignored),
        ],
    )
    sel = select(rep, max_groups=10, catalogue=check_catalogue())
    ids = {g.rule_id: g.instance_ids for g in sel.groups}
    assert ids == {
        "FC-RT-012": [runtime.id],  # the finding, not the mirrored check again
        "FC-RT-013": ["S04:FC-RT-013"],
        "FC-RT-041": ["S02:FC-RT-041"],
        "FC-BC-007": [rep.findings[1].id],  # hypotheses are explained too
    }


def test_needs_review_title_and_remediation_come_from_the_injected_catalogue():
    rep = report([], [scenario("S02", [check("FC-RT-041", "S02")])])
    without = select(rep).groups[0]
    assert (without.title, without.remediation) == ("FC-RT-041", "")
    with_cat = select(rep, catalogue=check_catalogue()).groups[0]
    assert with_cat.title == CHECKS["FC-RT-041"].title
    assert with_cat.remediation == CHECKS["FC-RT-041"].remediation


def test_group_severity_is_the_worst_instance():
    rep = report(
        [
            finding(
                "FC-RT-020",
                kind=FindingKind.RUNTIME,
                severity=Severity.HIGH,
                path=None,
                check_ref="S05:FC-RT-020",
            )
        ],
        [scenario("S03", [check("FC-RT-020", "S03", severity=Severity.INFO)])],
    )
    (group,) = select(rep).groups
    assert group.severity is Severity.HIGH and group.total == 2


def test_metadata_level_has_no_source_and_no_traffic(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "chat.py").write_text("x = 1\n" * 20)
    rep = report(
        [finding(line=5)],
        [
            scenario(
                "S03", [check(exchange_seq=1, observed="o" * 900)], [exchange(1, {"input": "body"})]
            )
        ],
    )
    sel = select(rep, repo=tmp_path)
    instances = [i for g in sel.groups for i in g.instances]
    assert all(i.snippet is None and i.traffic_excerpt is None for i in instances)
    assert sel.files == []
    review = next(i for i in instances if i.source == "check")
    assert len(review.observed or "") <= 300  # capped at the metadata level


# --- snippets ---------------------------------------------------------------------------------


def _write(root: Path, rel: str, lines: int, width: int = 10) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"{n:0{width}d}" for n in range(1, lines + 1)) + "\n")


def test_snippet_is_eight_lines_around_the_finding(tmp_path):
    _write(tmp_path, "app/chat.py", 100)
    rep = report([finding(line=50)])
    (instance,) = select(rep, level="snippets", repo=tmp_path).groups[0].instances
    lines = (instance.snippet or "").splitlines()
    assert instance.snippet_start_line == 42 and len(lines) == 17
    assert lines[0].endswith("42") and lines[-1].endswith("58")


def test_snippet_is_capped_at_forty_lines_and_clipped_at_file_edges(tmp_path):
    _write(tmp_path, "app/chat.py", 200)
    rep = report([finding(line=3)])
    f = rep.findings[0]
    f.location.end_line = 120  # type: ignore[union-attr]
    (instance,) = select(rep, level="snippets", repo=tmp_path).groups[0].instances
    assert instance.snippet_start_line == 1
    assert len((instance.snippet or "").splitlines()) == SNIPPET_MAX_LINES


def test_total_snippet_budget(tmp_path):
    for n in range(1, 6):
        _write(tmp_path, f"app/f{n}.py", 60, width=300)  # ~5 KB per 17-line snippet
    rep = report([finding(f"FC-BC-00{n}", path=f"app/f{n}.py", line=30) for n in range(1, 6)])
    sel = select(rep, level="snippets", repo=tmp_path)
    sent = [i.snippet for g in sel.groups for i in g.instances if i.snippet]
    assert 0 < len(sent) < 5 and sum(len(s) for s in sent) <= SNIPPET_TOTAL_CHARS
    assert any("snippet budget" in note for note in sel.notes)


def test_wrong_repo_omits_the_snippet_with_a_note(tmp_path):
    rep = report([finding(line=5), finding("FC-GEN-004", line=9)])
    sel = select(rep, level="snippets", repo=tmp_path / "elsewhere")
    assert all(i.snippet is None for g in sel.groups for i in g.instances)
    (note,) = sel.notes  # one note per missing file
    assert note.startswith("snippet omitted: app/chat.py was not found under --repo ")
    assert note.endswith("elsewhere")


def test_paths_outside_the_repo_are_never_read(tmp_path):
    (tmp_path / "secret.py").write_text("TOP_SECRET = 1\n" * 20)
    repo = tmp_path / "repo"
    repo.mkdir()
    rep = report([finding(path="../secret.py", line=5)])
    sel = select(rep, level="snippets", repo=repo)
    assert sel.groups[0].instances[0].snippet is None
    assert "TOP_SECRET" not in json.dumps([n for n in sel.notes])


def test_check_traffic_excerpt_is_capped(tmp_path):
    big = {"input": "z" * 5000}
    rep = report([], [scenario("S03", [check(exchange_seq=1)], [exchange(1, big)])])
    (instance,) = select(rep, level="snippets", repo=tmp_path).groups[0].instances
    assert instance.traffic_excerpt is not None and len(instance.traffic_excerpt) <= 2000


# --- contract excerpts ------------------------------------------------------------------------


def test_every_excerpt_still_appears_verbatim_in_the_contract_document():
    lines = CONTRACT.read_text(encoding="utf-8").splitlines()
    for table in (ce.VERIFIED, ce.UNVERIFIED):
        for topic, text in table.items():
            assert any(line.startswith(f"| {topic} ") and text in line for line in lines), (
                f"contract drift: {topic!r}"
            )


def test_unverified_topics_keep_their_label_and_every_topic_exists():
    for text in ce.UNVERIFIED.values():
        assert text.startswith("**[UNVERIFIED]**")
    for rule_id, topics in ce.RULE_TOPICS.items():
        for topic in topics:
            assert topic in ce.VERIFIED or topic in ce.UNVERIFIED, (rule_id, topic)
    assert "[UNVERIFIED]" in (ce.excerpt_for("FC-BC-007") or "")
    assert (ce.excerpt_for("FC-BC-001") or "").startswith("[VERIFIED] Responses — unsupported")


def test_excerpts_cover_every_runtime_check_with_a_contract_row():
    without_contract = {"FC-RT-008", "FC-RT-060"}  # baseline exit, request limit: no row
    assert set(CHECKS) - set(ce.RULE_TOPICS) == without_contract
    assert ce.excerpts_for(["FC-RT-008", "FC-BC-001"]).keys() == {"FC-BC-001"}


# --- prompt and request -----------------------------------------------------------------------


def test_request_matches_plan_19_5(tmp_path):
    plan = service(config(tmp_path, llm="fake")).prepare(
        report([finding()]), ExplainOptions(repo=tmp_path)
    )
    request = plan.calls[0].request
    assert request.model == BHARATCODE_DEFAULT_MODEL and request.store is False
    assert request.max_output_tokens == 4000 and request.reasoning_effort == "none"
    assert request.instructions == INSTRUCTIONS
    assert request.text_format is not None
    assert request.text_format["name"] == SCHEMA_NAME and request.text_format["strict"] is True
    assert request.text_format["type"] == "json_schema"
    document = json.loads(str(request.input))
    assert document["provider"] == "bharatcode" and document["context_level"] == "metadata"
    assert set(document["contract_excerpts"]) == {"FC-BC-001"}
    assert document["groups"][0]["instances"][0]["path"] == "app/chat.py"


@pytest.mark.parametrize("structured", [True, False])
def test_preview_payload_equals_the_adapter_payload(structured):
    from diag_support import mock_transport
    from pydantic import SecretStr

    from flightcheck.core.config import BHARATCODE_BASE_URL, ProviderConfig

    adapter = BharatCodeAdapter(
        ProviderConfig(
            name="bharatcode",
            kind="bharatcode",
            base_url=BHARATCODE_BASE_URL,
            api_key=SecretStr(FAKE_KEY),
        ),
        transport=mock_transport().transport,
        sleep=lambda _s: None,
    )
    request = build_request(
        '{"groups": []}',
        model="qwen-3.8-27b",
        max_output_tokens=4000,
        reasoning_none=structured,
        structured=structured,
    )
    with adapter:
        assert preview_payload(request) == adapter.responses_payload(request)


def test_sanitizer_is_applied_to_group_fields(tmp_path):
    rep = report([finding(message=f"failed in {tmp_path.resolve()}/app/chat.py")])
    sel = select(rep, repo=tmp_path, sanitizer=Sanitizer(tmp_path))
    assert sel.groups[0].instances[0].message == "failed in app/chat.py"


def test_diagnostics_imports_only_core_and_providers():
    """docs/ARCHITECTURE.md §3: diagnostics → core, providers (never runtime/scanner/mock)."""
    import ast

    package = Path(__file__).resolve().parents[2] / "src" / "flightcheck" / "diagnostics"
    allowed = ("flightcheck.core", "flightcheck.providers", "flightcheck.diagnostics")
    for module in package.glob("*.py"):
        for node in ast.walk(ast.parse(module.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            for name in names:
                if name.startswith("flightcheck"):
                    assert name.startswith(allowed), f"{module.name} imports {name}"
                assert name.split(".")[0] not in {"subprocess", "importlib", "runpy"}

"""§19.6 post-validation of AI output: IDs, patches, fences, strict parsing."""

from __future__ import annotations

from pathlib import Path

import pytest
from diag_support import (
    batch_json,
    config,
    explanation,
    finding,
    mock_transport,
    ok_text,
    report,
    service,
    steps,
)

from flightcheck.core.models import AI_LABEL
from flightcheck.diagnostics.service import ExplainOptions, strip_code_fences, unified_diff

SOURCE = "\n".join(
    [f"# line {n}" for n in range(1, 10)]
    + ["response = client.responses.create(model=m, input=x, store=True)"]
    + [f"# line {n}" for n in range(11, 25)]
)
BAD_LINE = "response = client.responses.create(model=m, input=x, store=True)"
GOOD_LINE = "response = client.responses.create(model=m, input=x, store=False)"


@pytest.fixture
def app(tmp_path: Path) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "chat.py").write_text(SOURCE + "\n", encoding="utf-8")
    return tmp_path


def _run(root: Path, answer: str, *, context: str = "metadata", rep=None):
    rep = rep or report([finding("FC-BC-001", line=10)])
    bridge = mock_transport(steps(ok_text(answer)))
    svc = service(config(root, llm="bharatcode", context=context), bridge.transport)
    out = svc.explain(rep, ExplainOptions(repo=root, consent=lambda _i: True))
    return rep, out


def _fid(rep) -> str:
    return rep.findings[0].id


def test_unknown_finding_ids_are_rejected(app):
    rep = report([finding("FC-BC-001", line=10)])
    _, out = _run(app, batch_json(explanation("FC-BC-001", [_fid(rep), "f-0000000000"])), rep=rep)
    (s,) = out.ai_suggestions
    assert s.explanation is None and s.fallback_reason == "rejected"
    assert "f-0000000000" in (s.rejected_reason or "")


def test_empty_finding_ids_are_rejected(app):
    _, out = _run(app, batch_json(explanation("FC-BC-001", [])))
    assert out.ai_suggestions[0].rejected_reason == "no finding_ids"


def test_explanation_for_a_rule_that_was_not_sent_is_dropped(app):
    rep = report([finding("FC-BC-001", line=10)])
    answer = batch_json(explanation("FC-BC-999", [_fid(rep)]))
    _, out = _run(app, answer, rep=rep)
    (s,) = out.ai_suggestions
    assert s.rule_id == "FC-BC-001" and s.explanation is None
    assert s.fallback_reason == "missing_explanation"


def test_a_finding_id_from_another_group_is_rejected(app):
    rep = report([finding("FC-BC-001", line=10), finding("FC-GEN-004", line=12)])
    other = rep.findings[1].id
    answer = batch_json(explanation("FC-BC-001", [other]), explanation("FC-GEN-004", [other]))
    _, out = _run(app, answer, rep=rep)
    by_rule = {s.rule_id: s for s in out.ai_suggestions}
    assert by_rule["FC-BC-001"].fallback_reason == "rejected"
    assert by_rule["FC-GEN-004"].explanation is not None


def test_first_answer_per_rule_wins(app):
    rep = report([finding("FC-BC-001", line=10)])
    answer = batch_json(
        explanation("FC-BC-001", [_fid(rep)], summary="first"),
        explanation("FC-BC-001", [_fid(rep)], summary="second"),
    )
    _, out = _run(app, answer, rep=rep)
    assert out.ai_suggestions[0].explanation.summary == "first"


def _patch(original: str, path: str = "app/chat.py") -> dict:
    return {"path": path, "original_snippet": original, "proposed_snippet": GOOD_LINE}


def test_patch_at_the_metadata_level_is_rejected(app):
    rep = report([finding("FC-BC-001", line=10)])
    answer = batch_json(explanation("FC-BC-001", [_fid(rep)], patch=_patch(BAD_LINE)))
    _, out = _run(app, answer, rep=rep)
    s = out.ai_suggestions[0]
    assert s.explanation is None and s.diff is None
    assert s.rejected_reason == "patch proposed without snippets context"


def test_verbatim_patch_at_the_snippets_level_gets_a_local_labelled_diff(app):
    rep = report([finding("FC-BC-001", line=10)])
    answer = batch_json(explanation("FC-BC-001", [_fid(rep)], patch=_patch(BAD_LINE)))
    _, out = _run(app, answer, context="snippets", rep=rep)
    s = out.ai_suggestions[0]
    assert s.explanation is not None and s.explanation.patch is not None
    assert s.diff is not None and s.diff.startswith(f"# {AI_LABEL}\n")
    assert "-" + BAD_LINE in s.diff and "+" + GOOD_LINE in s.diff
    assert "--- a/app/chat.py" in s.diff


def test_non_verbatim_patch_is_rejected(app):
    rep = report([finding("FC-BC-001", line=10)])
    invented = "client.responses.create(store=True, model=m)"
    answer = batch_json(explanation("FC-BC-001", [_fid(rep)], patch=_patch(invented)))
    _, out = _run(app, answer, context="snippets", rep=rep)
    assert out.ai_suggestions[0].rejected_reason == (
        "original_snippet is not verbatim in the snippet sent"
    )


def test_patch_for_a_path_that_was_not_sent_is_rejected(app):
    rep = report([finding("FC-BC-001", line=10)])
    answer = batch_json(
        explanation("FC-BC-001", [_fid(rep)], patch=_patch(BAD_LINE, "app/other.py"))
    )
    _, out = _run(app, answer, context="snippets", rep=rep)
    assert out.ai_suggestions[0].rejected_reason == "patch path was not sent with a snippet"


def test_code_fences_are_stripped_before_strict_parsing(app):
    rep = report([finding("FC-BC-001", line=10)])
    fenced = "```json\n" + batch_json(explanation("FC-BC-001", [_fid(rep)])) + "\n```"
    _, out = _run(app, fenced, rep=rep)
    assert out.ai_suggestions[0].explanation is not None


def test_prose_around_the_json_is_invalid_output(app):
    rep = report([finding("FC-BC-001", line=10)])
    answer = "Here you go:\n" + batch_json(explanation("FC-BC-001", [_fid(rep)]))
    _, out = _run(app, answer, rep=rep)
    assert out.ai_suggestions[0].fallback_reason == "invalid_output"


@pytest.mark.parametrize(
    "override",
    [
        {"summary": "x" * 601},
        {"fix_steps": ["a"] * 7},
        {"model_stated_confidence": "certain"},
        {"extra_field": 1},
    ],
)
def test_schema_violations_are_invalid_output(app, override):
    rep = report([finding("FC-BC-001", line=10)])
    _, out = _run(app, batch_json(explanation("FC-BC-001", [_fid(rep)], **override)), rep=rep)
    s = out.ai_suggestions[0]
    assert s.explanation is None and s.fallback_reason == "invalid_output"


def test_secrets_in_ai_output_are_redacted(app):
    rep = report([finding("FC-BC-001", line=10)])
    leaked = "bc_live_" + "abcdefghijklmnop"
    answer = batch_json(explanation("FC-BC-001", [_fid(rep)], root_cause=f"key {leaked}"))
    _, out = _run(app, answer, rep=rep)
    assert leaked not in out.model_dump_json()
    assert "[REDACTED]" in out.ai_suggestions[0].explanation.root_cause


def test_strip_code_fences_unit():
    assert strip_code_fences('```\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_code_fences('  ```JSON\n{"a": 1}```  ') == '{"a": 1}'
    assert strip_code_fences('{"a": 1}') == '{"a": 1}'
    assert strip_code_fences('text ```{"a": 1}```') == 'text ```{"a": 1}```'


def test_unified_diff_is_generated_locally():
    diff = unified_diff("a.py", "x = 1\n", "x = 2\n")
    assert diff.splitlines()[:3] == [f"# {AI_LABEL}", "--- a/a.py", "+++ b/a.py"]

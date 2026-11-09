"""§19.4 sanitisation: planted secrets never leave the process; paths, headers, size, UTF-8."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from diag_support import (
    batch_json,
    check,
    config,
    exchange,
    explanation,
    finding,
    mock_transport,
    ok_text,
    report,
    scenario,
    service,
    steps,
)

from flightcheck.core.models import CheckStatus, FindingKind, Severity
from flightcheck.diagnostics.sanitize import STRING_LIMIT, TRUNCATION_MARKER, Sanitizer
from flightcheck.diagnostics.service import ExplainOptions
from flightcheck.reporting.json_report import report_to_json

BC_LIVE = "bc_live_" + "Zq81Xv0Lm2Nn"
SK = "sk-" + "proj4f9a8b7c6d5e4f3a"
BEARER_TOKEN = "eyJhbGciOiJIUzI1NiJ9" + "payload42"
ENV_VALUE = "env-only-value-" + "7731qx"  # matches no pattern: caught as an env value
PLANTED = [BC_LIVE, SK, BEARER_TOKEN, ENV_VALUE]


@pytest.fixture
def planted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MY_SERVICE_TOKEN", ENV_VALUE)
    app = tmp_path / "app"
    app.mkdir()
    lines = [f"# filler {n}" for n in range(1, 30)]
    lines[9] = f'client = OpenAI(api_key="{BC_LIVE}")'
    lines[10] = f'other = "{SK}"'
    lines[11] = f'headers = {{"Authorization": "Bearer {BEARER_TOKEN}"}}'
    lines[12] = f'token_from_env = "{ENV_VALUE}"'
    (app / "chat.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    failed = check(
        "FC-RT-050",
        "S01",
        status=CheckStatus.FAILED,
        severity=Severity.CRITICAL,
        message=f"Real key seen: {BC_LIVE}",
        observed=f"Bearer {BEARER_TOKEN}",
        exchange_seq=1,
    )
    review = check("FC-RT-020", "S03", observed=f"stderr {ENV_VALUE} {SK}", exchange_seq=2)
    traffic = [
        exchange(
            1,
            {"input": f"key {BC_LIVE}", "headers": {"authorization": SK}},
            headers={"authorization": f"Bearer {BEARER_TOKEN}"},
        ),
        exchange(2, {"input": ENV_VALUE, "metadata": SK}),
    ]
    rep = report(
        [
            finding("FC-SEC-001", line=10, message=f"Hard-coded key {BC_LIVE} in {tmp_path}"),
            finding("FC-SEC-002", line=12, message=f"Bearer {BEARER_TOKEN} literal"),
        ],
        [scenario("S01", [failed], traffic[:1]), scenario("S03", [review], traffic[1:])],
        command="test",
    )
    return tmp_path, rep


def _groups(payload: dict) -> list:
    return json.loads(payload["input"])["groups"]


def _assert_clean(text: str) -> None:
    for secret in PLANTED:
        assert secret not in text


def test_planted_secrets_never_reach_the_serialised_request_payload(planted):
    root, rep = planted
    svc = service(config(root, llm="bharatcode", context="snippets"))
    plan = svc.prepare(rep, ExplainOptions(repo=root))
    assert plan.selection.files == ["app/chat.py"]  # the snippet really was included
    snippets = [i.snippet for g in plan.selection.groups for i in g.instances if i.snippet]
    assert snippets and "[REDACTED" in snippets[0]
    assert any(i.traffic_excerpt for g in plan.selection.groups for i in g.instances)
    serialised = json.dumps(plan.payloads(), ensure_ascii=False)
    _assert_clean(serialised)
    assert str(root) not in serialised  # absolute paths made relative
    excerpts = [
        instance["request_body_excerpt"]
        for group in _groups(plan.payloads()[0])
        for instance in group["instances"]
        if "request_body_excerpt" in instance
    ]
    assert excerpts  # traffic bodies were included, but never headers
    assert all("authorization" not in e.lower() and "FAKE KEY" not in e for e in excerpts)


def test_planted_secrets_never_reach_the_wire_or_the_explained_report(planted):
    root, rep = planted
    fid = rep.findings[0].id
    leak = batch_json(explanation("FC-SEC-001", [fid], summary=f"echo {BC_LIVE} {ENV_VALUE}"))
    bridge = mock_transport(steps(ok_text(leak)))
    svc = service(config(root, llm="bharatcode", context="snippets"), bridge.transport)
    out = svc.explain(rep, ExplainOptions(repo=root, consent=lambda _i: True))
    (request,) = bridge.requests
    _assert_clean(request.content.decode("utf-8"))
    _assert_clean(out.ai_suggestions[0].model_dump_json())
    _assert_clean(report_to_json(out))


def test_dry_run_payloads_are_clean_at_the_metadata_level_too(planted):
    root, rep = planted
    plan = service(config(root, llm="fake")).prepare(rep, ExplainOptions(repo=root))
    serialised = json.dumps(plan.payloads())
    _assert_clean(serialised)
    groups = json.dumps(_groups(plan.payloads()[0]))
    assert '"snippet' not in groups  # metadata: no source lines
    assert "request_body_excerpt" not in groups  # and no traffic bodies


def test_absolute_paths_become_repo_relative_and_home_becomes_tilde(tmp_path):
    sanitizer = Sanitizer(tmp_path)
    root = str(tmp_path.resolve())
    assert sanitizer.text(f"error in {root}/app/chat.py:3") == "error in app/chat.py:3"
    assert sanitizer.text(f"cwd {root}") == "cwd ."
    assert sanitizer.text(f"{root}2/x.py") == f"{root}2/x.py"  # a sibling is not the root
    home = str(Path.home())
    assert Sanitizer(None).text(f"{home}/proj/a.py") == "~/proj/a.py"


def test_long_strings_are_truncated_with_a_marker():
    out = Sanitizer(None).text("a" * 5000)
    assert len(out) == STRING_LIMIT and out.endswith(TRUNCATION_MARKER)
    assert Sanitizer(None).text("short") == "short"


def test_secret_at_the_cut_is_redacted_before_truncation():
    text = "x" * (STRING_LIMIT - 20) + BC_LIVE + "y" * 100
    assert "Zq81" not in Sanitizer(None).text(text)


def test_invalid_utf8_is_replaced():
    sanitizer = Sanitizer(None)
    assert sanitizer.text(b"ok \xff\xfe end") == "ok �� end"
    assert sanitizer.text("lone \udcff surrogate") == "lone ? surrogate"


def test_headers_and_credential_keys_are_dropped_entirely():
    data = {
        "request_headers": {"authorization": "x"},
        "headers": {"a": "b"},
        "Authorization": "Bearer zzzzzzzzzzzz",
        "api_key": "abc",
        "keep": ["v", {"response_headers": {}}],
    }
    assert Sanitizer(None).obj(data) == {"keep": ["v", {}]}


def test_runtime_finding_traffic_excerpt_has_bodies_only(planted):
    root, rep = planted
    runtime = finding("FC-RT-050", kind=FindingKind.RUNTIME, path=None, check_ref="S01:FC-RT-050")
    rep = rep.model_copy(update={"findings": [*rep.findings, runtime]})
    plan = service(config(root, llm="fake", context="snippets")).prepare(
        rep, ExplainOptions(repo=root)
    )
    instance = next(
        i for g in plan.selection.groups for i in g.instances if i.finding_id == runtime.id
    )
    assert instance.traffic_excerpt and "request_json" in instance.traffic_excerpt
    assert "authorization" not in instance.traffic_excerpt.lower()
    _assert_clean(instance.traffic_excerpt)

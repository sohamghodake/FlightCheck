"""Self-contained HTML report (plan §24, ADR-021, ADR-032).

``build_view(report)`` turns a report into a pre-grouped view model (plain dicts, lists and
strings) so the Jinja template holds no logic; ``render_html(report)`` renders it with
``autoescape=True``. The view is built from ``redact_obj(report.model_dump(mode="json"))``, the
same redaction pass as the JSON report, never from the raw models; text derived from it
(pretty-printed traffic bodies) is redacted again.

The output has no JavaScript and no external assets: inline CSS only (ADR-021). The footer
shows ``metadata.generated_at`` from the report, never the wall clock, so the same JSON always
renders the same HTML.
"""

from __future__ import annotations

import json
import os
import shlex
from collections import Counter
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined

from flightcheck.core.errors import ConfigError
from flightcheck.core.models import (
    AI_LABEL,
    CheckStatus,
    FindingKind,
    Report,
    Severity,
    Verification,
)
from flightcheck.core.paths import resolve_inside
from flightcheck.core.redact import redact, redact_obj
from flightcheck.reporting.terminal import FALLBACK_HINTS

HTML_FILENAME = "flightcheck-report.html"
TEMPLATE_NAME = "report.html.j2"

# §20.14: the same words everywhere.
VERIFICATION_LABELS = {
    Verification.STATIC.value: "static",
    Verification.RUNTIME_MOCK.value: "observed with mock",
    Verification.RUNTIME_LIVE.value: "observed live",
}
HYPOTHESIS_LABEL = "hypothesis (unverified)"
STATUS_LABELS = {
    CheckStatus.PASSED.value: "passed",
    CheckStatus.FAILED.value: "failed",
    CheckStatus.NEEDS_REVIEW.value: "needs review",
    CheckStatus.SKIPPED.value: "skipped",
    CheckStatus.UNSUPPORTED.value: "unsupported",
    CheckStatus.ERROR.value: "error",
}
# Display order of the six statuses (worst first, as in the terminal tables).
STATUS_ORDER = (
    CheckStatus.FAILED.value,
    CheckStatus.ERROR.value,
    CheckStatus.NEEDS_REVIEW.value,
    CheckStatus.PASSED.value,
    CheckStatus.UNSUPPORTED.value,
    CheckStatus.SKIPPED.value,
)
MOCK_FIDELITY_NOTICES = {
    "documentation_model": (
        "Runtime results were observed with FlightCheck's local mock, which models BharatCode's "
        "public documentation. They were not compared with the live API, so a passed check means "
        "“behaved correctly against the documented contract”, not “works against BharatCode”."
    ),
    "compared_with_live": (
        "Runtime results were observed with FlightCheck's local mock; the mock's behaviour has "
        "been compared with the live BharatCode API."
    ),
    "n/a": "No runtime scenarios were run for this report (static analysis only).",
}
# Plan §16.2 / docs/RULES.md "Explicit limitations", as plain text.
LIMITATIONS = (
    "One-hop name resolution only: a call's client is found from an inline constructor, a name "
    "bound in the same function, an enclosing function or the module, or self.<attr> in the "
    "same class. Clients passed as arguments, returned from factories, stored in containers or "
    "imported from other modules are not resolved; their call sites are still found and take "
    "the repository-level provider at low confidence.",
    "One-hop container literals: tools=TOOLS is followed only to a list/dict/tuple literal "
    "bound to TOOLS before the call in the same function, an enclosing function or the module.",
    "Only literal values are inspected: store=cfg.store is unknown and never produces a "
    "contract violation.",
    "No inter-procedural data flow, no type inference, no evaluation of decorators, dynamic "
    "imports, getattr dispatch or framework magic.",
    "“Function never references finish_reason”-style heuristics are lexical and phrased as "
    "“not observed in this function”: a check performed in a helper function elsewhere is a "
    "false positive. These rules are medium or low confidence.",
    "try coverage is the try body only: a call in an except, else or finally block, or in a "
    "function defined inside a try, counts as unprotected.",
    "Static findings never claim runtime behaviour. “Requests from this call site will …” is "
    "used only for documented contract rules with literal evidence; everything else uses "
    "“may”.",
)
# Size guards (§27 Phase 9 item 13: a full report stays well under 2 MB).
MESSAGE_LIMIT = 4000
BODY_LIMIT = 4000
EXCERPT_LIMIT = 8000


def _clip(text: str | None, limit: int) -> str:
    if not text:
        return ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _pretty(value: Any, limit: int = BODY_LIMIT) -> str:
    """Pretty JSON of an already-redacted value, redacted again (keys included)."""
    if value is None or value == {} or value == []:
        return ""
    text = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False)
    return _clip(redact(text), limit)


def _safe_url(url: Any) -> str:
    """Only http(s) links become ``href``s (autoescape does not stop ``javascript:``)."""
    if isinstance(url, str) and url.startswith(("https://", "http://")):
        return url
    return ""


def _when(stamp: Any) -> str:
    """``generated_at`` (ISO 8601 from the JSON) as ``YYYY-MM-DD HH:MM:SS UTC``."""
    if not isinstance(stamp, str):
        return ""
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return stamp
    offset = moment.utcoffset()
    if offset is not None and offset.total_seconds() == 0:
        return moment.strftime("%Y-%m-%d %H:%M:%S UTC")
    return moment.isoformat()


def _location(loc: dict[str, Any] | None) -> str:
    if not loc:
        return ""
    line = loc.get("line") or 0
    return f"{loc['path']}:{line}" if line else str(loc["path"])


def _evidence(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    out = []
    for item in items:
        label = item["type"]
        if item.get("exchange_seq") is not None:
            label = f"{label} #{item['exchange_seq']}"
        out.append(
            {
                "label": label,
                "location": _location(item.get("location")),
                "excerpt": item.get("excerpt", ""),
                "note": item.get("note", ""),
            }
        )
    return out


def _finding_view(f: dict[str, Any]) -> dict[str, Any]:
    hypothesis = f["kind"] == FindingKind.HYPOTHESIS.value
    loc = f.get("location")
    return {
        "id": f["id"],
        "rule_id": f["rule_id"],
        "title": f["title"],
        "severity": f["severity"],
        "kind": f["kind"],
        "verification": f["verification"],
        "verification_label": HYPOTHESIS_LABEL
        if hypothesis
        else VERIFICATION_LABELS[f["verification"]],
        "verification_class": "hypothesis" if hypothesis else f["verification"],
        "confidence": f["confidence"],
        "line": f"L{loc['line']}" if loc and loc.get("line") else "",
        "message": _clip(f["message"], MESSAGE_LIMIT),
        "remediation": _clip(f.get("remediation", ""), MESSAGE_LIMIT),
        "doc_url": _safe_url(f.get("doc_url")),
        "provider": f.get("provider") or "",
        "check_ref": f.get("check_ref") or "",
        "evidence": _evidence(f.get("evidence", [])),
    }


def _findings_by_file(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group by file in first-seen order (a file never appears twice)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for f in findings:
        path = f["location"]["path"] if f.get("location") else ""
        groups.setdefault(path, []).append(_finding_view(f))
    return [
        {"path": path or "(repository)", "count": len(items), "findings": items}
        for path, items in groups.items()
    ]


def _summary_cards(data: dict[str, Any]) -> dict[str, Any]:
    """Counts by severity, check status and verification level. Hypotheses get their own
    card and are not in the verification counts; AI suggestions are never counted here."""
    summary = data["summary"]
    findings = data["findings"]
    checks = [c for s in data["scenarios"] for c in s["checks"]]
    by_sev = summary["findings_by_severity"]
    by_kind = summary["findings_by_kind"]
    by_status = summary["checks_by_status"]
    by_check_ver = summary["checks_by_verification"]
    verified = Counter(
        f["verification"] for f in findings if f["kind"] != FindingKind.HYPOTHESIS.value
    )
    return {
        "findings_total": len(findings),
        "checks_total": len(checks),
        "highest_severity": summary.get("highest_severity") or "",
        "severity": [{"name": s.value, "count": by_sev.get(s.value, 0)} for s in Severity],
        "kind": [
            {"name": FindingKind.STATIC.value, "count": by_kind.get(FindingKind.STATIC.value, 0)},
            {
                "name": FindingKind.RUNTIME.value,
                "count": by_kind.get(FindingKind.RUNTIME.value, 0),
            },
        ],
        "hypotheses": by_kind.get(FindingKind.HYPOTHESIS.value, 0),
        "status": [
            {"name": s, "label": STATUS_LABELS[s], "count": by_status.get(s, 0)}
            for s in STATUS_ORDER
        ],
        "verification": [
            {
                "name": v.value,
                "label": VERIFICATION_LABELS[v.value],
                "findings": verified.get(v.value, 0),
                "checks": by_check_ver.get(v.value, 0),
            }
            for v in Verification
        ],
    }


def _app_exit(run: dict[str, Any] | None) -> str:
    if run is None:
        return "not run"
    if run["timed_out"]:
        return "timeout"
    return f"exit {run['exit_code']}"


def _scenario_view(s: dict[str, Any]) -> dict[str, Any]:
    counts = Counter(c["status"] for c in s["checks"])
    run = s.get("app_run")
    return {
        "id": s["scenario_id"],
        "name": s["name"],
        "status": s["status"],
        "status_label": STATUS_LABELS[s["status"]],
        "app_exit": _app_exit(run),
        "duration_ms": run["duration_ms"] if run else None,
        "command": shlex.join(run["command"]) if run else "",
        "stdout": _clip(run["stdout_excerpt"], EXCERPT_LIMIT) if run else "",
        "stderr": _clip(run["stderr_excerpt"], EXCERPT_LIMIT) if run else "",
        "requests": len(s["exchanges"]),
        "counts": [
            {"name": st, "label": STATUS_LABELS[st], "count": counts.get(st, 0)}
            for st in STATUS_ORDER
        ],
        "checks": [
            {
                "id": c["id"],
                "check_id": c["check_id"],
                "status": c["status"],
                "status_label": STATUS_LABELS[c["status"]],
                "severity": c["severity"],
                "verification": c["verification"],
                "verification_label": VERIFICATION_LABELS[c["verification"]],
                "message": _clip(c["message"], MESSAGE_LIMIT),
                "reason": c.get("reason") or "",
                "expected": _clip(c.get("expected"), MESSAGE_LIMIT),
                "observed": _clip(c.get("observed"), MESSAGE_LIMIT),
                "evidence": _evidence(c.get("evidence", [])),
            }
            for c in s["checks"]
        ],
        "exchanges": [
            {
                "seq": e["seq"],
                "method": e["method"],
                "path": e["path"],
                "endpoint": e.get("endpoint") or "",
                "stream": "yes" if e["stream"] else "no",
                "step_id": e["step_id"],
                "status": e["response_status"],
                "request": _pretty(e.get("request_json")),
                "response": _pretty(e.get("response_summary")),
            }
            for e in s["exchanges"]
        ],
    }


def _provenance(entry: dict[str, Any]) -> str:
    if entry["backend"] == "fake":
        return "fake LLM, offline; not generated by BharatCode"
    if entry["backend"] == "bharatcode":
        return f"BharatCode {entry.get('model') or 'unknown model'}"
    return "no model (deterministic fallback)"


def _ai_view(data: dict[str, Any]) -> dict[str, Any]:
    entries = []
    for s in data["ai_suggestions"]:
        exp = s.get("explanation")
        reason = s.get("fallback_reason") or ""
        entries.append(
            {
                "rule_id": s["rule_id"],
                "title": s["title"],
                "severity": s["severity"],
                "instances": len(s["finding_ids"]),
                "finding_ids": ", ".join(s["finding_ids"]),
                "provenance": _provenance(s),
                "backend": s["backend"],
                "model": s.get("model") or "",
                "structured_output": s["structured_output"],
                "generated_at": _when(s.get("generated_at")),
                "explanation": None
                if exp is None
                else {
                    "summary": exp["summary"],
                    "root_cause": exp["root_cause"],
                    "impact": exp["impact"],
                    "fix_steps": list(exp["fix_steps"]),
                    "caveats": list(exp["caveats"]),
                    "confidence": exp["model_stated_confidence"],
                },
                "diff": s.get("diff") or "",
                "fallback_reason": reason,
                "fallback_hint": FALLBACK_HINTS.get(reason, ""),
                "rejected_reason": s.get("rejected_reason") or "",
                "remediation": s.get("remediation") or "",
            }
        )
    explained = sum(1 for e in entries if e["explanation"] is not None)
    return {
        "label": AI_LABEL,
        "entries": entries,
        "explained": explained,
        "fallbacks": len(entries) - explained,
    }


def _capability_view(profile: dict[str, Any] | None) -> dict[str, Any] | None:
    if not profile:
        return None
    return {
        "provider": profile["provider"],
        "base_url": profile["base_url"],
        "documented_models": ", ".join(profile.get("documented_models") or []) or "none listed",
        "max_output_tokens": profile.get("max_output_tokens") or "unknown",
        "entries": [
            {
                "capability": name,
                "level": entry["level"],
                "source": entry["source"],
                "note": entry.get("note", ""),
            }
            for name, entry in profile["entries"].items()
        ],
    }


def build_view(report: Report) -> dict[str, Any]:
    """The template's only input: redacted, pre-grouped, display-ready values."""
    data: dict[str, Any] = redact_obj(report.model_dump(mode="json"))
    meta = data["metadata"]
    repo = data.get("repo")
    options = [
        {"name": key, "value": json.dumps(value, ensure_ascii=False, sort_keys=True)}
        for key, value in sorted(meta.get("options", {}).items())
    ]
    return {
        "title": f"FlightCheck report — {repo['root_name']}" if repo else "FlightCheck report",
        "meta": {
            "tool_version": meta["tool_version"],
            "schema_version": meta["schema_version"],
            "command": meta["command"],
            "generated_at": _when(meta["generated_at"]),
            "python_version": meta["python_version"],
            "platform": meta["platform"],
            "mock_fidelity": meta["mock_fidelity"],
            "options": options,
        },
        "repo": None
        if repo is None
        else {
            "root_name": repo["root_name"],
            "files": repo["python_files_scanned"],
            "bytes": repo["total_bytes_scanned"],
            "skipped": [{"reason": k, "count": v} for k, v in repo["files_skipped"].items()],
        },
        "cards": _summary_cards(data),
        "providers": [
            {
                "provider": d["provider"],
                "confidence": d["confidence"],
                "location": _location(d["location"]),
                "signals": "; ".join(d["signals"]),
            }
            for d in data["providers_detected"]
        ],
        "capability": _capability_view(data.get("capability_profile")),
        "file_groups": _findings_by_file(data["findings"]),
        "scenarios": [_scenario_view(s) for s in data["scenarios"]],
        "diagnostics": [
            {
                "level": d["level"],
                "code": d["code"],
                "message": _clip(d["message"], MESSAGE_LIMIT),
                "path": d.get("path") or "",
            }
            for d in data["diagnostics"]
        ],
        "ai": _ai_view(data),
        "limitations": list(LIMITATIONS),
        "mock_fidelity_notice": MOCK_FIDELITY_NOTICES.get(meta["mock_fidelity"], ""),
    }


@lru_cache(maxsize=1)
def _environment() -> Environment:
    return Environment(
        autoescape=True,
        loader=PackageLoader("flightcheck.reporting"),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def render_html(report: Report) -> str:
    """The report as one self-contained HTML document (no JS, no external assets)."""
    return _environment().get_template(TEMPLATE_NAME).render(view=build_view(report))


def write_html(report: Report, path: str | os.PathLike[str]) -> Path:
    """Write the HTML report to the file ``path`` (parent created if needed); return it.

    The file goes through ``resolve_inside(parent, name)``, so a symlink pointing outside the
    directory is refused.
    """
    target = Path(path)
    if target.exists() and target.is_dir():
        raise ConfigError(f"HTML output is a directory, not a file: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    written = resolve_inside(target.parent, target.name)
    written.write_text(render_html(report), encoding="utf-8")
    return written


__all__ = ["HTML_FILENAME", "build_view", "render_html", "write_html"]

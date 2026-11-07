"""Evaluate the static scanner on FlightCheck's curated fixtures (plan §26).

Runs ``orchestrator.run_scan`` on the rule fixtures (one scan) and on every fixture app (one scan
per app directory), compares findings with ``tests/fixtures/expected_findings.json`` on
``(rule_id, path, line)``, and reports per-rule TP/FN/FP, detection rate and precision, the
non-info finding count on clean fixtures, and the dogfood scan of ``src/``.

    python scripts/eval_fixtures.py            # print the summary and write docs/EVALUATION.md
    python scripts/eval_fixtures.py --check    # print the summary; exit 1 if a §26.2 gate fails

``--check`` does not write the report (CI and the local gates leave the tree unchanged). The
script imports flightcheck; it never runs the ``flightcheck`` CLI. ``--runtime`` arrives with
Phase 7B.
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from flightcheck.core.config import FlightCheckConfig
from flightcheck.core.models import Report, ScanRequest
from flightcheck.orchestrator import run_scan
from flightcheck.scanner.engine import load_builtin_rules

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "tests" / "fixtures" / "expected_findings.json"
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "EVALUATION.md"
CAVEAT = (
    "measured on FlightCheck's own curated fixtures — a regression check, not a measure of "
    "real-world accuracy"
)
Key = tuple[str, str, int]  # (rule_id, path, line)


@dataclass
class Evaluation:
    expected: set[Key] = field(default_factory=set)
    observed: set[Key] = field(default_factory=set)
    clean_non_info: list[Key] = field(default_factory=list)
    clean_apps: list[str] = field(default_factory=list)
    dogfood: Counter[str] = field(default_factory=Counter)
    files_scanned: int = 0

    def per_rule(self, rule_ids: list[str]) -> list[tuple[str, int, int, int]]:
        """``(rule_id, TP, FN, FP)`` for every registered rule, in registry order."""
        rows = []
        for rule_id in rule_ids:
            exp = {k for k in self.expected if k[0] == rule_id}
            obs = {k for k in self.observed if k[0] == rule_id}
            rows.append((rule_id, len(exp & obs), len(exp - obs), len(obs - exp)))
        return rows

    @property
    def missed(self) -> set[Key]:
        return self.expected - self.observed

    @property
    def unexpected(self) -> set[Key]:
        return self.observed - self.expected


def _keys(report: Report, prefix: str = "") -> set[Key]:
    return {
        (f.rule_id, prefix + f.location.path, f.location.line)
        for f in report.findings
        if f.location is not None
    }


def _scan(root: Path) -> Report:
    return run_scan(ScanRequest(root=root), FlightCheckConfig())


def evaluate(manifest: dict) -> Evaluation:
    result = Evaluation()
    rules_root = REPO_ROOT / manifest["root"]
    for path, items in manifest["files"].items():
        result.expected |= {(item["rule_id"], path, item["line"]) for item in items}
    report = _scan(rules_root)
    result.observed |= _keys(report)
    result.files_scanned += report.repo.python_files_scanned

    suite = manifest["apps"]
    apps_root = REPO_ROOT / suite["root"]
    for app, entry in sorted(suite["items"].items()):
        prefix = f"apps/{app}/"
        for path, items in entry["files"].items():
            result.expected |= {(item["rule_id"], prefix + path, item["line"]) for item in items}
        report = _scan(apps_root / app)
        result.observed |= _keys(report, prefix)
        result.files_scanned += report.repo.python_files_scanned
        if entry["clean"]:
            result.clean_apps.append(app)
            result.clean_non_info += [
                (f.rule_id, prefix + f.location.path, f.location.line)
                for f in report.findings
                if f.severity.value != "info" and f.location is not None
            ]

    dogfood = _scan(REPO_ROOT / "src")
    result.dogfood = Counter(f.rule_id for f in dogfood.findings)
    return result


def gates(result: Evaluation) -> list[tuple[str, str, bool]]:
    """§26.2 gates this script measures: ``(gate, observed value, passed)``."""
    detected = len(result.expected) - len(result.missed)
    dogfood_bad = sum(
        n for rule, n in result.dogfood.items() if rule.startswith(("FC-BC-", "FC-SEC-"))
    )
    return [
        (
            "Expected P0 findings on curated fixtures: 100 % detected",
            f"{detected}/{len(result.expected)}",
            not result.missed,
        ),
        (
            "Non-info findings on clean fixtures: 0",
            str(len(result.clean_non_info)),
            not result.clean_non_info,
        ),
        (
            "Dogfood `flightcheck scan src/`: 0 FC-BC-* and 0 FC-SEC-*",
            str(dogfood_bad),
            dogfood_bad == 0,
        ),
    ]


def _pct(num: int, den: int) -> str:
    return "n/a" if den == 0 else f"{100 * num / den:.1f} %"


def _commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


def render(result: Evaluation, rule_ids: list[str]) -> str:
    rows = result.per_rule(rule_ids)
    tp = sum(r[1] for r in rows)
    fn = sum(r[2] for r in rows)
    fp = sum(r[3] for r in rows)
    lines = [
        "# FlightCheck evaluation",
        "",
        "Generated by `python scripts/eval_fixtures.py`; do not edit by hand.",
        "",
        f"> All numbers here are {CAVEAT}.",
        "",
        f"- Date: {date.today().isoformat()}",
        f"- Commit (HEAD when generated): `{_commit()}`",
        f"- Python: {platform.python_version()} ({platform.system().lower() or 'unknown'})",
        f"- Fixture files scanned: {result.files_scanned} "
        f"(rule fixtures in one scan, then each app in `tests/fixtures/apps/` on its own)",
        "- Matching: a finding counts when `(rule_id, path, line)` equals a manifest entry in "
        "`tests/fixtures/expected_findings.json`.",
        "",
        "## Static rules",
        "",
        "| Rule | Expected | TP | FN | FP | Detection rate | Precision |",
        "| ---- | -------: | -: | -: | -: | -------------: | --------: |",
    ]
    for rule_id, r_tp, r_fn, r_fp in rows:
        lines.append(
            f"| {rule_id} | {r_tp + r_fn} | {r_tp} | {r_fn} | {r_fp} | "
            f"{_pct(r_tp, r_tp + r_fn)} | {_pct(r_tp, r_tp + r_fp)} |"
        )
    lines.append(
        f"| **All** | {tp + fn} | {tp} | {fn} | {fp} | {_pct(tp, tp + fn)} | {_pct(tp, tp + fp)} |"
    )
    lines += [
        "",
        "The manifest records one known false positive as expected, so changes to it stay "
        "visible: FC-GEN-001 on `apps/known_fp_helper_check/main.py` (the `finish_reason` check "
        "is in a helper function; the rule is lexical, plan §16.2). It counts as a TP above.",
        "",
        "## Clean fixtures",
        "",
        f"Clean apps: {', '.join(f'`{a}`' for a in result.clean_apps)}. "
        f"Non-info findings on them: **{len(result.clean_non_info)}**.",
        "",
        "## Dogfood",
        "",
        "`run_scan` on `src/`: "
        + (
            ", ".join(f"{rule} × {n}" for rule, n in sorted(result.dogfood.items()))
            or "no findings"
        )
        + ".",
        "",
        "## Quality gates (plan §26.2)",
        "",
        "| Gate | Observed | Result |",
        "| ---- | -------- | ------ |",
    ]
    for name, value, passed in gates(result):
        lines.append(f"| {name} | {value} | {'pass' if passed else '**FAIL**'} |")
    lines += [
        "| Planted secrets in outputs: 0 | `tests/security/test_scan_redaction.py` | "
        "checked by pytest |",
        '| `pytest -m "not live"` on 3.11/3.12/3.13 | CI | checked by CI |',
        "| `ruff check` / `ruff format --check` | CI | checked by CI |",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if a §26.2 gate fails")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="report path")
    args = parser.parse_args(argv)

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    result = evaluate(manifest)
    rule_ids = list(load_builtin_rules())
    for name, value, passed in gates(result):
        print(f"[{'pass' if passed else 'FAIL'}] {name}: {value}")
    for key in sorted(result.missed):
        print(f"  missed: {key[0]} {key[1]}:{key[2]}")
    for key in sorted(result.unexpected):
        print(f"  unexpected: {key[0]} {key[1]}:{key[2]}")
    for key in result.clean_non_info:
        print(f"  clean-fixture finding: {key[0]} {key[1]}:{key[2]}")
    if args.check:
        return 0 if all(passed for _, _, passed in gates(result)) else 1
    args.output.write_text(render(result, rule_ids), encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

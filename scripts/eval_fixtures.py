"""Evaluate the static scanner (and, with --runtime, the harness) on FlightCheck's fixtures.

Runs ``orchestrator.run_scan`` on the rule fixtures (one scan) and on every fixture app (one scan
per app directory), compares findings with ``tests/fixtures/expected_findings.json`` on
``(rule_id, path, line)``, and reports per-rule TP/FN/FP, detection rate and precision, the
non-info finding count on clean fixtures, and the dogfood scan of ``src/``.

    python scripts/eval_fixtures.py            # print the summary and write docs/EVALUATION.md
    python scripts/eval_fixtures.py --check    # print the summary; exit 1 if a §26.2 gate fails
    python scripts/eval_fixtures.py --runtime  # also run every fixture app (S01–S06) on the mock

``--check`` does not write the report (CI and the local gates leave the tree unchanged). The
script imports flightcheck; it never runs the ``flightcheck`` CLI. ``--runtime`` is the consent
to run FlightCheck's own fixture apps (``tests/fixtures/apps``) through ``orchestrator.run_test``
(the harness: scrubbed environment, mock on 127.0.0.1, fake key) and compares every
``(fixture, scenario, check_id)`` status, scenario status and runtime finding with
``tests/fixtures/expected_runtime.json`` (§26.1 "Runtime agreement"). ``python`` in the
fixtures' ``[test].command`` resolves to this interpreter.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from flightcheck.core.config import FlightCheckConfig, load_config
from flightcheck.core.models import FindingKind, Report, ScanRequest, TestRequest
from flightcheck.orchestrator import run_scan, run_test
from flightcheck.scanner.engine import load_builtin_rules

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "tests" / "fixtures" / "expected_findings.json"
RUNTIME_MANIFEST_PATH = REPO_ROOT / "tests" / "fixtures" / "expected_runtime.json"
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


RuntimeKey = tuple[str, str, str]  # (fixture, scenario, check_id)


@dataclass
class RuntimeEvaluation:
    expected: dict[RuntimeKey, str] = field(default_factory=dict)
    observed: dict[RuntimeKey, str] = field(default_factory=dict)
    scenario_mismatches: list[str] = field(default_factory=list)  # status / runtime findings
    scenarios: list[str] = field(default_factory=list)
    apps: int = 0

    @property
    def keys(self) -> list[RuntimeKey]:
        return sorted(set(self.expected) | set(self.observed))

    @property
    def disagreements(self) -> list[RuntimeKey]:
        return [k for k in self.keys if self.expected.get(k) != self.observed.get(k)]

    def per_scenario(self) -> list[tuple[str, int, int]]:
        """``(scenario, agreeing, total)`` triples per scenario."""
        rows = []
        bad = set(self.disagreements)
        for scenario in self.scenarios:
            keys = [k for k in self.keys if k[1] == scenario]
            rows.append((scenario, sum(k not in bad for k in keys), len(keys)))
        return rows


def _runtime_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    return env


def evaluate_runtime(manifest: dict) -> RuntimeEvaluation:
    """Run every fixture app through the manifest's scenarios (no [test.expect] overrides)."""
    result = RuntimeEvaluation(scenarios=list(manifest["scenarios"]))
    root = REPO_ROOT / manifest["root"]
    env = _runtime_env()
    for app, entry in sorted(manifest["apps"].items()):
        result.apps += 1
        request = TestRequest(
            root=root / app, allow_exec=True, scenarios=result.scenarios, include_scan=False
        )
        report = run_test(request, load_config(root / app), environ=env)
        got = {s.scenario_id: s for s in report.scenarios}
        for scenario in result.scenarios:
            want = entry.get(scenario, {})
            for check_id, status in want.get("checks", {}).items():
                result.expected[(app, scenario, check_id)] = status
            observed = got.get(scenario)
            if observed is None:
                result.scenario_mismatches.append(f"{app} {scenario}: no result")
                continue
            for check in observed.checks:
                result.observed[(app, scenario, check.check_id)] = check.status.value
            if observed.status.value != want.get("status"):
                result.scenario_mismatches.append(
                    f"{app} {scenario}: status {observed.status.value}, expected "
                    f"{want.get('status')}"
                )
        findings = sorted({f.rule_id for f in report.findings if f.kind is FindingKind.RUNTIME})
        if findings != entry.get("runtime_findings", []):
            result.scenario_mismatches.append(
                f"{app}: runtime findings {findings}, expected {entry.get('runtime_findings')}"
            )
    return result


def runtime_gates(result: RuntimeEvaluation) -> list[tuple[str, str, bool]]:
    keys = result.keys
    agree = len(keys) - len(result.disagreements)
    return [
        (
            "Runtime agreement (fixture, scenario, check_id): 100 %",
            f"{agree}/{len(keys)}",
            agree == len(keys) and bool(keys),
        ),
        (
            "Scenario statuses and runtime findings match expected_runtime.json",
            f"{len(result.scenario_mismatches)} mismatch(es)",
            not result.scenario_mismatches,
        ),
    ]


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


def render(
    result: Evaluation, rule_ids: list[str], runtime: RuntimeEvaluation | None = None
) -> str:
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
    all_gates = gates(result) + (runtime_gates(runtime) if runtime else [])
    for name, value, passed in all_gates:
        lines.append(f"| {name} | {value} | {'pass' if passed else '**FAIL**'} |")
    lines += [
        "| Planted secrets in outputs: 0 | `tests/security/test_scan_redaction.py` | "
        "checked by pytest |",
        '| `pytest -m "not live"` on 3.11/3.12/3.13 | CI | checked by CI |',
        "| `ruff check` / `ruff format --check` | CI | checked by CI |",
        "",
    ]
    if runtime is None:
        lines += [
            "Runtime agreement was not measured in this run "
            "(`python scripts/eval_fixtures.py --runtime`).",
            "",
        ]
        return "\n".join(lines)
    lines += [
        "## Runtime agreement (plan §26.1)",
        "",
        f"Every fixture app ({runtime.apps}) ran through {', '.join(runtime.scenarios)} against "
        "the mock (mock fidelity: documentation model; no `[test.expect]`), and each "
        "`(fixture, scenario, check_id)` status was compared with "
        "`tests/fixtures/expected_runtime.json`. Contract checks are reported once per run "
        "(ADR-029), so they count under the first scenario only.",
        "",
        "| Scenario | Agreeing | Total | Agreement |",
        "| -------- | -------: | ----: | --------: |",
    ]
    total_agree = total = 0
    for scenario, agree, count in runtime.per_scenario():
        total_agree += agree
        total += count
        lines.append(f"| {scenario} | {agree} | {count} | {_pct(agree, count)} |")
    lines += [
        f"| **All** | {total_agree} | {total} | {_pct(total_agree, total)} |",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if a §26.2 gate fails")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="report path")
    parser.add_argument(
        "--runtime",
        action="store_true",
        help="also run the fixture apps on the mock and check expected_runtime.json",
    )
    args = parser.parse_args(argv)

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    result = evaluate(manifest)
    rule_ids = list(load_builtin_rules())
    runtime = None
    if args.runtime:
        runtime = evaluate_runtime(json.loads(RUNTIME_MANIFEST_PATH.read_text(encoding="utf-8")))
    all_gates = gates(result) + (runtime_gates(runtime) if runtime else [])
    for name, value, passed in all_gates:
        print(f"[{'pass' if passed else 'FAIL'}] {name}: {value}")
    for key in sorted(result.missed):
        print(f"  missed: {key[0]} {key[1]}:{key[2]}")
    for key in sorted(result.unexpected):
        print(f"  unexpected: {key[0]} {key[1]}:{key[2]}")
    for key in result.clean_non_info:
        print(f"  clean-fixture finding: {key[0]} {key[1]}:{key[2]}")
    if runtime is not None:
        for key in runtime.disagreements:
            print(
                f"  runtime: {key[0]} {key[1]} {key[2]}: {runtime.observed.get(key)}, "
                f"expected {runtime.expected.get(key)}"
            )
        for line in runtime.scenario_mismatches:
            print(f"  runtime: {line}")
    if args.check:
        return 0 if all(passed for _, _, passed in all_gates) else 1
    args.output.write_text(render(result, rule_ids, runtime), encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

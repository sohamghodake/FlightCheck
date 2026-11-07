"""docs/report.schema.json must match Report.model_json_schema() (ADR-010 drift check)."""

from __future__ import annotations

import json
from pathlib import Path

from flightcheck.core.models import Report
from flightcheck.reporting.json_report import export_schema, schema_json

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "report.schema.json"
REGENERATE = "python -m flightcheck.reporting.json_report docs/report.schema.json"


def test_checked_in_schema_has_no_drift() -> None:
    assert SCHEMA_PATH.read_text(encoding="utf-8") == schema_json(), f"Run: {REGENERATE}"


def test_schema_describes_report_v1() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["title"] == "Report"
    assert {"metadata", "summary"} <= set(schema["required"])
    version = schema["$defs"]["ReportMetadata"]["properties"]["schema_version"]
    assert version.get("const") == "1.0" or version.get("enum") == ["1.0"]


def test_export_schema_writes_same_text(tmp_path: Path) -> None:
    out = export_schema(tmp_path / "schema.json")
    assert out.read_text(encoding="utf-8") == schema_json()
    assert json.loads(out.read_text(encoding="utf-8")) == Report.model_json_schema()

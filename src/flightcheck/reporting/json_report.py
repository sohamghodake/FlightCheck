"""JSON report writer/reader and schema export (plan §24).

Writing: ``Report.model_dump(mode="json")`` → ``redact_obj`` → ``json.dumps(indent=2)``.
Reading refuses unknown schema majors with a :class:`ConfigError` (exit 2).

Regenerate the checked-in schema with::

    python -m flightcheck.reporting.json_report docs/report.schema.json
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from flightcheck.core.errors import ConfigError
from flightcheck.core.models import SCHEMA_VERSION, Report
from flightcheck.core.paths import resolve_inside
from flightcheck.core.redact import redact_obj

REPORT_FILENAME = "flightcheck-report.json"
SUPPORTED_MAJOR = SCHEMA_VERSION.split(".")[0]


def report_to_json(report: Report) -> str:
    """Redacted, deterministic JSON text of ``report`` (ends with a newline)."""
    data = redact_obj(report.model_dump(mode="json"))
    return json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def write_report(report: Report, out_dir: str | os.PathLike[str]) -> Path:
    """Write ``flightcheck-report.json`` into ``out_dir`` (created if needed); return its path.

    The file path goes through ``resolve_inside``, so a symlinked report file pointing outside
    ``out_dir`` is refused.
    """
    directory = Path(out_dir)
    if directory.exists() and not directory.is_dir():
        raise ConfigError(f"Output path is not a directory: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    target = resolve_inside(directory, REPORT_FILENAME)
    target.write_text(report_to_json(report), encoding="utf-8")
    return target


def read_report(path: str | os.PathLike[str]) -> Report:
    """Load a saved report. Unknown schema majors and invalid files → :class:`ConfigError`."""
    source = Path(path)
    try:
        data: Any = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Report not found: {source}") from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Not a readable FlightCheck JSON report: {source}") from exc
    version = data.get("metadata", {}).get("schema_version") if isinstance(data, dict) else None
    if not isinstance(version, str):
        raise ConfigError(f"Not a FlightCheck report (no metadata.schema_version): {source}")
    if version.split(".")[0] != SUPPORTED_MAJOR:
        raise ConfigError(
            f"Unsupported report schema version {version!r}; this FlightCheck reads "
            f"{SUPPORTED_MAJOR}.x reports.",
            hint="Re-run the scan with this version of FlightCheck.",
        )
    try:
        return Report.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(
            f"Invalid FlightCheck report {source}: {exc.error_count()} validation error(s)"
        ) from exc


def schema_json() -> str:
    """JSON Schema of :class:`Report` as checked in at ``docs/report.schema.json``."""
    schema = Report.model_json_schema()
    return json.dumps(schema, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def export_schema(path: str | os.PathLike[str]) -> Path:
    target = Path(path)
    target.write_text(schema_json(), encoding="utf-8")
    return target


if __name__ == "__main__":  # pragma: no cover - maintenance entry point
    print(export_schema(sys.argv[1] if len(sys.argv) > 1 else "docs/report.schema.json"))

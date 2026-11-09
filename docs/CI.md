# Using FlightCheck in CI

FlightCheck is a CLI, so any CI system that runs Python 3.11+ can use it. This page shows
GitHub Actions; the commands are the same elsewhere. The one-step `flightcheck ci` command is
P1 and not available yet: use `scan` (and, if you choose, `test`) directly.

## Quick start (GitHub Actions)

Copy [.github/workflows/flightcheck-example.yml](../.github/workflows/flightcheck-example.yml)
into your repository and adapt three things:

1. **Trigger.** In this repository the example runs only by hand (`workflow_dispatch`), because
   FlightCheck's own fixture apps are deliberately broken and a scan of `.` would fail every
   push. In your project, use the triggers you want:

   ```yaml
   on:
     push:
     pull_request:
   ```

2. **Install step.** Replace `pip install .` (which installs FlightCheck from this checkout)
   with an install from the FlightCheck repository, ideally pinned to a tag or commit:

   ```yaml
   - name: Install FlightCheck
     run: pip install "git+https://github.com/sohamghodake/FlightCheck@main"
   ```

3. **Path.** Set `PATH_TO_SCAN` (or the `path` input) to the directory that holds your Python AI
   app, for example `.` or `backend/`.

The scan step is:

```bash
flightcheck scan "$PATH_TO_SCAN" --format all --fail-on high
```

`--format all` prints the terminal tables into the job log and writes both
`flightcheck-report.json` and `flightcheck-report.html` to `$PATH_TO_SCAN/.flightcheck/`. The
upload step runs with `if: always()`, so the reports are attached as the `flightcheck-report`
artifact even when the scan step fails the job. `.flightcheck/` is a dot-directory, so the
upload sets `include-hidden-files: true`.

## Exit codes

| Code | Meaning                                                                                   | What CI does |
| ---- | ----------------------------------------------------------------------------------------- | ------------ |
| 0    | Completed; no finding (or failed runtime check) at or above `--fail-on`                   | step passes  |
| 1    | Completed; at least one finding at or above `--fail-on`                                   | step fails   |
| 2    | Usage, configuration or consent error (bad flag, invalid `flightcheck.toml`, no `--allow-exec`, `explain` without `--yes` in CI) | step fails |
| 3    | Internal error (for example the mock could not start); re-run with `-v`                   | step fails   |

Exit codes never depend on `--format`. AI suggestions (`explain`) never change an exit code.

## Choosing `--fail-on`

`--fail-on` takes `critical`, `high` (the default), `medium`, `low`, `info` or `none`. A finding
at or above the threshold makes the run exit 1.

- Start with `--fail-on high`: contract violations that make BharatCode requests fail.
- Tighten to `medium` or `low` once the high findings are fixed.
- `--fail-on none` always exits 0 (report only).
- Hypotheses (findings labelled `[UNVERIFIED]`, such as FC-BC-007) never trigger `--fail-on`
  unless you add `--fail-on-hypotheses`.

FlightCheck's own CI runs `flightcheck scan src --fail-on low` on its source (the dogfood gate).

## Artifacts

| File                          | Written by                    | Content                                       |
| ----------------------------- | ----------------------------- | --------------------------------------------- |
| `flightcheck-report.json`     | `scan`, `test` (always)       | The report (schema `docs/report.schema.json`) |
| `flightcheck-report.html`     | `--format html` or `all`      | Self-contained HTML (no JavaScript)           |
| `flightcheck-traffic.json`    | `test --keep-traffic`         | Redacted recorded exchanges                   |
| `<report>.explained.json` and `.html` | `explain` (`.html` with `--format html` or `all`) | Report plus the AI suggestions section |

Use `--output DIR` to put them elsewhere, for example `--output "$RUNNER_TEMP/flightcheck"`.
Every file is redacted (known key patterns, `Bearer` values, secret-looking environment values).
`flightcheck report REPORT.json` re-renders a saved report as HTML (default) or in the terminal
(`--format terminal`) without a key, network access or code execution.

## Why `test` needs `--allow-exec` in CI too

`flightcheck test` **runs your application** (the `[test]` command in `flightcheck.toml`)
against a local traffic-recording mock on 127.0.0.1. Running code is never implied by
configuration: every run needs `--allow-exec`, in CI as well as locally. Only add it when the
job already trusts the code it checks out (for example, not for pull requests from forks
without review). The egress guard is best effort, not a sandbox. A runtime step looks like:

```bash
flightcheck test "$PATH_TO_SCAN" --allow-exec --format all --fail-on high
```

The mock uses a fake API key; no BharatCode key is needed and nothing is sent to BharatCode.

## `explain` in CI

`flightcheck explain` sends findings to BharatCode only after consent. In CI (no TTY, or
`CI=true`) it exits 2 unless you pass `--yes`, after printing exactly what would be sent. Review
the payload first with `--dry-run`. The default context is metadata only (no source code).
`--llm fake` produces a clearly labelled offline demo and sends nothing.

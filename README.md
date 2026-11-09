# FlightCheck

> Test any AI integration. Ship with confidence.

FlightCheck is a local-first, CLI-first Python tool that inspects a Python AI application's source
code and its actual HTTP traffic to find integration mistakes against BharatCode's documented API
contract (for example `store:true` or `previous_response_id` on `/responses`, truncated output
treated as complete, tool results without a matching `call_id`, or no handling for `503` busy
responses). It reports evidence-backed findings, keeps "proven" separate from "suspected", and can
explain findings with BharatCode's own model only when you explicitly opt in.

Built for the BharatCode Build League (Round 1), direction: Coding & developer tools.

## Status

**Under construction.** Only `flightcheck --version` works today. The planned commands are
`scan`, `test`, `explain` and `report`; see [docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md)
for progress and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

## Install (development)

Requires Python 3.11 or newer.

With pip:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
flightcheck --version
```

With [uv](https://docs.astral.sh/uv/):

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
flightcheck --version
```

## Development checks

```bash
ruff check . && ruff format --check . && pytest -m "not live" -q
```

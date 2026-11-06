"""Shared pytest configuration for FlightCheck's own test suite."""

import pytest


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forbid real network access in every test (live tests opt in explicitly)."""
    monkeypatch.setenv("FLIGHTCHECK_NO_NETWORK", "1")

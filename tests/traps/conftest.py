"""The trap suite runs against the live Valio API and the real catalog.

tests/conftest.py replaces the catalog and the Valio client for the offline tests; these same-named
fixtures switch that off here. Run with: pytest -m live tests/traps (or python scripts/run_traps.py)."""
import os
from pathlib import Path

import pytest

os.environ.setdefault("GATEWAY_DB", str(Path(__file__).resolve().parents[2] / "state" / "traps.db"))


@pytest.fixture(autouse=True)
def fixed_catalog():
    yield


@pytest.fixture(autouse=True)
def no_network():
    yield


@pytest.fixture(autouse=True)
def no_claude():
    yield

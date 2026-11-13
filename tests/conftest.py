"""Fixtures compartidas: cada test arranca con un MockClient nuevo e independiente."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import argenequip_client  # noqa: E402


@pytest.fixture
def cliente(monkeypatch):
    mock = argenequip_client.MockClient()
    monkeypatch.setattr(argenequip_client, "_cliente", mock)   # get_client() devuelve este
    return mock

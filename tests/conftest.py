from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        api_token="test-api-token-0123456789",
        data_dir=tmp_path / "data",
        output_dir=tmp_path / "output",
        max_concurrent_downloads=2,
        aria2_rpc_port=16891,
        encryption_key=Fernet.generate_key().decode("ascii"),
        allow_private_targets=False,
    )


@pytest.fixture()
def client(settings):
    from fastapi.testclient import TestClient
    app = create_app(settings, start_worker=False)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def auth_headers():
    return {"X-API-Key": "test-api-token-0123456789"}

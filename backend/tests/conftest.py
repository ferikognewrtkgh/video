from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def checkpoint_dir(tmp_path: Path) -> Path:
    path = tmp_path / "checkpoints"
    path.mkdir()
    return path


@pytest.fixture
def app(checkpoint_dir: Path, tmp_path: Path):
    return create_app(
        checkpoint_dir=checkpoint_dir,
        media_root=tmp_path,
        seed_demo=False,
        environment={"MANGAFLOW_PROVIDER": "mock"},
    )


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def mars_payload():
    return {
        "name": "红沙信号",
        "source_text": "火星基地的维修工程师发现一台失联机器人正在沙暴中发送求救信号。她必须穿过废弃隧道，在氧气耗尽前救回它。",
        "target_duration_sec": 52,
        "style": "硬科幻青年漫画",
    }


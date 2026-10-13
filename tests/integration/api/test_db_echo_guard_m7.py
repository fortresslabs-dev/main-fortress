"""
M7 — init_db must not enable SQLAlchemy `echo` on production even if
DEBUG=true accidentally lands in the env. echo prints fully-bound
SQL including bound API keys / passwords / customer data into the
container logs.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest


@pytest.fixture(autouse=True)
def _restore_db_globals():
    """init_db rebinds module-globals `engine` and `async_session_maker`;
    snapshot them so other tests in the suite keep their bootstrapped
    session pool after these tests run."""
    from src.services import database as db_mod
    snap_engine = db_mod.engine
    snap_maker = db_mod.async_session_maker
    yield
    db_mod.engine = snap_engine
    db_mod.async_session_maker = snap_maker


class _FakeConn:
    async def run_sync(self, fn): pass


class _FakeEngine:
    @asynccontextmanager
    async def begin(self):
        yield _FakeConn()
    async def dispose(self): pass


def _run_init_capture(monkeypatch, *, debug: bool, env: str) -> dict:
    from src.utils import config as cfg_mod
    monkeypatch.setattr(cfg_mod.settings, "DEBUG", debug, raising=False)
    monkeypatch.setattr(cfg_mod.settings, "ENVIRONMENT", env, raising=False)

    from src.services import database as db_mod
    captured: dict = {}

    def _fake_create_engine(url, **kwargs):
        captured.update(kwargs)
        return _FakeEngine()

    monkeypatch.setattr(db_mod, "create_async_engine", _fake_create_engine)
    monkeypatch.setattr(
        db_mod, "async_sessionmaker", lambda *a, **kw: lambda: None
    )

    asyncio.get_event_loop().run_until_complete(db_mod.init_db())
    return captured


@pytest.mark.parametrize("env_name", ["production", "PRODUCTION", "Prod"])
def test_m7_echo_off_in_production_regardless_of_debug(env_name, monkeypatch):
    """DEBUG=true on a production stack must NOT enable echo."""
    captured = _run_init_capture(monkeypatch, debug=True, env=env_name)
    assert captured.get("echo") is False, (
        f"echo={captured.get('echo')!r} with ENVIRONMENT={env_name} + DEBUG=true. "
        f"Echo must be False on production regardless of DEBUG."
    )


def test_m7_echo_on_in_test_when_debug_true(monkeypatch):
    """DEBUG-aware path still works for local dev/test."""
    captured = _run_init_capture(monkeypatch, debug=True, env="test")
    assert captured.get("echo") is True


def test_m7_echo_off_when_debug_false(monkeypatch):
    """No-DEBUG stays off everywhere."""
    captured = _run_init_capture(monkeypatch, debug=False, env="development")
    assert captured.get("echo") is False

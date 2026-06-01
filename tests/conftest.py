"""
conftest.py — Shared pytest fixtures.
Each test gets a fresh isolated DB to avoid cross-test contamination.
"""
import pytest
import uuid
from pathlib import Path
import app.database as db_module


@pytest.fixture(autouse=True)
async def isolated_db(tmp_path):
    """Give each test its own SQLite file."""
    db_module.DB_PATH = tmp_path / f"test_{uuid.uuid4().hex[:8]}.db"
    db_module._db_initialised = False
    await db_module.init_db()
    yield
    db_module._db_initialised = False

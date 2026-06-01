"""
database.py — SQLite async database using aiosqlite.
Uses context manager pattern (no threading issues in tests).
"""

import aiosqlite
import logging
from pathlib import Path

DB_PATH = Path("/tmp/store_intelligence.db")
logger = logging.getLogger(__name__)

_db_initialised = False


async def init_db():
    global _db_initialised
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id    TEXT UNIQUE NOT NULL,
            store_id    TEXT NOT NULL,
            camera_id   TEXT NOT NULL,
            visitor_id  TEXT NOT NULL,
            event_type  TEXT NOT NULL,
            timestamp   TEXT NOT NULL,
            zone_id     TEXT,
            dwell_ms    INTEGER DEFAULT 0,
            is_staff    INTEGER DEFAULT 0,
            confidence  REAL DEFAULT 0.0,
            queue_depth INTEGER,
            sku_zone    TEXT,
            session_seq INTEGER DEFAULT 1,
            inserted_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_store_time ON events(store_id, timestamp);
        CREATE INDEX IF NOT EXISTS idx_visitor     ON events(visitor_id);
        CREATE INDEX IF NOT EXISTS idx_event_type ON events(event_type);
        CREATE INDEX IF NOT EXISTS idx_store_type ON events(store_id, event_type);

        CREATE TABLE IF NOT EXISTS pos_transactions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            store_id         TEXT NOT NULL,
            transaction_id   TEXT UNIQUE NOT NULL,
            timestamp        TEXT NOT NULL,
            basket_value_inr REAL DEFAULT 0.0
        );
        CREATE INDEX IF NOT EXISTS idx_pos_store_time ON pos_transactions(store_id, timestamp);
        """)
        await db.commit()
    _db_initialised = True
    logger.info(f"DB ready at {DB_PATH}")


async def get_db():
    if not _db_initialised:
        await init_db()
    return aiosqlite.connect(DB_PATH)


async def check_db_health() -> dict:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT COUNT(*) FROM events") as cur:
                row = await cur.fetchone()
        return {"status": "ok", "event_count": row[0] if row else 0}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

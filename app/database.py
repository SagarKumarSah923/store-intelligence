"""
database.py - SQLite async setup via aiosqlite.
"""

import os
import aiosqlite, logging
from pathlib import Path

DEFAULT_DB_FILE = Path(__file__).resolve().parents[1] / "store_intel_v2.db"
DB_PATH = Path(os.getenv("STORE_INTEL_DB_PATH", str(DEFAULT_DB_FILE))).expanduser().resolve()
logger = logging.getLogger(__name__)
_ready = False

TABLE_SCHEMAS = {
    "entry_exit_events": [
        ("dedup_key", "TEXT"),
        ("event_type", "TEXT"),
        ("id_token", "TEXT"),
        ("store_code", "TEXT"),
        ("store_id", "TEXT"),
        ("camera_id", "TEXT"),
        ("event_timestamp", "TEXT"),
        ("is_staff", "INTEGER"),
        ("gender_pred", "TEXT"),
        ("age_pred", "INTEGER"),
        ("age_bucket", "TEXT"),
        ("is_face_hidden", "INTEGER"),
        ("group_id", "TEXT"),
        ("group_size", "INTEGER"),
    ],
    "zone_events": [
        ("dedup_key", "TEXT"),
        ("event_type", "TEXT"),
        ("track_id", "INTEGER"),
        ("store_id", "TEXT"),
        ("camera_id", "TEXT"),
        ("zone_id", "TEXT"),
        ("zone_name", "TEXT"),
        ("zone_type", "TEXT"),
        ("is_revenue_zone", "TEXT"),
        ("event_time", "TEXT"),
        ("zone_hotspot_x", "REAL"),
        ("zone_hotspot_y", "REAL"),
        ("gender", "TEXT"),
        ("age", "INTEGER"),
        ("age_bucket", "TEXT"),
    ],
    "queue_events": [
        ("queue_event_id", "TEXT"),
        ("event_type", "TEXT"),
        ("track_id", "INTEGER"),
        ("store_id", "TEXT"),
        ("camera_id", "TEXT"),
        ("zone_id", "TEXT"),
        ("zone_name", "TEXT"),
        ("queue_join_ts", "TEXT"),
        ("queue_served_ts", "TEXT"),
        ("queue_exit_ts", "TEXT"),
        ("wait_seconds", "INTEGER"),
        ("queue_position_at_join", "INTEGER"),
        ("abandoned", "INTEGER"),
        ("zone_hotspot_x", "REAL"),
        ("zone_hotspot_y", "REAL"),
        ("gender", "TEXT"),
        ("age", "INTEGER"),
        ("age_bucket", "TEXT"),
    ],
    "pos_transactions": [
        ("order_id", "TEXT"),
        ("store_id", "TEXT"),
        ("order_date", "TEXT"),
        ("order_time", "TEXT"),
        ("product_id", "TEXT"),
        ("brand_name", "TEXT"),
        ("total_amount", "REAL"),
    ],
}


async def _ensure_table_columns(db, table_name, definitions):
    async with db.execute(f"PRAGMA table_info({table_name})") as cursor:
        existing = {row[1] for row in await cursor.fetchall()}
    for column, column_type in definitions:
        if column not in existing:
            await db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column} {column_type}")


async def init_db():
    global _ready
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS entry_exit_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            dedup_key       TEXT UNIQUE NOT NULL,
            event_type      TEXT NOT NULL,
            id_token        TEXT NOT NULL,
            store_code      TEXT NOT NULL,
            store_id        TEXT,
            camera_id       TEXT NOT NULL,
            event_timestamp TEXT NOT NULL,
            is_staff        INTEGER DEFAULT 0,
            gender_pred     TEXT,
            age_pred        INTEGER,
            age_bucket      TEXT,
            is_face_hidden  INTEGER DEFAULT 0,
            group_id        TEXT,
            group_size      INTEGER,
            inserted_at     TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_ee_store    ON entry_exit_events(store_code, event_type);
        CREATE INDEX IF NOT EXISTS idx_ee_token    ON entry_exit_events(id_token);
        CREATE INDEX IF NOT EXISTS idx_ee_ts       ON entry_exit_events(event_timestamp);

        CREATE TABLE IF NOT EXISTS zone_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            dedup_key       TEXT UNIQUE NOT NULL,
            event_type      TEXT NOT NULL,
            track_id        INTEGER,
            store_id        TEXT NOT NULL,
            camera_id       TEXT NOT NULL,
            zone_id         TEXT NOT NULL,
            zone_name       TEXT,
            zone_type       TEXT,
            is_revenue_zone TEXT DEFAULT 'Yes',
            event_time      TEXT NOT NULL,
            zone_hotspot_x  REAL,
            zone_hotspot_y  REAL,
            gender          TEXT,
            age             INTEGER,
            age_bucket      TEXT,
            inserted_at     TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_ze_store    ON zone_events(store_id, zone_id);
        CREATE INDEX IF NOT EXISTS idx_ze_time     ON zone_events(event_time);

        CREATE TABLE IF NOT EXISTS queue_events (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            queue_event_id          TEXT UNIQUE NOT NULL,
            event_type              TEXT NOT NULL,
            track_id                INTEGER,
            store_id                TEXT NOT NULL,
            camera_id               TEXT NOT NULL,
            zone_id                 TEXT NOT NULL,
            zone_name               TEXT,
            queue_join_ts           TEXT,
            queue_served_ts         TEXT,
            queue_exit_ts           TEXT,
            wait_seconds            INTEGER,
            queue_position_at_join  INTEGER,
            abandoned               INTEGER DEFAULT 0,
            zone_hotspot_x          REAL,
            zone_hotspot_y          REAL,
            gender                  TEXT,
            age                     INTEGER,
            age_bucket              TEXT,
            inserted_at             TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_qe_store ON queue_events(store_id);

        CREATE TABLE IF NOT EXISTS pos_transactions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id         TEXT UNIQUE,
            store_id         TEXT NOT NULL,
            order_date       TEXT NOT NULL,
            order_time       TEXT NOT NULL,
            product_id       TEXT,
            brand_name       TEXT,
            total_amount     REAL DEFAULT 0.0,
            inserted_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_pos_store ON pos_transactions(store_id, order_date);
        """)
        for table_name, schema in TABLE_SCHEMAS.items():
            await _ensure_table_columns(db, table_name, schema)
        await db.commit()
    _ready = True
    logger.info(f"DB ready at {DB_PATH}")


async def get_db():
    await init_db()
    return aiosqlite.connect(DB_PATH)


async def check_db_health() -> dict:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT COUNT(*) FROM entry_exit_events") as c:
                r = await c.fetchone()
        return {"status": "ok", "entry_event_count": r[0] if r else 0}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

import asyncio
import sqlite3
import uuid
import tempfile
from pathlib import Path
import app.database as db_module
import aiosqlite

path = Path(tempfile.gettempdir()) / f'test_{uuid.uuid4().hex[:8]}.db'
db_module.DB_PATH = path
db_module._ready = False
asyncio.run(db_module.init_db())
print('DB_PATH', db_module.DB_PATH)
print('exists', path.exists())
print('zone schema', sqlite3.connect(str(path)).execute('PRAGMA table_info(zone_events)').fetchall())

async def q():
    async with aiosqlite.connect(str(path)) as db:
        async with db.execute("SELECT DISTINCT zone_id FROM zone_events WHERE store_id=? AND event_type='zone_entered'", ('STORE_X',)) as cur:
            print(await cur.fetchall())

asyncio.run(q())

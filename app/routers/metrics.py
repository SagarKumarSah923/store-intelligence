"""
GET /stores/{store_id}/metrics
Returns: unique visitors, conversion rate, avg dwell per zone,
queue depth, abandonment rate. Excludes staff. Real-time.
"""

from fastapi import APIRouter, HTTPException
from app.database import get_db
from app.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


@router.get("/{store_id}/metrics")
async def get_metrics(store_id: str):
    async with await get_db() as db:
        db.row_factory = aiosqlite.Row

        # Unique customer visitors (non-staff ENTRY events)
        async with db.execute("""
            SELECT COUNT(DISTINCT visitor_id) as unique_visitors
            FROM events
            WHERE store_id=? AND event_type='ENTRY' AND is_staff=0
        """, (store_id,)) as cur:
            row = await cur.fetchone()
            unique_visitors = row[0] if row else 0

        # Visitors who reached billing (potential conversions)
        async with db.execute("""
            SELECT COUNT(DISTINCT visitor_id) as billing_visitors
            FROM events
            WHERE store_id=? AND event_type='BILLING_QUEUE_JOIN' AND is_staff=0
        """, (store_id,)) as cur:
            row = await cur.fetchone()
            billing_visitors = row[0] if row else 0

        # Conversion rate
        conversion_rate = round(
            billing_visitors / unique_visitors if unique_visitors > 0 else 0.0, 4
        )

        # Avg dwell per zone
        async with db.execute("""
            SELECT zone_id, AVG(dwell_ms) as avg_dwell, COUNT(*) as visits
            FROM events
            WHERE store_id=? AND event_type='ZONE_EXIT' AND is_staff=0
              AND zone_id IS NOT NULL AND dwell_ms > 0
            GROUP BY zone_id
        """, (store_id,)) as cur:
            rows = await cur.fetchall()
            avg_dwell_per_zone = {
                r[0]: {"avg_dwell_ms": round(r[1]), "visit_count": r[2]}
                for r in rows
            }

        # Current queue depth (people in BILLING but not yet exited)
        async with db.execute("""
            SELECT COUNT(DISTINCT visitor_id) FROM events
            WHERE store_id=? AND event_type='BILLING_QUEUE_JOIN' AND is_staff=0
              AND visitor_id NOT IN (
                SELECT DISTINCT visitor_id FROM events
                WHERE store_id=? AND event_type='EXIT'
              )
        """, (store_id, store_id)) as cur:
            row = await cur.fetchone()
            queue_depth = row[0] if row else 0

        # Abandonment rate
        async with db.execute("""
            SELECT COUNT(DISTINCT visitor_id) FROM events
            WHERE store_id=? AND event_type='BILLING_QUEUE_ABANDON' AND is_staff=0
        """, (store_id,)) as cur:
            row = await cur.fetchone()
            abandonments = row[0] if row else 0

        abandonment_rate = round(
            abandonments / billing_visitors if billing_visitors > 0 else 0.0, 4
        )

        # Last event time
        async with db.execute("""
            SELECT MAX(timestamp) FROM events WHERE store_id=?
        """, (store_id,)) as cur:
            row = await cur.fetchone()
            last_event_at = row[0] if row else None

    if unique_visitors == 0 and not last_event_at:
        logger.info(f"store={store_id} metrics=empty_store")

    return {
        "store_id": store_id,
        "unique_visitors": unique_visitors,
        "billing_visitors": billing_visitors,
        "conversion_rate": conversion_rate,
        "avg_dwell_per_zone": avg_dwell_per_zone,
        "queue_depth": queue_depth,
        "abandonment_rate": abandonment_rate,
        "last_event_at": last_event_at
    }


import aiosqlite

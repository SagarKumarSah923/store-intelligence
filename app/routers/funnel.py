"""
GET /stores/{store_id}/funnel
Conversion funnel: Entry → Zone Visit → Billing Queue → Purchase
Session is the unit. Re-entries don't double-count.
"""

from fastapi import APIRouter
from app.database import get_db
from app.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


@router.get("/{store_id}/funnel")
async def get_funnel(store_id: str):
    async with await get_db() as db:
        # Stage 1: Unique customers who entered
        async with db.execute("""
            SELECT COUNT(DISTINCT visitor_id) FROM events
            WHERE store_id=? AND event_type='ENTRY' AND is_staff=0
        """, (store_id,)) as cur:
            row = await cur.fetchone()
            entered = row[0] if row else 0

        # Stage 2: Entered AND visited at least one product zone
        async with db.execute("""
            SELECT COUNT(DISTINCT visitor_id) FROM events
            WHERE store_id=? AND event_type='ZONE_ENTER' AND is_staff=0
              AND zone_id NOT IN ('ENTRY_EXIT','STOCKROOM','BILLING','ACCESSORIES')
              AND visitor_id IN (
                SELECT DISTINCT visitor_id FROM events
                WHERE store_id=? AND event_type='ENTRY' AND is_staff=0
              )
        """, (store_id, store_id)) as cur:
            row = await cur.fetchone()
            browsed = row[0] if row else 0

        # Stage 3: Reached billing queue
        async with db.execute("""
            SELECT COUNT(DISTINCT visitor_id) FROM events
            WHERE store_id=? AND event_type='BILLING_QUEUE_JOIN' AND is_staff=0
        """, (store_id,)) as cur:
            row = await cur.fetchone()
            queued = row[0] if row else 0

        # Stage 4: Completed purchase (proxy: in billing + did not abandon)
        async with db.execute("""
            SELECT COUNT(DISTINCT visitor_id) FROM events
            WHERE store_id=? AND event_type='BILLING_QUEUE_JOIN' AND is_staff=0
              AND visitor_id NOT IN (
                SELECT DISTINCT visitor_id FROM events
                WHERE store_id=? AND event_type='BILLING_QUEUE_ABANDON'
              )
        """, (store_id, store_id)) as cur:
            row = await cur.fetchone()
            purchased = row[0] if row else 0

    def drop(a, b):
        if a == 0:
            return 0.0
        return round((a - b) / a * 100, 1)

    return {
        "store_id": store_id,
        "funnel": [
            {
                "stage": "ENTRY",
                "label": "Walked in",
                "visitors": entered,
                "drop_off_pct": 0.0
            },
            {
                "stage": "ZONE_VISIT",
                "label": "Browsed products",
                "visitors": browsed,
                "drop_off_pct": drop(entered, browsed)
            },
            {
                "stage": "BILLING_QUEUE",
                "label": "Reached billing",
                "visitors": queued,
                "drop_off_pct": drop(browsed, queued)
            },
            {
                "stage": "PURCHASE",
                "label": "Completed purchase",
                "visitors": purchased,
                "drop_off_pct": drop(queued, purchased)
            }
        ],
        "overall_conversion_pct": round(purchased / entered * 100, 1) if entered > 0 else 0.0
    }

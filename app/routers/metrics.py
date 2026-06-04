from fastapi import APIRouter
from app.database import get_db
from app.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


def _store_filter(store_id: str):
    alt = store_id.replace("ST","store_") if store_id.startswith("ST") else "ST" + store_id.replace("store_", "")
    return store_id, alt


@router.get("/{store_id}/metrics")
async def get_metrics(store_id: str):
    sid, alt = _store_filter(store_id)
    async with await get_db() as db:

        async with db.execute("""
            SELECT COUNT(DISTINCT id_token) FROM entry_exit_events
            WHERE (store_id=? OR store_code=? OR store_id=? OR store_code=?)
              AND event_type='entry' AND is_staff=0
        """, (sid, sid, alt, alt)) as c:
            unique_visitors = (await c.fetchone())[0] or 0

        async with db.execute("""
            SELECT COUNT(DISTINCT track_id) FROM queue_events
            WHERE store_id=? OR store_id=?
        """, (sid, alt)) as c:
            billing_visitors = (await c.fetchone())[0] or 0

        async with db.execute("""
            SELECT COUNT(DISTINCT track_id) FROM queue_events
            WHERE (store_id=? OR store_id=?) AND abandoned=0
        """, (sid, alt)) as c:
            converted = (await c.fetchone())[0] or 0

        conversion_rate = round(converted / unique_visitors, 4) if unique_visitors > 0 else 0.0

        async with db.execute("""
            SELECT z1.zone_id, z1.zone_name,
                   AVG((julianday(z2.event_time) - julianday(z1.event_time)) * 86400) AS avg_dwell_s,
                   COUNT(*) as visits
            FROM zone_events z1
            JOIN zone_events z2 ON z1.track_id=z2.track_id AND z1.zone_id=z2.zone_id
            WHERE z1.event_type='zone_entered' AND z2.event_type='zone_exited'
              AND (z1.store_id=? OR z1.store_id=?)
            GROUP BY z1.zone_id
        """, (sid, alt)) as c:
            rows = await c.fetchall()
            avg_dwell_per_zone = {
                r[0]: {"zone_name": r[1],
                        "avg_dwell_s": round(r[2] or 0, 1),
                        "visit_count": r[3]}
                for r in rows
            }

        async with db.execute("""
            SELECT COUNT(*) FROM queue_events
            WHERE (store_id=? OR store_id=?) AND queue_exit_ts IS NULL
        """, (sid, alt)) as c:
            queue_depth = (await c.fetchone())[0] or 0

        async with db.execute("""
            SELECT COUNT(*) FROM queue_events
            WHERE (store_id=? OR store_id=?) AND abandoned=1
        """, (sid, alt)) as c:
            abandonments = (await c.fetchone())[0] or 0

        abandonment_rate = round(abandonments / billing_visitors, 4) if billing_visitors > 0 else 0.0

        async with db.execute("""
            SELECT MAX(event_timestamp) FROM entry_exit_events
            WHERE store_id=? OR store_code=? OR store_id=? OR store_code=?
        """, (sid, sid, alt, alt)) as c:
            last_event_at = (await c.fetchone())[0]

    logger.info(f"store={store_id} visitors={unique_visitors} conversion={conversion_rate}")
    return {
        "store_id":            store_id,
        "unique_visitors":     unique_visitors,
        "billing_visitors":    billing_visitors,
        "converted_visitors":  converted,
        "conversion_rate":     conversion_rate,
        "avg_dwell_per_zone":  avg_dwell_per_zone,
        "queue_depth":         queue_depth,
        "abandonment_rate":    abandonment_rate,
        "gender_breakdown":    {},
        "last_event_at":       last_event_at
    }

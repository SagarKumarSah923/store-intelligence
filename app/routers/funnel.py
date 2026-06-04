from fastapi import APIRouter
from app.database import get_db

router = APIRouter()


def _store_filter(store_id: str):
    alt = store_id.replace("ST","store_") if store_id.startswith("ST") else "ST"+store_id.replace("store_","")
    return store_id, alt


@router.get("/{store_id}/funnel")
async def get_funnel(store_id: str):
    sid, alt = _store_filter(store_id)

    async with await get_db() as db:
        async with db.execute("""
            SELECT COUNT(DISTINCT id_token) FROM entry_exit_events
            WHERE (store_id=? OR store_code=? OR store_id=? OR store_code=?)
              AND event_type='entry' AND is_staff=0
        """, (sid,sid,alt,alt)) as c:
            entered = (await c.fetchone())[0] or 0

        async with db.execute("""
            SELECT COUNT(DISTINCT track_id) FROM zone_events
            WHERE (store_id=? OR store_id=?) AND event_type='zone_entered'
              AND zone_type NOT IN ('BILLING','BOH')
        """, (sid,alt)) as c:
            zone_tracks = (await c.fetchone())[0] or 0
        browsed = min(zone_tracks, entered)

        async with db.execute("""
            SELECT COUNT(DISTINCT track_id) FROM queue_events
            WHERE store_id=? OR store_id=?
        """, (sid,alt)) as c:
            queued = min((await c.fetchone())[0] or 0, browsed)

        async with db.execute("""
            SELECT COUNT(DISTINCT track_id) FROM queue_events
            WHERE (store_id=? OR store_id=?) AND abandoned=0
        """, (sid,alt)) as c:
            purchased = min((await c.fetchone())[0] or 0, queued)

    def drop(a, b):
        return round((a-b)/a*100, 1) if a > 0 else 0.0

    return {
        "store_id": store_id,
        "funnel": [
            {"stage":"ENTRY",         "label":"Walked in",        "visitors":entered,   "drop_off_pct":0.0},
            {"stage":"ZONE_VISIT",    "label":"Browsed products", "visitors":browsed,   "drop_off_pct":drop(entered,browsed)},
            {"stage":"BILLING_QUEUE", "label":"Joined queue",     "visitors":queued,    "drop_off_pct":drop(browsed,queued)},
            {"stage":"PURCHASE",      "label":"Purchased",        "visitors":purchased, "drop_off_pct":drop(queued,purchased)},
        ],
        "overall_conversion_pct": round(purchased/entered*100,1) if entered>0 else 0.0
    }

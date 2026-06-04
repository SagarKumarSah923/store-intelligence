from fastapi import APIRouter
from app.database import get_db
from app.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


@router.get("/{store_id}/heatmap")
async def get_heatmap(store_id: str):
    async with await get_db() as db:
        async with db.execute("""
            SELECT zone_id,
                   COUNT(DISTINCT track_id)        AS visit_count,
                   AVG(CASE WHEN event_type='zone_exited' THEN (julianday(event_time) - julianday(event_time)) * 86400 END) AS avg_dwell_s
            FROM zone_events
            WHERE store_id=? AND event_type='zone_exited'
              AND zone_id IS NOT NULL
            GROUP BY zone_id
        """, (store_id,)) as cur:
            rows = await cur.fetchall()

        async with db.execute("""
            SELECT COUNT(DISTINCT id_token) FROM entry_exit_events
            WHERE store_id=? AND event_type='entry' AND is_staff=0
        """, (store_id,)) as cur:
            r = await cur.fetchone()
            total_sessions = r[0] if r else 0

    if not rows:
        return {
            "store_id": store_id,
            "zones": [],
            "data_confidence": "LOW",
            "total_sessions": total_sessions
        }

    max_visits = max((r[1] for r in rows), default=1)
    max_dwell  = max((r[2] or 0 for r in rows), default=1)

    zones = []
    for row in rows:
        zone_id, visits, avg_dwell = row
        avg_dwell = avg_dwell or 0
        zones.append({
            "zone_id": zone_id,
            "visit_count": visits,
            "avg_dwell_s": round(avg_dwell),
            "frequency_score": round(visits / max_visits * 100),
            "dwell_score": round(avg_dwell / max_dwell * 100) if max_dwell > 0 else 0,
            "heat_score": round((visits/max_visits*0.6 + avg_dwell/max_dwell*0.4) * 100)
        })

    zones.sort(key=lambda z: z["heat_score"], reverse=True)
    confidence = "HIGH" if total_sessions >= 20 else "LOW"

    return {
        "store_id": store_id,
        "zones": zones,
        "data_confidence": confidence,
        "total_sessions": total_sessions
    }

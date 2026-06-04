from fastapi import APIRouter
from datetime import datetime, timezone, timedelta
from app.database import get_db, check_db_health
from app.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

STALE_THRESHOLD_MINUTES = 10


@router.get("/health")
async def health_check():
    now = datetime.now(timezone.utc)
    db_status = await check_db_health()

    store_feeds = []
    if db_status["status"] == "ok":
        async with await get_db() as db:
            async with db.execute("""
                SELECT store_id, camera_id, MAX(event_timestamp) as last_event
                FROM entry_exit_events GROUP BY store_id, camera_id
            """) as cur:
                rows = await cur.fetchall()

        stale_cutoff = (now - timedelta(minutes=STALE_THRESHOLD_MINUTES)).isoformat()
        for store_id, camera_id, last_event in (rows or []):
            is_stale = last_event is None or last_event < stale_cutoff
            store_feeds.append({
                "store_id": store_id,
                "camera_id": camera_id,
                "last_event_at": last_event,
                "status": "STALE_FEED" if is_stale else "OK"
            })

    overall = "ok" if db_status["status"] == "ok" else "degraded"
    logger.info(f"event=health_check status={overall} feeds={len(store_feeds)}")

    return {
        "status": overall,
        "timestamp": now.isoformat(),
        "database": db_status,
        "store_feeds": store_feeds,
        "version": "1.0.0"
    }

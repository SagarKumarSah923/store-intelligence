from fastapi import APIRouter
from datetime import datetime, timezone, timedelta
from app.database import get_db
from app.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


def _resolve_store_ids(store_id: str) -> tuple[str, str]:
    if store_id.startswith("STORE_"):
        return store_id, store_id.replace("STORE_", "ST", 1)
    if store_id.startswith("ST"):
        return store_id, "STORE_" + store_id[2:]
    return store_id, store_id

QUEUE_SPIKE_THRESHOLD   = 5
DEAD_ZONE_MINUTES       = 30
STALE_FEED_MINUTES      = 10
CONVERSION_DROP_PCT     = 30.0


@router.get("/{store_id}/anomalies")
async def get_anomalies(store_id: str):
    now = datetime.now(timezone.utc)
    anomalies = []

    store_id_a, store_id_b = _resolve_store_ids(store_id)

    async with await get_db() as db:
        async with db.execute("""
            SELECT COUNT(DISTINCT track_id) FROM queue_events
            WHERE (store_id=? OR store_id=?) AND event_type='queue_completed'
              AND track_id NOT IN (
                SELECT DISTINCT track_id FROM queue_events
                WHERE (store_id=? OR store_id=?) AND event_type='queue_abandoned'
              )
        """, (store_id_a, store_id_b, store_id_a, store_id_b)) as cur:
            r = await cur.fetchone()
            queue_depth = r[0] if r else 0

        if queue_depth >= QUEUE_SPIKE_THRESHOLD:
            severity = "CRITICAL" if queue_depth > 8 else "WARN"
            anomalies.append({
                "anomaly_type": "BILLING_QUEUE_SPIKE",
                "severity": severity,
                "value": queue_depth,
                "threshold": QUEUE_SPIKE_THRESHOLD,
                "description": f"Billing queue has {queue_depth} customers waiting",
                "suggested_action": "Open additional billing counter or redirect customers to express checkout"
            })

        cutoff = (now - timedelta(minutes=DEAD_ZONE_MINUTES)).isoformat()
        async with db.execute("""
            SELECT DISTINCT zone_id FROM zone_events
            WHERE (store_id=? OR store_id=?) AND event_type='zone_entered'
              AND zone_id NOT IN ('BILLING','BOH')
        """, (store_id_a, store_id_b)) as cur:
            all_zones = {r[0] for r in await cur.fetchall()}

        async with db.execute("""
            SELECT DISTINCT zone_id FROM zone_events
            WHERE (store_id=? OR store_id=?) AND event_type='zone_entered'
              AND event_time >= ? AND zone_id NOT IN ('BILLING','BOH')
        """, (store_id_a, store_id_b, cutoff)) as cur:
            active_zones = {r[0] for r in await cur.fetchall()}

        async with db.execute("""
            SELECT camera_id, MAX(event_time) as last_seen FROM zone_events
            WHERE store_id=? OR store_id=? GROUP BY camera_id
        """, (store_id_a, store_id_b)) as cur:
            rows = await cur.fetchall()

        for dead_zone in (all_zones - active_zones):
            anomalies.append({
                "anomaly_type": "DEAD_ZONE",
                "severity": "WARN",
                "value": DEAD_ZONE_MINUTES,
                "zone_id": dead_zone,
                "description": f"Zone '{dead_zone}' has had no customer visits in {DEAD_ZONE_MINUTES} minutes",
                "suggested_action": f"Check product display in {dead_zone} zone or redirect staff to engage customers"
            })

        one_hr_ago    = (now - timedelta(hours=1)).isoformat()
        four_hrs_ago  = (now - timedelta(hours=4)).isoformat()

        async with db.execute("""
            SELECT
              SUM(CASE WHEN event_time >= ? THEN 1 ELSE 0 END)   AS recent_entries,
              SUM(CASE WHEN event_time <  ? THEN 1 ELSE 0 END)   AS baseline_entries
            FROM zone_events
            WHERE (store_id=? OR store_id=?) AND event_type='zone_entered'
        """, (one_hr_ago, one_hr_ago, store_id_a, store_id_b)) as cur:
            r = await cur.fetchone()
            recent_entries   = r[0] or 0
            baseline_entries = r[1] or 0

        async with db.execute("""
            SELECT
              SUM(CASE WHEN queue_served_ts >= ? THEN 1 ELSE 0 END)   AS recent_billing,
              SUM(CASE WHEN queue_served_ts <  ? THEN 1 ELSE 0 END)   AS baseline_billing
            FROM queue_events
            WHERE (store_id=? OR store_id=?) AND event_type='queue_completed'
        """, (one_hr_ago, one_hr_ago, store_id_a, store_id_b)) as cur:
            r = await cur.fetchone()
            recent_billing   = r[0] or 0
            baseline_billing = r[1] or 0

        def conv(entries, billing):
            return billing / entries if entries > 0 else None

        recent_rate   = conv(recent_entries, recent_billing)
        baseline_rate = conv(baseline_entries, baseline_billing)

        if recent_rate is not None and baseline_rate and baseline_rate > 0:
            drop_pct = (baseline_rate - recent_rate) / baseline_rate * 100
            if drop_pct >= CONVERSION_DROP_PCT:
                severity = "CRITICAL" if drop_pct >= 50 else "WARN"
                anomalies.append({
                    "anomaly_type": "CONVERSION_DROP",
                    "severity": severity,
                    "value": round(drop_pct, 1),
                    "threshold": CONVERSION_DROP_PCT,
                    "description": f"Conversion rate dropped {drop_pct:.1f}% vs recent average",
                    "suggested_action": "Review staffing levels, check if billing is operational, audit zone engagement"
                })

        stale_cutoff = (now - timedelta(minutes=STALE_FEED_MINUTES)).isoformat()
        async with db.execute("""
            SELECT camera_id, MAX(event_time) as last_seen FROM zone_events
            WHERE store_id=? OR store_id=? GROUP BY camera_id
        """, (store_id_a, store_id_b)) as cur:
            rows = await cur.fetchall()

        for cam_id, last_seen in (rows or []):
            if last_seen and last_seen < stale_cutoff:
                anomalies.append({
                    "anomaly_type": "STALE_FEED",
                    "severity": "CRITICAL",
                    "camera_id": cam_id,
                    "last_event_at": last_seen,
                    "description": f"No events from {cam_id} in last {STALE_FEED_MINUTES}+ minutes",
                    "suggested_action": f"Check camera {cam_id} connectivity and pipeline health"
                })

    logger.info(f"store={store_id} anomalies_found={len(anomalies)}")
    return {
        "store_id": store_id,
        "checked_at": now.isoformat(),
        "anomaly_count": len(anomalies),
        "anomalies": anomalies
    }

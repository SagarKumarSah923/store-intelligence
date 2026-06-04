"""
ingest_pos.py - Load official pos_transactions.csv into the database.
Maps order_date + order_time ? combined timestamp.
"""

import csv, asyncio, aiosqlite


async def load_pos(csv_path: str, db_path: str = "/tmp/store_intel_v2.db"):
    async with aiosqlite.connect(db_path) as db:
        with open(csv_path) as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            try:
                await db.execute("""
                    INSERT OR IGNORE INTO pos_transactions
                    (order_id, store_id, order_date, order_time, product_id, brand_name, total_amount)
                    VALUES (?,?,?,?,?,?,?)
                """, (r['order_id'], r['store_id'], r['order_date'], r['order_time'],
                      r.get('product_id'), r.get('brand_name'),
                      float(r.get('total_amount',0) or 0)))
            except Exception as e:
                print(f"Skip row {r.get('order_id')} : {e}")
        await db.commit()
        async with db.execute("SELECT COUNT(*) FROM pos_transactions") as c:
            n = (await c.fetchone())[0]
        print(f"? POS loaded: {n} records in DB")


if __name__ == "__main__":
    import sys
    csv_file = sys.argv[1] if len(sys.argv) > 1 else "pos_transactions.csv"
    asyncio.run(load_pos(csv_file))

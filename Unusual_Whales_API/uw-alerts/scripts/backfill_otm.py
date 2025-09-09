# apps/api/scripts/backfill_otm.py
from __future__ import annotations
import time
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

# 复用你现有的 engine
from apps.api.app.db.engine import engine

BATCH = 2000                # 每批处理多少条（可按需调小以减少锁冲突）
SLEEP_BETWEEN_BATCH = 0.1   # 每批之间小睡让路给轮询（可调为 0）

def ensure_sqlite_runtime():
    # 打开 WAL + 设置 busy_timeout，降低并发写冲突
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL;"))
        conn.execute(text("PRAGMA busy_timeout=10000;"))

def ensure_otm_column():
    # 如无该列则在线加列并建索引（幂等）
    with engine.begin() as conn:
        cols = conn.execute(text("PRAGMA table_info('metrics_greeks');")).fetchall()
        names = {row[1] for row in cols}  # row[1] = column name
        if 'otm_pct' not in names:
            conn.execute(text("ALTER TABLE metrics_greeks ADD COLUMN otm_pct REAL;"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_metrics_greeks_otm_pct ON metrics_greeks(otm_pct);"))

def _compute_otm_pct(spot, strike, side):
    # 无符号 OTM%（0.12 = 12% OTM）
    if not spot or spot == 0 or strike is None or not side:
        return None
    if str(side).upper() == 'C':
        return (float(strike) - float(spot)) / float(spot)
    return (float(spot) - float(strike)) / float(spot)

def count_missing() -> int:
    with engine.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM metrics_greeks WHERE otm_pct IS NULL")).scalar_one()
        return int(n)

def backfill_otm(batch=BATCH, sleep_between_batches=SLEEP_BETWEEN_BATCH):
    ensure_sqlite_runtime()
    ensure_otm_column()

    total = count_missing()
    print(f"[OTM] rows to backfill: {total}")

    updated = 0
    last_id = ""  # 基于 alert_id 的游标分页

    with Session(engine) as s:
        while True:
            ids = s.execute(text("""
                SELECT g.alert_id
                FROM metrics_greeks g
                LEFT JOIN metrics_price p ON p.alert_id = g.alert_id
                WHERE g.otm_pct IS NULL AND g.alert_id > :last
                ORDER BY g.alert_id
                LIMIT :lim
            """), {"last": last_id, "lim": batch}).fetchall()

            if not ids:
                break

            for (aid,) in ids:
                row = s.execute(text("""
                    SELECT g.strike, g.side, p.stock_close
                    FROM metrics_greeks g
                    JOIN metrics_price p ON p.alert_id = g.alert_id
                    WHERE g.alert_id = :a
                """), {"a": aid}).first()

                last_id = aid
                if not row:
                    continue

                strike, side, spot = row
                otm = _compute_otm_pct(spot, strike, side)

                # 有写锁冲突则退避重试
                for attempt in range(5):
                    try:
                        s.execute(
                            text("UPDATE metrics_greeks SET otm_pct = :v WHERE alert_id = :a"),
                            {"v": otm, "a": aid}
                        )
                        updated += 1
                        break
                    except OperationalError as e:
                        if "database is locked" in str(e).lower() and attempt < 4:
                            time.sleep(0.25 * (2 ** attempt))  # 0.25s, 0.5s, 1s, 2s
                            continue
                        raise

            s.commit()
            print(f"[OTM] progress: {updated}/{total} (+{len(ids)} this batch)")
            if sleep_between_batches:
                time.sleep(sleep_between_batches)

    left = count_missing()
    print(f"[OTM] done. updated={updated}, remaining={left}")

if __name__ == "__main__":
    backfill_otm()

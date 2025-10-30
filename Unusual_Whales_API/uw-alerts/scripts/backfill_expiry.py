# apps/api/scripts/backfill_expiry.py
from __future__ import annotations
import time
from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

# 复用你现有的 engine
from apps.api.app.db.engine import engine

# 批量与节流参数
BATCH = 2000                 # 每批处理多少条
SLEEP_BETWEEN_BATCH = 0.1    # 每批之间小睡(秒)，降低与在线写入的冲突

# ========= SQLite 运行期保障 =========
def ensure_sqlite_runtime():
    # 开 WAL、设置 busy_timeout，降低并发冲突
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL;"))
        conn.execute(text("PRAGMA busy_timeout=10000;"))
        # 可选：稍降落盘严格度，减少锁持续时间
        conn.execute(text("PRAGMA synchronous=NORMAL;"))

# ========= DDL：幂等添加列与索引 =========
def ensure_expiry_column():
    with engine.begin() as conn:
        cols = conn.execute(text("PRAGMA table_info('metrics_greeks');")).fetchall()
        names = {row[1] for row in cols}  # row[1] 是列名
        if 'expiry' not in names:
            # 存文本 ISO 日期；SQLite 无原生 DATE 类型，用 TEXT 最稳
            conn.execute(text("ALTER TABLE metrics_greeks ADD COLUMN expiry TEXT;"))
        # 给常用筛选加索引（幂等）
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_metrics_greeks_expiry ON metrics_greeks(expiry);"))

# ========= 解析 OCC option symbol =========
@dataclass
class OptionParts:
    underlying: str
    expiry: str       # YYYY-MM-DD
    right: str        # 'C' or 'P'
    strike: float     # e.g. 260.0

def parse_option_symbol(s: str) -> OptionParts:
    """
    解析形如 'TSLA251003P00260000' 的 OCC 代码：
    [underlying][YYMMDD][C|P][strike(8)]
    - strike 八位，后三位为小数
    - underlying 为前缀剩余部分
    """
    if not s or len(s) < 16:
        raise ValueError(f"option symbol too short: {s!r}")

    tail = s[-15:]          # YYMMDD + C/P + 8位strike
    underlying = s[:-15]

    yy = int(tail[0:2])
    mm = int(tail[2:4])
    dd = int(tail[4:6])
    right = tail[6].upper()
    if right not in ('C', 'P'):
        raise ValueError(f"invalid right in {s!r}")

    strike_raw = tail[7:]
    if not strike_raw.isdigit() or len(strike_raw) != 8:
        raise ValueError(f"invalid strike digits in {s!r}")
    strike = int(strike_raw) / 1000.0

    yyyy = 2000 + yy
    expiry_iso = date(yyyy, mm, dd).isoformat()

    return OptionParts(
        underlying=underlying,
        expiry=expiry_iso,
        right=right,
        strike=strike,
    )

# ========= 统计剩余未回填 =========
def count_missing() -> int:
    with engine.connect() as conn:
        n = conn.execute(text("SELECT COUNT(*) FROM metrics_greeks WHERE expiry IS NULL")).scalar_one()
        return int(n)

# ========= 主回填流程 =========
def backfill_expiry(batch=BATCH, sleep_between_batches=SLEEP_BETWEEN_BATCH):
    ensure_sqlite_runtime()
    ensure_expiry_column()

    total = count_missing()
    print(f"[EXPIRY] rows to backfill: {total}")

    updated = 0
    skipped = 0
    last_id = ""  # 基于 alert_id 的游标分页；要求 alert_id 可按字典序稳定递增

    with Session(engine) as s:
        while True:
            ids = s.execute(text("""
                SELECT alert_id, option_symbol
                FROM metrics_greeks
                WHERE expiry IS NULL AND alert_id > :last
                ORDER BY alert_id
                LIMIT :lim
            """), {"last": last_id, "lim": batch}).fetchall()

            if not ids:
                break

            for aid, optsym in ids:
                last_id = aid
                if not optsym:
                    skipped += 1
                    continue

                try:
                    parts = parse_option_symbol(optsym)
                    expiry = parts.expiry
                except Exception:
                    # 解析失败跳过；必要时可额外记录到一张 error 表
                    skipped += 1
                    continue

                # 写入，处理写锁冲突
                for attempt in range(5):
                    try:
                        s.execute(
                            text("UPDATE metrics_greeks SET expiry = :e WHERE alert_id = :a"),
                            {"e": expiry, "a": aid}
                        )
                        updated += 1
                        break
                    except OperationalError as e:
                        if "database is locked" in str(e).lower() and attempt < 4:
                            time.sleep(0.25 * (2 ** attempt))  # 退避：0.25s,0.5s,1s,2s
                            continue
                        raise

            s.commit()
            print(f"[EXPIRY] progress: {updated}/{total} (+{len(ids)} this batch, skipped={skipped})")
            if sleep_between_batches:
                time.sleep(sleep_between_batches)

    left = count_missing()
    print(f"[EXPIRY] done. updated={updated}, skipped={skipped}, remaining={left}")

if __name__ == "__main__":
    backfill_expiry()

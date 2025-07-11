#!/usr/bin/env python
"""
增量下载期权数据 → Parquet（已自动展开分片）
用法:
  python download_options.py --tickers AAPL MSFT --start 2025-04-01 --end 2025-05-01
  新增 ticker: 
    python download_options.py --ticker TSLA --start 2025-04-01 --end 2025-05-01
  每日更新: 
    python download_options.py 
"""
import os, json, argparse, asyncio, datetime as dt, gzip, io
from pathlib import Path
from typing import List, Any

import aiohttp, pandas as pd
from dotenv import load_dotenv
from aiolimiter import AsyncLimiter
from tenacity import (
    retry, retry_if_exception, stop_after_attempt,
    wait_exponential,
)
from aiohttp import ClientResponseError
from rich.progress import (
    Progress, BarColumn, TextColumn, SpinnerColumn,
    TimeElapsedColumn, TimeRemainingColumn,
)

# ╭──────────────── 常量 ───────────────╮
BASE_DIR   = Path(__file__).resolve().parent
DATA_DIR   = BASE_DIR / "Data/raw/options"
META_FILE  = BASE_DIR / "Meta/last_fetched.json"
load_dotenv()
API_KEY    = os.getenv("IVOL_API_KEY", "")
# 免费额度示例：2 req/sec，120 req/min
limiter_sec = AsyncLimiter(2, 1)
limiter_min = AsyncLimiter(120, 60)
HEADERS     = {"User-Agent": "SingularSquare/OptionTracker/1.0"}
# ╰────────────────────────────────────╯


# ── 元数据 ───────────────────────────────────────────────────────
def load_meta() -> dict[str, dict[str,str]]:
    if META_FILE.exists():
        return json.loads(META_FILE.read_text())
    META_FILE.parent.mkdir(parents=True, exist_ok=True)
    return {}

def save_meta(meta: dict[str, dict[str,str]]):
    META_FILE.write_text(json.dumps(meta, indent=2))

# ── 带限速 + 重试的请求封装 ──────────────────────────────────────
@retry(
    retry=retry_if_exception(lambda e: isinstance(e, ClientResponseError) and e.status in (429, 503)),
    wait=wait_exponential(multiplier=2, min=1, max=8),
    stop=stop_after_attempt(3),
    reraise=True,
)
async def call_api(session: aiohttp.ClientSession, url: str, params: dict | None = None) -> Any:
    async with limiter_sec, limiter_min:
        async with session.get(url, params=params, timeout=30, headers=HEADERS) as r:
            if r.status in (429, 503):
                raise ClientResponseError(
                    request_info=r.request_info,
                    history=r.history,
                    status=r.status,
                    message="Rate limited",
                    headers=r.headers,
                )
            r.raise_for_status()
            # 部分文件分片直接返回 gzip bytes
            if r.headers.get("Content-Type", "").startswith("application/gzip"):
                return await r.read()
            # 其它情况默认 json
            return await r.json()


# ── 下载并展开单个 gzip 分片 ────────────────────────────────────
async def download_part(session: aiohttp.ClientSession, url: str) -> pd.DataFrame | None:
    try:
        # 不走 call_api，避免 JSON 解析；直接拿 bytes
        async with limiter_sec, limiter_min:          # 仍然限速
            async with session.get(url, timeout=30, headers=HEADERS) as r:
                r.raise_for_status()
                gz_bytes = await r.read()

        buf = gzip.decompress(gz_bytes)
        df = pd.read_csv(
            io.BytesIO(buf),
            dtype={"option_symbol": "string"},        # 避免变 float
            low_memory=False,
        )
        return df

    except Exception as e:
        print(f"⚠️ 下载/解析 {url} 失败: {e}")
        return None


# ── 单日+CP 抓取 ────────────────────────────────────────────────
async def fetch_one(session, symbol, trade_date, cp) -> pd.DataFrame | None:
    base_url = "https://restapi.ivolatility.com/equities/eod/stock-opts-by-param"
    params = dict(
      apiKey=API_KEY, symbol=symbol,
      tradeDate=trade_date.strftime("%Y-%m-%d"),
      cp=cp, dteFrom=0, dteTo=700,
      moneynessFrom=-100, moneynessTo=100,
      region="USA",
    )

    # ① initial request
    js = await call_api(session, base_url, params=params)

    # —— INLINE ROWS? ——  
    # 如果接口直接返回了一个 list，且每个元素都有 c_date（或其它字段），
    # 那就说明这就是完整的数据，直接转成 DataFrame 返回。
    if isinstance(js, list) and js and "c_date" in js[0]:
        df_inline = pd.DataFrame(js)
        df_inline["tradeDate"] = trade_date
        df_inline["symbol"]    = symbol
        return df_inline

    detail = js.get("status", {}).get("urlForDetails") if isinstance(js, dict) else None
    if detail:
        MAX_TRIES, SLEEP_SEC = 20, 3
        for i in range(MAX_TRIES):
            await asyncio.sleep(SLEEP_SEC)
            js2 = await call_api(session, detail)
            # code = js2.get("status", {}).get("code") if isinstance(js2, dict) else None
            # print(f"DEBUG poll {symbol} {trade_date} [{i+1}/{MAX_TRIES}] → code={code}")
            if isinstance(js2, list) or (isinstance(js2, dict) and js2.get("data")):
                js = js2
                break
        else:
            print(f"⚠️ {symbol} {trade_date} timed out after {MAX_TRIES} tries")
            return None

    # ③ NOW: extract the actual CSV parts
    # if js is list of parts, use it; if dict, pull js["data"]
    if isinstance(js, dict):
        data = js.get("data")
        if isinstance(data, list) and data and "c_date" in data[0]:
            df = pd.DataFrame(data)
            df["tradeDate"] = trade_date
            df["symbol"]    = symbol
            return df

    # ④ Otherwise, JS must be part-descriptor list or dict-with-data   
    if isinstance(js, list):
        parts = js
    else:
        data = js.get("data", [])
        parts = json.loads(data) if isinstance(data, str) else data

    # print("DEBUG parts:", parts[:3])

    # build URLs, then download/decompress each one
    def _get_signed_url(p):
        if not isinstance(p, dict):
            return None
        for key in ("url", "fileUrl", "downloadUrl", "urlForDownload"):
            if p.get(key):
                return p[key]
        data_list = p.get("data")
        if isinstance(data_list, list):
            for entry in data_list:
                for key in ("url", "fileUrl", "downloadUrl", "urlForDownload"):
                    if entry.get(key):
                        return entry.get(key)
        return None

    file_urls = [u for u in (_get_signed_url(p) for p in parts) if u]
    if not file_urls:
        return None

    dfs = await asyncio.gather(*(download_part(session, u) for u in file_urls))
    dfs = [d for d in dfs if d is not None and not d.empty]
    if not dfs:
        return None

    # ④ concat all CSV rows and return
    big = pd.concat(dfs, ignore_index=True)
    big["tradeDate"] = trade_date
    big["symbol"]    = symbol
    return big


# ── 抓取单 Symbol （带进度条）────────────────────────────────────
async def fetch_symbol(
    symbol: str,
    start: dt.date,
    end: dt.date,
    progress: Progress,
    task_id: int,
    cps: tuple[str, ...] = ("C", "P"),
):
    dates = pd.bdate_range(start, end)
    async with aiohttp.ClientSession() as sess:

        async def one_call(d, cp):
            # print(f"→ [{symbol}] {d.date()} {cp}")
            try:
                df = await fetch_one(sess, symbol, d.date(), cp)
                if df is None or df.empty:
                    print(f"   × 无数据或解析失败：{symbol} {d.date()} {cp}")
                    return
                fname = DATA_DIR / f"symbol={symbol}" / f"year={d.year}" / f"{d:%Y-%m-%d}_{cp}.parquet"
                fname.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(fname, index=False, compression="snappy")
                # print(f"   ✓ wrote {len(df)} rows to {fname}")
            except Exception as e:
                print(f"⚠️ {symbol} {d.date()} {cp} -> {e!r}")
            finally:
                progress.advance(task_id)

        chunk = 5  # 每批 5 个交易日并发
        for i in range(0, len(dates), chunk):
            sub = dates[i : i + chunk]
            await asyncio.gather(*(one_call(d, cp) for d in sub for cp in cps))
            await asyncio.sleep(5)  # 批次冷却


# ── CLI 入口 ────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--tickers", nargs="+", default=None,
        help=(
            "要抓取的标的列表（可选）；\n"
            "• 如指定，则同时更新 Meta 中已有的所有 ticker，以及新输入的 ticker；\n"
            "• 如不指定，则仅更新 Meta 中已有的 ticker。"
        )
    )
    ap.add_argument(
        "--start", default=None,
        help="新 ticker 的起始下载日期 (YYYY-MM-DD)，当使用 --tickers 且有新 ticker 时必填"
    )
    ap.add_argument(
        "--end", default=(dt.date.today() - dt.timedelta(days=1)).isoformat(),
        help="下载的结束日期 (YYYY-MM-DD)，默认今天"
    )
    args = ap.parse_args()

    # 如果指定了 --tickers，就必须提供 --start
    if args.tickers and not args.start:
        ap.error("当使用 --tickers 时，必须通过 --start 指定起始日期")

    original_end = pd.to_datetime(args.end).date()
    user_start = pd.to_datetime(args.start).date() if args.start else None
    meta       = load_meta()

    # 组装需要更新的 ticker 列表

    # 1) 确定要处理的所有 ticker
    if args.tickers:
        input_set = {t.upper() for t in args.tickers}
        all_tickers = sorted(meta.keys() | input_set)
    else:
        all_tickers = sorted(meta.keys())
        if not all_tickers:
            ap.error("Meta 为空，请至少通过 --tickers 指定一个标的并提供 --start")

    progress = Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("{task.description}", justify="right"),
        BarColumn(bar_width=None, style="green"),
        "[progress.percentage]{task.percentage:>3.0f}%",
        "•",
        "{task.completed}/{task.total}",
        TimeElapsedColumn(),
        TimeRemainingColumn(compact=True),
    )

    async def runner(tasks: List[asyncio.Task]):
        with progress:
            await asyncio.gather(*tasks)

    jobs: List[tuple[str, dt.date, dt.date, int]] = []
    meta_updates: list[tuple[str, str|None, str|None]] = []

    for sym in all_tickers:
        was_known = sym in meta

        if not was_known:
            if user_start is None:
                ap.error(f"新 ticker {sym} 必须提供 --start")
            # 新 ticker：first 从 user_start 开始，last 设为 user_start 前一天
            meta[sym] = {
                "first": user_start.isoformat(),
                "last":  (user_start - dt.timedelta(days=1)).isoformat()
            }

        rec   = meta[sym]  
        first = pd.to_datetime(rec["first"]).date()
        last  = pd.to_datetime(rec["last"]).date()
        
        if args.tickers:
            # —— 情况 A：用户指定了 tickers 且提供了 start —— 
            # 新 ticker 用 user_start；旧 ticker 用 meta+1；end 一律用 args.end
            if was_known:
                start_date = last + dt.timedelta(days=1)
            
            else:
                start_date = user_start  # args.start 已保证非 None

            end_date = original_end
            new_first = None
            new_last = original_end.isoformat()

        else:
            # —— 情况 B：未指定tickers 但提供了start —— 回填模式 ——
            # 所有 ticker 使用 user_start；end_date 取决于 每一个 ticker 的 first 日期
            if args.start and not args.tickers:
                start_date = user_start
                end_date = min(first - dt.timedelta(days=1), original_end)
                new_first = min(first, start_date).isoformat()
                new_last = None 

            else:
                # —— 情况 C：未指定 tickers 也未指定 start —— 增量更新 —— 
                # 从 meta+1 到 end_date 拉取，并更新 meta
                start_date = last + dt.timedelta(days=1)
                end_date = original_end
                new_first = None
                new_last = max(last, end_date).isoformat()

        if start_date > end_date:
            print(f"{sym} 已最新（{start_date} > {end_date}），跳过")
            continue

        total = len(pd.bdate_range(start_date, end_date)) * 2  # 2 CPs
        tid = progress.add_task(f"[cyan]{sym}", total=total)

        jobs.append(
            fetch_symbol(sym, start_date, end_date, progress=progress, task_id=tid)
        )
        meta_updates.append((sym, new_first, new_last))

    if jobs:
        asyncio.run(runner(jobs))

        for sym, nf, nl in meta_updates:
            if nf is not None:
                meta[sym]["first"] = nf
            if nl is not None:
                meta[sym]["last"] = nl

        save_meta(meta)
        print("✅ All done")
    else:
        print("🎉 没有需要抓取的任务")


if __name__ == "__main__":
    main()

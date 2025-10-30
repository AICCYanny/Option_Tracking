import time
import heapq
from collections import deque
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional, Set, Deque, Dict, Any, List, Tuple
from itertools import count

from apps.api.app.config import settings
from apps.api.app.clients.uw_client import UWClient
from apps.api.app.db.engine import engine, SessionLocal
from apps.api.app.db.models import Base
from apps.api.app import crud
from apps.api.app.bucket_math import bucket_for_alert_iso
from apps.api.app.utils.occ import parse_option_symbol, get_greeks

import threading

Base.metadata.create_all(bind=engine)

def _parse_iso(ts: str) -> Optional[datetime]:
    try:
        if ts.endswith('Z'):
            ts = ts.replace('Z', '+00:00')
        return datetime.fromisoformat(ts)
    except Exception:
        return None
    
def is_fresh(t_alert: str, grace_sec: int) -> bool:
    dt = _parse_iso(t_alert)
    if dt is None:
        return False
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() <= grace_sec

def _compute_otm_pct(spot: float | None, strike: float | None, side: str | None) -> float | None:
    if not spot or spot == 0 or strike is None or not side:
        return None
    if side.upper() == 'C':
        return (strike - spot) / spot
    return (spot - strike) / spot

def compute_bucket_agg_from_intraday(intr: Any, start_iso_utc: str, end_iso_utc: str) -> dict:
    """
    Calculate aggregated volume and premium for the time bucket.
    """
    rows = intr.get('data') if isinstance(intr, dict) else []

    def parse_ts(x: Any) -> Optional[datetime]:
        if not isinstance(x, str):
            return None
        s = x.replace("Z", "+00:00") if x.endswith("Z") else x
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
        
    def to_int(x) -> int:
        try: return int(x)
        except Exception: return 0

    def to_float(x) -> float:
        try: return float(x)
        except Exception: return 0.0

    def safe_div(n, d) -> float:
        return n / d if d else 0.0
        
    start_dt = _parse_iso(start_iso_utc)
    end_dt = _parse_iso(end_iso_utc)

    sides = ('ask', 'bid', 'mid', 'no')
    vol_totals  = {k: 0   for k in sides}
    prem_totals = {k: 0.0 for k in sides}

    total_vol_multi = 0
    iv_low_sum  = 0.0
    iv_high_sum = 0.0
    minutes = 0

    for r in rows:
        if not isinstance(r, dict):
            continue
        dt = parse_ts(r.get('start_time'))
        if dt is None or start_dt is None or end_dt is None or not (start_dt <= dt < end_dt):
            continue

        for k in sides:
            vol_totals[k] += to_int(r.get(f"volume_{k}_side"))
            prem_totals[k] += to_float(r.get(f"premium_{k}_side"))

        total_vol_multi += to_int(r.get('volume_multi'))
        iv_low_sum  += to_float(r.get('iv_low'))
        iv_high_sum += to_float(r.get('iv_high'))
        minutes += 1

    total_vol = sum(vol_totals.values())
    total_premium = sum(prem_totals.values())

    avg_prices = {k: safe_div(prem_totals[k], vol_totals[k]) / 100 for k in sides}

    result = {
        'bucket_minutes': minutes,
        # avg prices
        **{f'avg_price_{k}': avg_prices[k] for k in sides}, 
        'avg_price': safe_div(total_premium, total_vol) / 100,
        # iv
        'avg_iv_low':  safe_div(iv_low_sum, minutes),
        'avg_iv_high': safe_div(iv_high_sum, minutes),
        # volumes
        **{f'volume_{k}': vol_totals[k] for k in sides},
        'volume_multi': total_vol_multi,
        'total_volume': total_vol,
        'bucket_multi_ratio': safe_div(total_vol_multi, total_vol),
        # premiums
        **{f'premium_{k}': prem_totals[k] for k in sides},
        'total_premium': total_premium,
    }
    return result

# ---------- Simple timer: min-heap ----------
BucketJob = Dict[str, Any]
_job_seq = count()

def schedule_bucket_job(heap: List[Tuple[float, BucketJob]], job: BucketJob, run_at_epoch: float):
    heapq.heappush(heap, (run_at_epoch, next(_job_seq), job))

def run_due_bucket_jobs(client: UWClient, heap: List[Tuple[float, BucketJob]], now_epoch: float):
    due: List[BucketJob] = []
    while heap and heap[0][0] <= now_epoch:
        _, _, job = heapq.heappop(heap)
        due.append(job)

    executed = len(due)
    next_at = heap[0][0] if heap else None
    if next_at is not None:
        next_utc = datetime.fromtimestamp(next_at, tz=timezone.utc)
        next_et = next_utc.astimezone(ZoneInfo("America/New_York"))
    else:
        next_et = '-'
    if not executed:
        print(f"Executed: 0, Remaining: {len(heap)}, Next_at: {next_et}.")
        return 
    
    with SessionLocal() as session:
        for job in due:
            aid = job['alert_id']
            opt = job.get('option_symbol')
            s = job['start_utc']
            e = job['end_utc']

            if not opt:
                # No symbol, skip
                print(f"[BUCKET] {aid} skipped (no option_symbol)")
                continue

            try:
                intr = client.fetch_option_intraday(opt)
                agg = compute_bucket_agg_from_intraday(intr, s, e)
                crud.save_bucket_metrics(session, 
                                         alert_id=aid, 
                                         bucket_start_iso_utc=s,
                                         bucket_end_iso_utc=e,
                                         bucket_minutes=agg['bucket_minutes'],
                                         option_symbol=opt,
                                         avg_price_ask=agg['avg_price_ask'],
                                         avg_price_bid=agg['avg_price_bid'],
                                         avg_price_mid=agg['avg_price_mid'],
                                         avg_price_no=agg['avg_price_no'],
                                         avg_price=agg['avg_price'],
                                         avg_iv_low=agg['avg_iv_low'],
                                         avg_iv_high=agg['avg_iv_high'],
                                         volume_ask=agg['volume_ask'],
                                         volume_bid=agg['volume_bid'],
                                         volume_mid=agg['volume_mid'],
                                         volume_no=agg['volume_no'],
                                         volume_multi=agg['volume_multi'],
                                         total_volume=agg['total_volume'],
                                         bucket_multi_ratio=agg['bucket_multi_ratio'],
                                         premium_ask=agg['premium_ask'],
                                         premium_bid=agg['premium_bid'],
                                         premium_mid=agg['premium_mid'],
                                         premium_no=agg['premium_no'],
                                         total_premium=agg['total_premium'],
                                         data=agg)
                print(f"[BUCKET] {aid} done ({opt}) vol={agg['total_volume']} prem={agg['total_premium']}")
            except Exception as ex:
                crud.save_bucket_metrics(session,
                                         alert_id=aid,
                                         bucket_start_iso_utc=s,
                                         bucket_end_iso_utc=e,
                                         data={
                                             'error': f"{ex}",
                                             'start_utc': s,
                                             'end_utc': e,
                                         })
                print(f"[BUCKET][ERR] {aid} {ex}")
        session.commit()

    print(f"Executed: {executed}, Remaining: {len(heap)}, Next_at: {next_et}.")
    return

def run_loop(stop_event: threading.Event | None = None):
    client = UWClient()

    # Pending query + enqueueing
    pending: Deque[Dict[str, Any]] = deque()
    enqueued_ids: Set[str] = set()
    bucket_jobs_heap: List[Tuple[float, BucketJob]] = []

    warm_started = False
    print(f"[{datetime.now(timezone.utc).isoformat()}] start polling every {settings.poll_interval_sec}s ...")

    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                print("[Poller] stop signal received.")
                break

            cycle_start = time.monotonic()
            # Fetch latest alerts on page 0
            try:
                data = client.fetch_alerts(
                    page=0,
                    limit=settings.alerts_limit,
                    intraday_only=settings.alerts_intraday_only,
                    # config_ids=settings.alerts_config_ids,
                )

            except Exception as e:
                print(f"[ERR] fetch_alerts: {e}")
                # Try to process existing alerts even if fail
                _process_queue(pending, max_n=settings.max_process_per_cycle)
                run_due_bucket_jobs(client, bucket_jobs_heap, time.time())
                time.sleep(settings.poll_interval_sec)
                continue

            items = data.get('data')
            if not isinstance(items, list):
                items = []
            
            # Initialize once: avoid backfill trigger 429
            if not warm_started:
                warm_started = True
                print(f"[warm-start] skip first cycle; queue stays empty.")
                time.sleep(settings.poll_interval_sec)
                continue

            # Enqueueing: enqueue only fresh and not enqueued alerts
            newly_found = []
            for it in items:
                aid = str(it.get('id'))
                if not aid or aid in enqueued_ids:
                    continue
                enqueued_ids.add(aid) 
                it['__id'] = aid
                newly_found.append(it)

            if newly_found:
                batch_new: list[dict] = []
                batch_seen: set[str] = set()

                # Untracable endpoints: trigger immediately before enqueueing (fresh)
                with SessionLocal() as session:
                    for it in newly_found:
                        aid = it['__id']
                        t_alert = it.get('created_at')
                        m = it.get('meta')
                        symbol = m.get('underlying_symbol')
                        option_symbol = it.get('symbol')
                        ask_volume = int(m.get('ask_volume'))
                        bid_volume = int(m.get('bid_volume'))
                        volume = int(m.get('volume'))
                        avg_fill = float(m.get('avg_fill'))
                        close = float(m.get('close'))
                        diff = float(m.get('diff'))
                        total_premium = float(m.get('total_premium'))
                        iv_change = float(m.get('iv_change'))
                        open_interest = int(m.get('open_interest'))
                        vol_oi_ratio = float(m.get('vol_oi_ratio'))
                        multi_leg_vol_ratio = float(m.get('multi_leg_vol_ratio'))

                        canonical_id = crud.upsert_alert(
                            session,
                            alert_id=aid,
                            created_at=str(t_alert),
                            symbol=symbol,
                            option_symbol=option_symbol,
                            ask_volume=ask_volume,
                            bid_volume=bid_volume,
                            volume=volume,
                            avg_fill=avg_fill,
                            close=close,
                            diff=diff,
                            total_premium=total_premium,
                            iv_change=iv_change,
                            open_interest=open_interest,
                            vol_oi_ratio=vol_oi_ratio,
                            multi_leg_vol_ratio=multi_leg_vol_ratio,
                            raw_obj=it
                        )

                        if canonical_id in batch_seen:
                            print(f"  - duplicate (same day+contract); skip enqueue/schedule, id={canonical_id}.")
                            continue
                        batch_seen.add(canonical_id)

                        print(f"[NEW] {t_alert} {symbol} {option_symbol} id={canonical_id}")

                        # Derive expiry from option symbol
                        derived_expiry = None
                        derived_strike = None
                        derived_side = None
                        if option_symbol:
                            try:
                                parts = parse_option_symbol(option_symbol)
                                derived_expiry = parts.expiry
                                derived_strike = parts.strike
                                derived_side = parts.right
                                print(f"  - parsed from {option_symbol}: {parts}")
                            except Exception as pe:
                                print(f"  - parse option_symbol failed: {pe}")

                        if symbol and is_fresh(str(t_alert), settings.live_alert_grace_sec):
                            g_expiry = derived_expiry
                            g_strike = derived_strike
                            g_side = derived_side
                            if g_expiry and g_strike:
                                try:
                                    greeks = client.fetch_stock_greeks(symbol, expiry=g_expiry)
                                    greek = get_greeks(greeks, g_strike, g_side)
                                    price = client.fetch_stock_state(symbol)['data']
                                    stock_market_time = price['market_time']
                                    stock_close = float(price['close'])
                                    stock_previous_close = float(price['prev_close'])
                                    stock_volume = int(price['volume'])
                                    stock_total_volume = int(price['total_volume'])
                                    otm_pct = _compute_otm_pct(stock_close, g_strike, g_side)
                                    crud.save_greeks_snapshot(session, 
                                                              alert_id=canonical_id,
                                                              option_symbol=greek.option_symbol,
                                                              side=greek.side,
                                                              dte=greek.dte,
                                                              strike=greek.strike,
                                                              delta=greek.delta,
                                                              gamma=greek.gamma,
                                                              theta=greek.theta,
                                                              rho=greek.rho,
                                                              vega=greek.vega,
                                                              vanna=greek.vanna,
                                                              charm=greek.charm,
                                                              volatility=greek.volatility, 
                                                              otm_pct=otm_pct,
                                                              expiry=g_expiry,
                                                              data=greeks)
                                    crud.save_price_snapshot(session, 
                                                             alert_id=canonical_id, 
                                                             market_time=stock_market_time,
                                                             stock_close=stock_close,
                                                             stock_previous_close=stock_previous_close,
                                                             stock_volume=stock_volume,
                                                             stock_total_volume=stock_total_volume,
                                                             data=price)
                                    print(f"  - greeks ok ({symbol}); stock-state ok (snapshot saved)")
                                except Exception as ge:
                                    print(f"  [ERR] greeks/state failed: {ge}")

                            else: 
                                print("  - missing expiry for greeks; skipped.")
                        elif symbol:
                            print("  - stale alert; skip greeks/stock-state fetch.")

                        bounds = bucket_for_alert_iso(str(t_alert))
                        if bounds is not None:
                            start_utc, end_utc = bounds
                            # Trigger at end_utc + epsilon
                            end_dt = _parse_iso(end_utc)
                            if end_dt:
                                run_at = end_dt.timestamp() + 0.8
                                job = {
                                    'alert_id': canonical_id,
                                    'option_symbol': option_symbol,
                                    'start_utc': start_utc,
                                    'end_utc': end_utc,
                                }
                                schedule_bucket_job(bucket_jobs_heap, job, run_at)
                                print(f"  - bucket scheduled: [{start_utc} ~ {end_utc}]")
                        else:
                            print("  - bucket window invalid after clipping; skip.")

                        batch_new.append(it)

                    session.commit()

                # Reverse queue to process early enqueued
                for it in reversed(batch_new):
                    pending.append(it)

            # Process queue
            _process_queue(pending, max_n=settings.max_process_per_cycle)

            # Run due bucket jobs
            run_due_bucket_jobs(client, bucket_jobs_heap, time.time())

            if not newly_found:
                print(f"[{datetime.now(timezone.utc).isoformat()}] heartbeat, no new alerts. pending={len(pending)}")

            elapsed = time.monotonic() - cycle_start
            remaining = settings.poll_interval_sec - elapsed
            if remaining > 0:
                if stop_event is not None:
                    stop_event.wait(remaining)
                else:
                    time.sleep(remaining)

    except KeyboardInterrupt:
        print("Stopped by user.")
    finally:
        client.close()

def main():
    client = UWClient()

    # Pending query + enqueueing
    pending: Deque[Dict[str, Any]] = deque()
    enqueued_ids: Set[str] = set()
    bucket_jobs_heap: List[Tuple[float, BucketJob]] = []

    warm_started = False
    print(f"[{datetime.now(timezone.utc).isoformat()}] start polling every {settings.poll_interval_sec}s ...")

    try:
        while True:
            cycle_start = time.monotonic()
            # Fetch latest alerts on page 0
            try:
                data = client.fetch_alerts(
                    page=0,
                    limit=settings.alerts_limit,
                    intraday_only=settings.alerts_intraday_only,
                    # config_ids=settings.alerts_config_ids,
                )

            except Exception as e:
                print(f"[ERR] fetch_alerts: {e}")
                # Try to process existing alerts even if fail
                _process_queue(pending, max_n=settings.max_process_per_cycle)
                run_due_bucket_jobs(client, bucket_jobs_heap, time.time())
                time.sleep(settings.poll_interval_sec)
                continue

            items = data.get('data')
            if not isinstance(items, list):
                items = []
            
            # Initialize once: avoid backfill trigger 429
            if not warm_started:
                warm_started = True
                print(f"[warm-start] skip first cycle; queue stays empty.")
                time.sleep(settings.poll_interval_sec)
                continue

            # Enqueueing: enqueue only fresh and not enqueued alerts
            newly_found = []
            for it in items:
                aid = str(it.get('id'))
                if not aid or aid in enqueued_ids:
                    continue
                enqueued_ids.add(aid) 
                it['__id'] = aid
                newly_found.append(it)

            if newly_found:
                batch_new: list[dict] = []
                batch_seen: set[str] = set()

                # Untracable endpoints: trigger immediately before enqueueing (fresh)
                with SessionLocal() as session:
                    for it in newly_found:
                        aid = it['__id']
                        t_alert = it.get('created_at')
                        m = it.get('meta')
                        symbol = m.get('underlying_symbol')
                        option_symbol = it.get('symbol')
                        ask_volume = int(m.get('ask_volume'))
                        bid_volume = int(m.get('bid_volume'))
                        volume = int(m.get('volume'))
                        avg_fill = float(m.get('avg_fill'))
                        close = float(m.get('close'))
                        diff = float(m.get('diff'))
                        total_premium = float(m.get('total_premium'))
                        iv_change = float(m.get('iv_change'))
                        open_interest = int(m.get('open_interest'))
                        vol_oi_ratio = float(m.get('vol_oi_ratio'))
                        multi_leg_vol_ratio = float(m.get('multi_leg_vol_ratio'))

                        canonical_id = crud.upsert_alert(
                            session,
                            alert_id=aid,
                            created_at=str(t_alert),
                            symbol=symbol,
                            option_symbol=option_symbol,
                            ask_volume=ask_volume,
                            bid_volume=bid_volume,
                            volume=volume,
                            avg_fill=avg_fill,
                            close=close,
                            diff=diff,
                            total_premium=total_premium,
                            iv_change=iv_change,
                            open_interest=open_interest,
                            vol_oi_ratio=vol_oi_ratio,
                            multi_leg_vol_ratio=multi_leg_vol_ratio,
                            raw_obj=it
                        )

                        if canonical_id in batch_seen:
                            print(f"  - duplicate (same day+contract); skip enqueue/schedule, id={canonical_id}.")
                            continue
                        batch_seen.add(canonical_id)

                        print(f"[NEW] {t_alert} {symbol} {option_symbol} id={canonical_id}")

                        # Derive expiry from option symbol
                        derived_expiry = None
                        derived_strike = None
                        derived_side = None
                        if option_symbol:
                            try:
                                parts = parse_option_symbol(option_symbol)
                                derived_expiry = parts.expiry
                                derived_strike = parts.strike
                                derived_side = parts.right
                                print(f"  - parsed from {option_symbol}: {parts}")
                            except Exception as pe:
                                print(f"  - parse option_symbol failed: {pe}")

                        if symbol and is_fresh(str(t_alert), settings.live_alert_grace_sec):
                            g_expiry = derived_expiry
                            g_strike = derived_strike
                            g_side = derived_side
                            if g_expiry and g_strike:
                                try:
                                    greeks = client.fetch_stock_greeks(symbol, expiry=g_expiry)
                                    greek = get_greeks(greeks, g_strike, g_side)
                                    price = client.fetch_stock_state(symbol)['data']
                                    stock_market_time = price['market_time']
                                    stock_close = float(price['close'])
                                    stock_previous_close = float(price['prev_close'])
                                    stock_volume = int(price['volume'])
                                    stock_total_volume = int(price['total_volume'])
                                    otm_pct = _compute_otm_pct(stock_close, g_strike, g_side)
                                    crud.save_greeks_snapshot(session, 
                                                              alert_id=canonical_id,
                                                              option_symbol=greek.option_symbol,
                                                              side=greek.side,
                                                              dte=greek.dte,
                                                              strike=greek.strike,
                                                              delta=greek.delta,
                                                              gamma=greek.gamma,
                                                              theta=greek.theta,
                                                              rho=greek.rho,
                                                              vega=greek.vega,
                                                              vanna=greek.vanna,
                                                              charm=greek.charm,
                                                              volatility=greek.volatility, 
                                                              otm_pct=otm_pct,
                                                              expiry=g_expiry,
                                                              data=greeks)
                                    crud.save_price_snapshot(session, 
                                                             alert_id=canonical_id, 
                                                             market_time=stock_market_time,
                                                             stock_close=stock_close,
                                                             stock_previous_close=stock_previous_close,
                                                             stock_volume=stock_volume,
                                                             stock_total_volume=stock_total_volume,
                                                             data=price)
                                    print(f"  - greeks ok ({symbol}); stock-state ok (snapshot saved)")
                                except Exception as ge:
                                    print(f"  [ERR] greeks/state failed: {ge}")

                            else: 
                                print("  - missing expiry for greeks; skipped.")
                        elif symbol:
                            print("  - stale alert; skip greeks/stock-state fetch.")

                        bounds = bucket_for_alert_iso(str(t_alert))
                        if bounds is not None:
                            start_utc, end_utc = bounds
                            # Trigger at end_utc + epsilon
                            end_dt = _parse_iso(end_utc)
                            if end_dt:
                                run_at = end_dt.timestamp() + 0.8
                                job = {
                                    'alert_id': canonical_id,
                                    'option_symbol': option_symbol,
                                    'start_utc': start_utc,
                                    'end_utc': end_utc,
                                }
                                schedule_bucket_job(bucket_jobs_heap, job, run_at)
                                print(f"  - bucket scheduled: [{start_utc} ~ {end_utc}]")
                        else:
                            print("  - bucket window invalid after clipping; skip.")

                        batch_new.append(it)

                    session.commit()

                # Reverse queue to process early enqueued
                for it in reversed(batch_new):
                    pending.append(it)

            # Process queue
            _process_queue(pending, max_n=settings.max_process_per_cycle)

            # Run due bucket jobs
            run_due_bucket_jobs(client, bucket_jobs_heap, time.time())

            if not newly_found:
                print(f"[{datetime.now(timezone.utc).isoformat()}] heartbeat, no new alerts. pending={len(pending)}")

            elapsed = time.monotonic() - cycle_start
            remaining = settings.poll_interval_sec - elapsed
            if remaining > 0:
                time.sleep(remaining)

    except KeyboardInterrupt:
        print("Stopped by user.")
    finally:
        client.close()

def _process_queue(pending: Deque[Dict[str, Any]], max_n: int) -> None:
    processed = 0
    while processed < max_n and pending:
        it = pending.popleft()
        aid = it['__id']
        # print(f"[Proc] dequeued (id={aid})")
        processed += 1

if __name__ == "__main__":
    main()
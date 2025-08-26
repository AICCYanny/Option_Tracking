import os
import math
from datetime import datetime

import requests
import pandas as pd
import streamlit as st
from dotenv import load_dotenv, find_dotenv

# -----------------------------
# Helpers
# -----------------------------

def fmt_unit(x, digits=2):
    """Format numbers with K/M/B suffix, keeping `digits` decimals."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "—"
    sign = '-' if x < 0 else ''
    ax = abs(x)
    if ax >= 1_000_000_000:
        return f"{sign}{ax/1_000_000_000:.{digits}f}B"
    if ax >= 1_000_000:
        return f"{sign}{ax/1_000_000:.{digits}f}M"
    if ax >= 1_000:
        return f"{sign}{ax/1_000:.{digits}f}K"
    return f"{sign}{ax:.{digits}f}"


def pct_phrase(label: str, curr, prev, digits=1, denom_abs=True, basic_no_change=True):
    """Return a Chinese phrase with label (环比/同比/较上一日) + 增加/减少X%.
    If rounded to 0.0%, output label + 基本不变 (when basic_no_change).
    denom_abs=True will use abs(prev) in denominator.
    """
    try:
        curr = float(curr); prev = float(prev)
    except (TypeError, ValueError):
        return f"{label}—", None
    denom = abs(prev) if denom_abs else prev
    if denom == 0:
        return f"{label}—", None
    pct = (curr - prev) / denom * 100.0
    rounded = round(abs(pct), digits)
    if basic_no_change and rounded == 0:
        return f"{label}基本不变", pct
    word = "增加" if pct >= 0 else "减少"
    return f"{label}{word}{rounded:.{digits}f}%", pct


def ratio_phrase(curr, base, digits=2):
    try:
        curr = float(curr); base = float(base)
    except (TypeError, ValueError):
        return "—"
    if base == 0:
        return "—"
    return f"{curr/base:.{digits}f}"


def sign_transition(prev, curr):
    try:
        prev = float(prev); curr = float(curr)
    except (TypeError, ValueError):
        return None
    if prev < 0 and curr > 0:
        return "由负转正"
    if prev > 0 and curr < 0:
        return "由正转负"
    return None


def gex_change_label_and_pct(curr, prev, digits=1):
    """Special rules for GEX MoM phrasing:
    - Always prefix with '较上一日'.
    - If today a>0, yesterday b<0: phrase should be 增加 and pct=(a-b)/(-b)
    - If today a<0, yesterday b>0: phrase should be 减少 and pct=(b-a)/b
    - Else normal: 增加/减少 based on sign of (a-b) with denom=|b|
    If rounded 0.0% -> '较上一日基本不变'.
    """
    try:
        a = float(curr); b = float(prev)
    except (TypeError, ValueError):
        return "较上一日—", None
    if b == 0:
        return "较上一日—", None

    if a > 0 and b < 0:
        pct = (a - b) / (-b) * 100.0
        rounded = round(abs(pct), digits)
        if rounded == 0:
            return "较上一日基本不变", pct
        return f"较上一日增加{rounded:.{digits}f}%", pct
    if a < 0 and b > 0:
        pct = (b - a) / b * 100.0
        rounded = round(abs(pct), digits)
        if rounded == 0:
            return "较上一日基本不变", pct
        return f"较上一日减少{rounded:.{digits}f}%", pct

    # same sign or any other case -> normal vs |b|
    pct = (a - b) / abs(b) * 100.0
    rounded = round(abs(pct), digits)
    if rounded == 0:
        return "较上一日基本不变", pct
    word = "增加" if pct >= 0 else "减少"
    return f"较上一日{word}{rounded:.{digits}f}%", pct


def fetch_options_volume(ticker: str, limit: int, token: str) -> pd.DataFrame:
    url = f"https://api.unusualwhales.com/api/stock/{ticker}/options-volume"
    headers = {"Accept": "application/json, text/plain", "Authorization": f"Bearer {token}"}
    params = {"limit": str(limit)}
    r = requests.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    data = r.json().get('data', [])
    df = pd.DataFrame(data)
    # Ensure sorted: newest at top; coerce date
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date']).dt.date
    return df


def fetch_gex(ticker: str, token: str, timeframe: str = "1W") -> pd.DataFrame:
    url = f"https://api.unusualwhales.com/api/stock/{ticker}/greek-exposure"
    headers = {"Accept": "application/json, text/plain", "Authorization": f"Bearer {token}"}
    params = {"timeframe": timeframe}
    r = requests.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    data = r.json().get('data', [])
    df = pd.DataFrame(data)
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date']).dt.date
        df = df.sort_values('date')  # ascending; newest at bottom
    for c in ['call_gamma', 'put_gamma']:
        if c not in df.columns:
            df[c] = 0.0
    df['total_gamma'] = df['call_gamma'].astype(float) + df['put_gamma'].astype(float)
    return df


def build_summary(vol: pd.DataFrame, gex: pd.DataFrame, ticker_display: str) -> str:
    if vol is None or vol.empty:
        return "未获取到成交与权利金数据。"

    # vol df: newest at index 0 per API
    vol_today = vol.iloc[0]
    vol_prev = vol.iloc[1] if len(vol) >= 2 else None
    vol_far = vol.iloc[-1] if len(vol) >= 2 else None

    # Totals (today)
    call_vol_t = float(vol_today.get('call_volume', 0) or 0)
    put_vol_t = float(vol_today.get('put_volume', 0) or 0)
    tot_vol_t = call_vol_t + put_vol_t

    call_prem_t = float(vol_today.get('call_premium', 0) or 0)
    put_prem_t = float(vol_today.get('put_premium', 0) or 0)
    tot_prem_t = call_prem_t + put_prem_t

    # 30D avg (today)
    avg30_call = float(vol_today.get('avg_30_day_call_volume', 0) or 0)
    avg30_put = float(vol_today.get('avg_30_day_put_volume', 0) or 0)
    avg30_tot = avg30_call + avg30_put

    # Previous / far day values
    if vol_prev is not None:
        call_vol_prev = float(vol_prev.get('call_volume', 0) or 0)
        put_vol_prev = float(vol_prev.get('put_volume', 0) or 0)
        tot_vol_prev = call_vol_prev + put_vol_prev

        call_prem_prev = float(vol_prev.get('call_premium', 0) or 0)
        put_prem_prev = float(vol_prev.get('put_premium', 0) or 0)
        tot_prem_prev = call_prem_prev + put_prem_prev

        oi_prev = float(vol_prev.get('call_open_interest', 0) or 0) + float(vol_prev.get('put_open_interest', 0) or 0)
    else:
        call_vol_prev = put_vol_prev = tot_vol_prev = None
        call_prem_prev = put_prem_prev = tot_prem_prev = None
        oi_prev = None

    if vol_far is not None:
        call_vol_far = float(vol_far.get('call_volume', 0) or 0)
        put_vol_far = float(vol_far.get('put_volume', 0) or 0)
        tot_vol_far = call_vol_far + put_vol_far

        call_prem_far = float(vol_far.get('call_premium', 0) or 0)
        put_prem_far = float(vol_far.get('put_premium', 0) or 0)
        tot_prem_far = call_prem_far + put_prem_far
    else:
        call_vol_far = put_vol_far = tot_vol_far = None
        call_prem_far = put_prem_far = tot_prem_far = None

    # OI today and change vs prev
    oi_today = float(vol_today.get('call_open_interest', 0) or 0) + float(vol_today.get('put_open_interest', 0) or 0)
    oi_phrase_text, _ = pct_phrase("较上一日", oi_today, oi_prev, digits=2) if oi_prev is not None else ("较上一日—", None)

    # Ratios & changes (explicit 环比/同比 labels)
    vol30_ratio = ratio_phrase(tot_vol_t, avg30_tot, digits=2)

    vol_mom_phrase, _ = pct_phrase("环比", tot_vol_t, tot_vol_prev) if tot_vol_prev is not None else ("环比—", None)
    vol_yoy_phrase, _ = pct_phrase("同比", tot_vol_t, tot_vol_far) if tot_vol_far is not None else ("同比—", None)

    call_vol_mom_phrase, _ = pct_phrase("环比", call_vol_t, call_vol_prev) if call_vol_prev is not None else ("环比—", None)
    call_vol_yoy_phrase, _ = pct_phrase("同比", call_vol_t, call_vol_far) if call_vol_far is not None else ("同比—", None)

    put_vol_mom_phrase, _ = pct_phrase("环比", put_vol_t, put_vol_prev) if put_vol_prev is not None else ("环比—", None)
    put_vol_yoy_phrase, _ = pct_phrase("同比", put_vol_t, put_vol_far) if put_vol_far is not None else ("同比—", None)

    prem_mom_phrase, _ = pct_phrase("环比", tot_prem_t, tot_prem_prev) if tot_prem_prev is not None else ("环比—", None)
    prem_yoy_phrase, _ = pct_phrase("同比", tot_prem_t, tot_prem_far) if tot_prem_far is not None else ("同比—", None)

    # GEX: take last two dates (sorted ascending) -> today = last, prev = second last
    gex_today = gex.iloc[-1] if (gex is not None and len(gex) >= 1) else None
    gex_prev = gex.iloc[-2] if (gex is not None and len(gex) >= 2) else None

    if gex_today is not None:
        total_gamma_today = float(gex_today.get('total_gamma', 0) or 0)
        gex_text_value = fmt_unit(total_gamma_today, digits=2)
        if gex_prev is not None:
            total_gamma_prev = float(gex_prev.get('total_gamma', 0) or 0)
            trans = sign_transition(total_gamma_prev, total_gamma_today)
            change_phrase, _ = gex_change_label_and_pct(total_gamma_today, total_gamma_prev, digits=1)
            if trans:
                gex_line = f"净gamma exposure{trans}为{gex_text_value}，{change_phrase}。"
            else:
                gex_line = f"净gamma exposure为{gex_text_value}，{change_phrase}。"
        else:
            gex_line = f"净gamma exposure为{gex_text_value}。"
    else:
        gex_line = "净gamma exposure数据不可用。"

    # First line: volumes & premiums (today)
    line1 = (
        f"今天{ticker_display.upper()}总成交量{fmt_unit(tot_vol_t)}（call {fmt_unit(call_vol_t)}，put {fmt_unit(put_vol_t)}），"
        f"总成交额{fmt_unit(tot_prem_t)}，call成交额 {fmt_unit(call_prem_t)}，put成交额 {fmt_unit(put_prem_t)}。"
    )

    # Second line: volume vs 30D & MoM/YoY
    line2 = (
        f"\n\n成交量与30天均值比为{vol30_ratio}，{vol_mom_phrase}，{vol_yoy_phrase}。"
    )

    # Third & fourth lines: call/put volume changes
    line3 = f"\n\nCall成交量{call_vol_mom_phrase}，{call_vol_yoy_phrase}。"
    line4 = f"\n\nPut成交量{put_vol_mom_phrase}，{put_vol_yoy_phrase}。"

    # Fifth line: premium changes
    line5 = f"\n\n成交额{prem_mom_phrase}，{prem_yoy_phrase}。"

    # Sixth line: OI change
    line6 = f"\n\nOI{oi_phrase_text}。"

    # Seventh line: GEX
    line7 = f"\n\n{gex_line}"

    return line1 + line2 + line3 + line4 + line5 + line6 + line7


# -----------------------------
# Streamlit UI
# -----------------------------

def main():
    st.set_page_config(page_title="UW Options Daily Summary", page_icon="📈", layout="centered")

    load_dotenv(find_dotenv())
    token = os.getenv('UW_TOKEN', '').strip()

    st.title("Unusual Whales 期权日报生成器")
    st.caption("输入 Ticker 与天数（limit），自动拉取 EOD 数据并生成中文总结。")

    with st.sidebar:
        st.header("参数设置")
        ticker = st.text_input("Ticker", value="QQQ").strip().upper()
        limit = st.number_input("limit（最近N天，用于环比/同比基准）", min_value=2, max_value=30, value=6, step=1)
        timeframe = st.selectbox("GEX 时间窗口", options=["1W", "2W", "1M"], index=0, help="仅用于GEX接口，不影响环比/同比基准")
        submitted = st.button("生成总结")

    if not token:
        st.warning("尚未检测到 UW_TOKEN。请在本地 .env 中设置 UW_TOKEN，或在环境变量中配置。")
        st.stop()

    if submitted:
        try:
            vol_df = fetch_options_volume(ticker, int(limit), token)
        except Exception as e:
            st.error(f"拉取 options-volume 失败：{e}")
            return
        try:
            gex_df = fetch_gex(ticker, token, timeframe=timeframe)
        except Exception as e:
            st.error(f"拉取 greek-exposure 失败：{e}")
            gex_df = pd.DataFrame()

        if vol_df.empty:
            st.error("options-volume 返回为空。请检查 ticker 或 limit。")
            return

        # 展示原始数据（可折叠）
        with st.expander("查看原始数据（options-volume）"):
            st.dataframe(vol_df)
        if not gex_df.empty:
            with st.expander("查看原始数据（greek-exposure）"):
                st.dataframe(gex_df)

        summary = build_summary(vol_df, gex_df, ticker)
        st.subheader("📄 自动生成总结")
        st.write(summary)

        # 复制按钮
        st.code(summary)


if __name__ == "__main__":
    main()

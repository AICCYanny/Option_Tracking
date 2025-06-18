import streamlit as st, requests
import time
import pandas as pd
import gzip
import io
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime, timedelta, date
from cachetools import TTLCache, cached
import json

# 单例 Session，避免每次新建 TCP 连接
@st.cache_resource(show_spinner=False)
def get_session():
    s = requests.Session()
    s.headers["User-Agent"] = "OptionTracking/1.0"
    return s

# 数据级缓存（24h）
@st.cache_data(ttl=86400, show_spinner=False)
def fetch_option_data_for_day(symbol, trade_date, dte_offset, cp, api_key):
    """返回指定日期 + cp （C/P）的 DataFrame；429 时自动指数回退"""
    session = get_session()
    url = "https://restapi.ivolatility.com/equities/eod/stock-opts-by-param"
    params = {
        "apiKey": api_key,
        "symbol": symbol.upper(),
        "tradeDate": trade_date.strftime('%Y-%m-%d'),
        "dteFrom": dte_offset,
        "dteTo": 700 + dte_offset,
        "cp": cp,                       # 必须 C 或 P
        "moneynessFrom": -100,
        "moneynessTo": 100,
        "region": "USA",
    }

    backoff = 2          # 秒
    while True:
        res = session.get(url, params=params, timeout=10)
        if res.status_code == 429:
            time.sleep(backoff)
            backoff = min(backoff * 2, 32)   # 指数回退，封顶 32s
            continue
        if res.status_code != 200:
            st.error(f"[{trade_date:%Y-%m-%d} {cp}] API {res.status_code}: {res.text}")
            return None

        data = res.json()
        if isinstance(data.get("data"), list) and data["data"]:
            return pd.DataFrame(data["data"])

        if data.get("status", {}).get("code") == "PENDING":
            pending_url = data["status"]["urlForDetails"]
            for _ in range(16):
                time.sleep(2)
                pending = session.get(pending_url).json()
                if isinstance(pending, list) and pending[0]["meta"]["status"] == "COMPLETE":
                    dl_url = pending[0]["data"][0]["urlForDownload"]
                    gz_data = session.get(dl_url).content
                    with gzip.GzipFile(fileobj=io.BytesIO(gz_data)) as gz:
                        return pd.read_csv(gz)
        return None
    
def detect_abnormal_trades(raw_df,
                           win=3,
                           rel_thresh=3.0,
                           notional_thresh_k=5000,
                           vol_abs_thresh=1000,
                           vol_gt_oi=False):
    """
    条件：
        • 名义金额 = volume × mid_price × 100  ≥ notional_thresh_k * 1_000
        • volume / rolling_mean(win)          ≥ rel_thresh
    参数单位：
        notional_thresh_k  -- 千美元
    """
    # ---------- 1. 列名统一 ----------
    rename_map = {
        # underlying price
        "underlying_price": "underlying", "spot": "underlying", "close": "underlying", 
        # cp
        "cp": "cp", "call_put": "cp", "cpFlag": "cp",
        # strike
        "strike": "strike", "price_strike": "strike",
        # expiry
        "expiry": "expiry", "expiration_date": "expiry", "expirationDate": "expiry",
        # volume
        "volume": "volume", "totalVolume": "volume",
        # bid / ask
        "Bid": "Bid", "bid": "Bid", "Ask": "Ask", "ask": "Ask",
        # trade date
        "tradeDate": "tradeDate", "c_date": "tradeDate",
        # open interest
        "openinterest": "OI", "OI": "OI", "openInterest": "OI",
        # implied volatility
        "iv": "iv", "IV": "iv", "ImpliedVol": "iv",
        # delta
        "delta": "delta", "Delta": "delta",
        # gamma
        "gamma": "gamma", "Gamma": "gamma",
        # vega
        "vega":  "vega",  "Vega":  "vega",
        # theta
        "theta": "theta", "Theta": "theta",
        # rho
        "rho":   "rho",   "Rho":   "rho"
    }
    df = raw_df.rename(columns={c: rename_map.get(c, c) for c in raw_df.columns})
    df = df.loc[:, ~df.columns.duplicated()]  # 去重列

    # ---------- 2. 字段检查 ----------
    base_need = {"underlying", "cp", "strike", "expiry", "volume", "Bid", "Ask", "tradeDate"}
    if vol_gt_oi:
        base_need.add("OI")
    if not base_need.issubset(df.columns):
        st.warning("缺少关键列，无法筛选异常")
        return pd.DataFrame()
    
    keep_cols = list(base_need) + ["iv", "delta", "gamma", "vega", "theta", "rho"]
    keep_cols = [c for c in keep_cols if c in df.columns]  # greeks 可能缺

    # ---------- 3. 基础清洗 ----------
    df = df[keep_cols].copy()
    df["tradeDate"] = pd.to_datetime(df["tradeDate"])
    df[["volume", "Bid", "Ask"]] = df[["volume", "Bid", "Ask"]].apply(
        pd.to_numeric, errors="coerce").fillna(0)

    # 名义金额（美元）
    df["mid"]      = (df["Bid"] + df["Ask"]) / 2
    df["notional"] = df["volume"] * df["mid"] * 100   # 美股期权乘数 100

    # ---------- 4. 分组计算 ----------
    out = []
    for _, g in df.groupby(["cp", "strike", "expiry"]):
        g = g.sort_values("tradeDate")
        g["roll_mean"] = g["volume"].shift(1).rolling(win, min_periods=1).mean()
        rel = g["volume"] / g["roll_mean"].replace(0, np.nan)
        g["rel"] = rel

        notional_ok = g["notional"] >= notional_thresh_k * 1_000

        cond_rel_ok     = (~rel.isna()) & (rel >= rel_thresh)
        cond_abs_ok     = (rel.isna())  & (g["volume"] >= vol_abs_thresh)

        cond = notional_ok & (cond_rel_ok | cond_abs_ok)

        if vol_gt_oi:
            cond &= g["volume"] > g["OI"]           # 追加 Volume > OI
        g["abnormal"] = cond

        out.append(g)

    return pd.concat(out, ignore_index=True)

def detect_abnormal_trades_v2(
    trades_df: pd.DataFrame,
    *,
    shares_out: int | None,
    adv_series: pd.DataFrame,
    notional_bp_thresh: float,
    dd_adv_pct_thresh: float,
    adv_min_thresh: float,
    win: int = 10,
    rel_thresh: float = 5.0,
    vol_abs_thresh: int=1000,
    vol_gt_oi: bool = True,
) -> pd.DataFrame:
    """
    **新版** 异常筛选核心函数  
    （取代旧版 `detect_abnormal_trades`，不再使用 `notional_thresh_k`）。

    逻辑＝四条条件「全都满足」：
        1. Notional / 动态市值 ≥ `notional_bp_thresh` bp
        2. |Dollar Delta| / (ADV × Underlying) ≥ `dd_adv_pct_thresh` %
        3. 标的 ADV ≥ `adv_min_thresh`
        4. *滚动量比* ≥ `rel_thresh`
           （窗口 `win`，可选附加 `vol_gt_oi` 过滤）

    返回值在原字段基础上新增：
        - dynamic_mktcap
        - notional_pct_mktcap
        - dollar_delta
        - dd_pct_adv
        - vol_roll_mean
        - vol_ratio
        - abnormal   （满足滚动量比条件）
        - feature_pass  （四条全部满足 → True）

    Parameters
    ----------
    trades_df : pd.DataFrame
        原始期权成交记录。需至少包含：
        ['tradeDate', 'option_symbol', 'volume', 'openInterest',
         'delta', 'underlying', 'notional']。
    shares_out : int | None
        当日流通股本。若为 `None`，与动态市值相关的条件自动判 Fail。
    adv : float | None
        最近 30 交易日平均成交量。若为 `None`，与 ADV 相关条件自动判 Fail。
    notional_bp_thresh : float
        条件 (1) 阈值，单位 bp。
    dd_adv_pct_thresh : float
        条件 (2) 阈值，单位 %。
    adv_min_thresh : float
        条件 (3) 阈值，单位 股。
    win : int
        滚动窗口长度（交易日）。
    rel_thresh : float
        滚动量比阈值。
    vol_gt_oi : bool
        若为 True，则强制 `volume > openInterest`。

    Returns
    -------
    pd.DataFrame
        与输入同序 DataFrame，附带计算列与 `feature_pass` 布尔标记。
    """
    rename_map = {
        # underlying price
        "underlying_price": "underlying", "spot": "underlying", "close": "underlying", 
        # cp
        "cp": "cp", "call_put": "cp", "cpFlag": "cp",
        # strike
        "strike": "strike", "price_strike": "strike",
        # expiry
        "expiry": "expiry", "expiration_date": "expiry", "expirationDate": "expiry",
        # volume
        "volume": "volume", "totalVolume": "volume",
        # bid / ask
        "Bid": "Bid", "bid": "Bid", "Ask": "Ask", "ask": "Ask",
        # mid price
        "price": "mid",
        # trade date
        "tradeDate": "tradeDate", "c_date": "tradeDate",
        # open interest
        "openinterest": "OI", "OI": "OI", "openInterest": "OI",
        # implied volatility
        "iv": "iv", "IV": "iv", "ImpliedVol": "iv",
        # delta
        "delta": "delta", "Delta": "delta",
        # gamma
        "gamma": "gamma", "Gamma": "gamma",
        # vega
        "vega":  "vega",  "Vega":  "vega",
        # theta
        "theta": "theta", "Theta": "theta",
        # rho
        "rho":   "rho",   "Rho":   "rho"
    }
    raw_df = trades_df.rename(columns={c: rename_map.get(c, c) for c in trades_df.columns})
    raw_df = raw_df.loc[:, ~raw_df.columns.duplicated()]  # 去重列


    df = raw_df.copy()

    # ===== 0) 预处理 =====
    df["tradeDate"] = pd.to_datetime(df["tradeDate"])
    df[["volume", "Bid", "Ask", "mid"]] = df[["volume", "Bid", "Ask", "mid"]].apply(
        pd.to_numeric, errors="coerce").fillna(0)
    df["notional"] = df["volume"] * df["mid"] * 100   # 美股期权乘数 100

    # 映射adv
    adv_series = adv_series.set_index('Date')['adv']
    df['adv'] = df['tradeDate'].map(adv_series)

    out = []
    for _, g in df.groupby(["cp", "strike", "expiry"]):
        g = g.sort_values("tradeDate")
        g["roll_mean"] = g["volume"].shift(1).rolling(win, min_periods=1).mean()
        rel = g["volume"] / g["roll_mean"].replace(0, np.nan)
        g["rel"] = rel

        # ===== 1) 滚动量比 (按合约维度) =====
        cond_rel = (~rel.isna()) & (rel >= rel_thresh) & (g["volume"] >= vol_abs_thresh)
        cond_abs = (rel.isna())  & (g["volume"] >= vol_abs_thresh)

        # ===== 2) 动态市值 & Notional bp =====
        g["dynamic_mktcap"] = shares_out * g["underlying"]
        g["notional_pct_mktcap"] = 10_000 * g["notional"] / g["dynamic_mktcap"]
        cond_notional_bp = g["notional_pct_mktcap"] >= notional_bp_thresh

        # ===== 3) Dollar Delta / (ADV×Underlying) =====
        g["dollar_delta"] = g["delta"].abs() * 100 * g["underlying"] * g['volume']
        #  |DD| / (ADV × Underlying) ×100 (%)
        g["dd_pct_adv"] = 100 * g["dollar_delta"] / (g['adv'] * g["underlying"])
        cond_dd_adv = g["dd_pct_adv"] >= dd_adv_pct_thresh

        # ===== 4) ADV 本身 =====
        cond_adv = g['adv'] <= adv_min_thresh

        # ===== 5) 最终叠加 =====
        cond =  (
            (cond_notional_bp & 
            cond_dd_adv) & 
            (cond_rel | cond_abs) &
            cond_adv
            )

        if vol_gt_oi:
            cond &= g["volume"] > g["OI"]           # 追加 Volume > OI
        g["abnormal"] = cond

        out.append(g)

    return pd.concat(out, ignore_index=True)

def fetch_eod(option_symbol: str,
              api_key: str,
              retry: int = 3,
              cutoff_date: date | None = None) -> pd.DataFrame:
    """
    拉取单个 option_symbol 过去 3 个月日线 EOD 数据，缓存 24 h
    """
    session = get_session()
    end = datetime.today().date() if cutoff_date is None else cutoff_date
    start = end - timedelta(days=90)
    url   = "https://restapi.ivolatility.com/equities/eod/single-stock-option-raw-iv"
    params = {
        "apiKey": api_key,
        "symbol": option_symbol,
        "from": start.strftime("%Y-%m-%d"),
        "to":   end.strftime("%Y-%m-%d")
    }
    for _ in range(retry):
        try:
            res = session.get(url, params=params, timeout=10)
            if not res.ok:
                time.sleep(1.5)
                continue
            df = pd.DataFrame(res.json().get("data", []))
           
            if cutoff_date and "date" in df.columns:
                mask = pd.to_datetime(df["date"], errors="coerce") <= pd.Timestamp(cutoff_date)
                df = df[mask]

            return df
        except (requests.RequestException, json.JSONDecodeError):
            time.sleep(1.5)
            continue
    # 所有重试都失败，返回空表
    return pd.DataFrame()

# ① 先建一个 1 小时 TTL 的缓存容器
_eod_cache = TTLCache(maxsize=4000, ttl=3600)   # 4 k key，够用了

# ② 用 cached 装饰器包一层
@cached(_eod_cache)
def fetch_eod_cached(
    option_symbol: str,
    api_key: str,
    cutoff_date: date | None = None  # ← 新增
) -> pd.DataFrame:
    return fetch_eod(option_symbol, api_key, cutoff_date=cutoff_date)

def show_contract_chart(option_symbol: str, api_key: str, cutoff_date: date | None = None) -> None:
    """显示合约过去 3 个月成交量 + Mid 价图，并附 EOD 数据表"""
    df = fetch_eod_cached(option_symbol, api_key, cutoff_date=cutoff_date)
    if df.empty:
        st.warning("未获取到任何 EOD 数据")
        return

    # —— 列名统一 —— #
    df = df.rename(columns={
        "price": "price", "Price": "price", "mid": "price",
        "volume": "volume", "Volume": "volume"
    })
    if {"price", "volume", "date"}.issubset(df.columns) is False:
        st.warning("EOD 数据缺少必要列（price / volume / date）")
        return

    # —— 清洗 —— #
    df["price"]  = pd.to_numeric(df["price"],  errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    df["date"]   = pd.to_datetime(df["date"],  errors="coerce")

    # ① 先保证日期排序正确
    df = df.dropna(subset=["date"]).sort_values("date")

    # ② 需要计算环比百分变的列
    greek_cols = ["delta", "gamma", "vega", "theta", "rho"]

    # ③ 对每个列使用 pct_change，乘 100 得到百分数
    for col in greek_cols:
        if col in df.columns:
            df[f"{col}_pct"] = df[col].pct_change() * 100
        else:
            # 若接口该列缺失，给出 NaN 占位，便于后续统一处理
            df[f"{col}_pct"] = np.nan

    # ④ 可选：把首行 NaN 或 inf 替换成 0，并保留两位小数
    df[[f"{c}_pct" for c in greek_cols]] = (
        df[[f"{c}_pct" for c in greek_cols]]
        .replace([np.inf, -np.inf], np.nan)
        .round(2)
    )

    n_min = 2
    n_max = 30

    if len(df) >= 2:
        # 基于数据点数量调整柱宽
        n = len(df)
        # 限制在 [n_min, n_max] 之间
        n = min(max(n, n_min), n_max)
        # 线性插值：越多数据越接近0.6，越少数据越接近0.2
        bar_width = 0.2 + (n - n_min) / (n_max - n_min) * (0.6 - 0.2)
    else:
        bar_width = 0.2

    # —— 图表 —— #
    fig, ax1 = plt.subplots(figsize=(10, 6))
    ax1.bar(df["date"], df["volume"], width=bar_width, label="Volume")
    ax1.set_ylabel("Volume")
    ax1.tick_params(axis="x", rotation=45)                              
    ax1.margins(x=0.15)        # 左右各再留 15% 空白；想更窄就调 0.10~0.05

    ax2 = ax1.twinx()
    ax2.plot(df["date"], df["price"],
             color="#FFC107", marker="o", label="Mid Price ($)")
    ax2.set_ylabel("Mid Price ($)")
    plt.title(f"{option_symbol}  |  Past 3-Month Volume & Mid Price")
    lines, labels   = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax2.legend(lines + lines2, labels + labels2, loc="upper left")
    fig.tight_layout()
    plt.grid(True, which="both", axis="x", linestyle="--", alpha=0.3)
    st.pyplot(fig)

    # —— 数据表 & 下载 —— #
    st.subheader("🔍 原始 EOD 数据")
    st.dataframe(df)
    st.download_button(
        "📥 下载该合约 3-月 EOD 数据 CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name=f"{option_symbol}_eod.csv",
        mime="text/csv",
    )

def build_price_volume_figure(df: pd.DataFrame, option_symbol: str) -> "plt.Figure":

    n_min = 2
    n_max = 30

    if len(df) >= 2:
        # 基于数据点数量调整柱宽
        n = len(df)
        # 限制在 [n_min, n_max] 之间
        n = min(max(n, n_min), n_max)
        # 线性插值：越多数据越接近0.6，越少数据越接近0.2
        bar_width = 0.2 + (n - n_min) / (n_max - n_min) * (0.6 - 0.2)
    else:
        bar_width = 0.2

    fig, ax1 = plt.subplots(figsize=(10, 6))
    ax1.bar(df["date"], df["volume"], width=bar_width, label="Volume")
    ax1.set_ylabel("Volume")
    ax1.tick_params(axis="x", rotation=45)                              
    ax1.margins(x=0.15)        # 左右各再留 15% 空白；想更窄就调 0.10~0.05

    ax2 = ax1.twinx()
    ax2.plot(df["date"], df["price"],
             color="#FFC107", marker="o", label="Mid Price ($)")
    ax2.set_ylabel("Mid Price ($)")
    plt.title(f"{option_symbol}  |  Past 3-Month Volume & Mid Price")
    lines, labels   = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax2.legend(lines + lines2, labels + labels2, loc="upper left")
    fig.tight_layout()
    return fig

def generate_contract_chart(option_symbol: str, api_key: str, cutoff_date: date) -> "plt.Figure":
    """复用 fetch_eod()，但只返回 fig，方便批量保存"""
    df = fetch_eod_cached(option_symbol, api_key, cutoff_date=cutoff_date)
    if df.empty:
        return None

    df = df.rename(columns={
        "price": "price", "Price": "price", "mid": "price",
        "volume": "volume", "Volume": "volume"
    })
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df.dropna(subset=["date", "price"], inplace=True)

    return build_price_volume_figure(df, option_symbol=option_symbol)
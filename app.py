import streamlit as st
import pandas as pd
import requests
from functools import lru_cache
from cachetools import TTLCache, cached
import os
import time
import gzip
import io
import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime, timedelta
from datetime import date
import zipfile
import json
import yfinance as yf
from pandas.tseries.offsets import BDay

if os.getenv("RUNNING_IN_STREAMLIT_CLOUD") != "1":
    from dotenv import load_dotenv
    load_dotenv()

# --- 环境变量加载 ---
# load_dotenv()
default_key = os.getenv("IVOL_API_KEY", "")

@st.cache_data(ttl=86400, show_spinner=False)

@st.cache_resource(show_spinner=False)          # Streamlit 会把它当作单例
def get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": "OptionTracking/1.0"})
    return s

# --- OCC optionSymbol 构造 ---
def construct_option_symbol(ticker: str, expiry: str, call_put: str, strike: float) -> str:
    ticker_formatted = ticker.upper().ljust(6)
    expiry_formatted = expiry.replace("-", "")[2:]
    strike_formatted = f"{int(round(strike * 1000)):08d}"
    return f"{ticker_formatted}{expiry_formatted}{call_put.upper()}{strike_formatted}"

def get_workdays(end_date: str | datetime, days: int = 15):
    """返回向前回溯的最近 N 个工作日（含 end_date 当天）"""
    date = pd.to_datetime(end_date)
    workdays = pd.bdate_range(end=date, periods=days) 
    return workdays

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

def get_shares_outstanding(ticker: str) -> int | None:
    """
    查询某支股票的流通股本（Shares Outstanding）。
    Parameters
    ----------
    ticker : str
        股票代码（如 "AAPL"）。
    Returns
    -------
    int | None
        Shares Outstanding（股）。获取失败则返回 `None`。
    """
    try:
        stock = yf.Ticker(ticker)
        return stock.info.get("sharesOutstanding")
    except Exception:
        pass
    return None

def get_adv_series(ticker: str, 
                   n_days: int,
                   as_of: pd.Timestamp) -> pd.DataFrame:
    """
    获取过去 `n_days` 个自然日的每日 30 日平均成交量（ADV）时间序列。
    
    Parameters
    ----------
    ticker : str
        股票代码。
    n_days : int
        回溯天数（自然日）；建议设置为窗口长度的至少 2 倍。
        
    Returns
    -------
    pd.DataFrame
        包含 'date' 和 'adv' 两列，前 30 行为 NaN。
    """
    try:
        start = as_of - BDay(n_days * 2)  # 抓更长时间，保证够 rolling

        df_px = yf.download(
            ticker,
            start=start,
            end=as_of + timedelta(days=1),
            progress=False,
            auto_adjust=False,
            threads=False,
        )
        df_px.columns = df_px.columns.droplevel(1)
        df_px.columns.name = None
        df_px = df_px.reset_index()
            
        if df_px.empty:
            return pd.DataFrame(columns=["Date", "adv"])

        df_px = df_px[["Date", "Volume"]].copy()
        df_px["adv"] = df_px["Volume"].rolling(window=30, min_periods=30).mean()

        return df_px[["Date", "adv"]].tail(n_days).reset_index(drop=True)

    except Exception as e:
        print(f"[Error] {ticker}: {e}")
        return pd.DataFrame(columns=["Date", "adv"])

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

def figs_to_zip(figs: dict[str, "plt.Figure"]) -> bytes:
    """把 {'name': Figure, ...} 打包成 ZIP → bytes"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, fig in figs.items():
            img = io.BytesIO()
            fig.savefig(img, format="png", dpi=150, bbox_inches="tight")
            zf.writestr(f"{name}.png", img.getvalue())
    return buf.getvalue()

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

def draw_payoff(df_pay: pd.DataFrame, expiry: str, trade_date: str) -> "plt.Figure":
    """
    df_pay : 同一到期日、同一交易日的异常合约集合
             必须含 strike, volume, mid, cp 列
    """
    k_min, k_max = df_pay["strike"].min(), df_pay["strike"].max()
    S = np.linspace(0.5 * k_min, 1.5 * k_max, 400)

    payoff_sum, premium_sum = np.zeros_like(S), 0.0
    total_vol = df_pay["volume"].sum()

    for _, row in df_pay.iterrows():
        vol, K, prem = row["volume"], row["strike"], row["mid"]
        intrinsic = np.maximum(S - K, 0) if row["cp"] == "C" else np.maximum(K - S, 0)
        payoff_sum  += vol * intrinsic
        premium_sum += vol * prem

    net_payoff = (payoff_sum / total_vol) - (premium_sum / total_vol)

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(S, net_payoff, label="Net Payoff (Intrinsic − Premium)")
    ax.axhline(0, color="black", lw=0.8)
    ax.set_xlabel("Underlying Price at Expiry")
    ax.set_ylabel("Avg Net Payoff per Contract ($)")
    ax.set_title(f"Net Payoff | Exp {expiry} | Trade {trade_date}")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    return fig
    


# --- 页面选择 ---
page = st.sidebar.selectbox("选择功能", [
    "📈 获取单个期权 IV 数据（Intraday）",
    "🔍 获取期权集合（按 DTE + Moneyness/Delta）",
    "🗓️ 获取期权历史 EOD 数据",
    "📊 异常期权交易监测", 
    "🆕 Page 5 | 特征筛选",
    "🗓️ Label数据下载"
])

# --- 页面 1: Intraday IV 数据 ---
if page == "📈 获取单个期权 IV 数据（Intraday）":
    st.title("📈 获取单个期权 IV 数据（Intraday）")

    ticker = st.text_input("股票代码 (Ticker)")
    expiry_date = st.date_input("期权到期日")
    call_put = st.selectbox("Call / Put", ["C", "P"])
    strike = st.number_input("执行价")
    trade_date = st.date_input("数据日期")
    minute_type = st.selectbox("分钟类型", ["MINUTE_1", "MINUTE_5", "MINUTE_15", "MINUTE_30", "HOUR"])
    api_key = st.text_input("API Key", type="password", value=default_key)

    option_symbol = construct_option_symbol(ticker, expiry_date.strftime("%Y-%m-%d"), call_put, strike)
    st.code(f"生成的 optionSymbol: {option_symbol}")

    if st.button("获取 IV 数据"):
        url = "https://restapi.ivolatility.com/equities/intraday/single-equity-optionsymbol-rawiv"
        params = {
            "apiKey": api_key,
            "optionSymbol": option_symbol,
            "date": trade_date.strftime("%Y-%m-%d"),
            "minuteType": minute_type
        }

        with st.spinner("正在请求数据..."):
            res = requests.get(url, params=params)
            if res.status_code == 204:
                st.info("数据未准备好，请稍后再试。")
            elif res.status_code != 200:
                st.error(f"请求失败，状态码：{res.status_code}")
            else:
                data = res.json()
                if data.get("status", {}).get("code") == "PENDING":
                    st.warning("数据正在生成...")
                    st.markdown(f"[点击查看结果]({data['status'].get('urlForDetails')})")
                elif data.get("data"):
                    df = pd.DataFrame(data["data"])
                    st.success("✅ 数据获取成功")
                    st.dataframe(df)
                    st.download_button("📥 下载为 CSV", df.to_csv(index=False), "option_data.csv", mime="text/csv")
                else:
                    st.info("没有数据，请稍后再试。")

# --- 页面 2: stock-opts-by-param 查询 ---
elif page == "🔍 获取期权集合（按 DTE + Moneyness/Delta）":
    st.title("🔍 获取期权集合")

    symbol = st.text_input("股票代码")
    trade_date = st.date_input("交易日期 (YYYY-MM-DD)")
    dte_from = st.number_input("最小 DTE", value=0)
    dte_to = st.number_input("最大 DTE", value=30)
    call_put = st.selectbox("Call / Put", ["C", "P"])
    region = st.selectbox("市场区域（可选）", ["", "USA", "EUROPE", "ASIA", "CANADA", "RUSSIA", "CRYPTO"])
    api_key = st.text_input("API Key", type="password", value=default_key, key="param_api")

    st.write("✅ symbol (repr):", repr(symbol))

    st.subheader("可选过滤参数（需填一组）")
    money_col1, money_col2 = st.columns(2)
    with money_col1:
        moneyness_from = st.number_input("Moneyness From (%)", value=None, step=1.0, format="%.2f", key="money_from")
    with money_col2:
        moneyness_to = st.number_input("Moneyness To (%)", value=None, step=1.0, format="%.2f", key="money_to")

    delta_col1, delta_col2 = st.columns(2)
    with delta_col1:
        delta_from = st.number_input("Delta From", value=None, step=0.01, format="%.2f", key="delta_from")
    with delta_col2:
        delta_to = st.number_input("Delta To", value=None, step=0.01, format="%.2f", key="delta_to")

    if st.button("📡 获取期权列表"):
        if (moneyness_from is None or moneyness_to is None) and (delta_from is None or delta_to is None):
            st.error("⚠️ 请至少填写 moneyness 或 delta 的完整区间")
        else:
            url = "https://restapi.ivolatility.com/equities/eod/stock-opts-by-param"
            params = {
                "apiKey": api_key,
                "symbol": symbol.strip().upper(),
                "tradeDate": trade_date,
                "dteFrom": dte_from,
                "dteTo": dte_to,
                "cp": call_put
            }
            if region:
                params["region"] = region
            if moneyness_from is not None and moneyness_to is not None:
                params["moneynessFrom"] = moneyness_from
                params["moneynessTo"] = moneyness_to
            if delta_from is not None and delta_to is not None:
                params["deltaFrom"] = delta_from
                params["deltaTo"] = delta_to

            try:
                res = requests.get(url, params=params)
                data = res.json()

                with st.expander("📦 查看 API 原始响应"):
                    st.json(data)

                # ✅ 如果是 PENDING 状态，显示链接
                if data.get("status", {}).get("code") == "PENDING":
                    pending_url = data["status"].get("urlForDetails")
                    st.warning("⏳ 数据正在生成中，请稍候...")

                    with st.spinner("后台生成数据中（最多等待 20 秒）..."):
                        for i in range(10):  # 最多轮询 10 次，每次间隔 2 秒
                            time.sleep(2)
                            pending_res = requests.get(pending_url)
                            try:
                                pending_data = pending_res.json()
                                if isinstance(pending_data, list) and pending_data[0].get("meta", {}).get("status") == "COMPLETE":
                                    download_url = pending_data[0]["data"][0]["urlForDownload"]
                                    st.success("✅ 数据已生成，正在下载并加载数据...")

                                    # 下载 .csv.gz 文件并解压
                                    file_res = requests.get(download_url)
                                    with gzip.GzipFile(fileobj=io.BytesIO(file_res.content)) as gz:
                                        df = pd.read_csv(gz)

                                    st.success(f"✅ 获取成功，共 {len(df)} 条数据")
                                    st.dataframe(df)

                                    # 下载按钮
                                    csv_data = df.to_csv(index=False).encode("utf-8")
                                    st.download_button("📥 下载 CSV", csv_data, file_name="option_chain.csv", mime="text/csv")
                                    break

                            except Exception as e:
                                st.error(f"解析失败：{e}")
                        else:
                            st.error("❌ 超时：数据未在预期时间内准备完成，请稍后重试或手动访问链接：")
                            st.markdown(f"[🔗 手动查看结果]({pending_url})")

                # ✅ 如果 data 是有效列表
                elif isinstance(data.get("data"), list) and len(data["data"]) > 0:
                    df = pd.DataFrame(data["data"])
                    st.success(f"✅ 获取成功，共 {len(df)} 条数据")
                    st.dataframe(df)
                    st.download_button("📥 下载为 CSV", df.to_csv(index=False), "option_chain_filtered.csv", mime="text/csv")

                # ✅ 如果 data 是空列表
                elif isinstance(data.get("data"), list):
                    st.info("⚠️ 返回空数组，结构正常，但无数据")

                # ❌ 其他未知结构
                else:
                    st.warning("⚠️ 未知响应结构，请检查参数或稍后重试")

            except Exception as e:
                st.error(f"❌ 请求失败或解析出错：{e}")

elif page == "🗓️ 获取期权历史 EOD 数据":
    st.title("🗓️ 获取期权历史 EOD 数据")

    use_symbol = st.toggle("直接输入 optionSymbol（关闭则按参数自动生成）", value=False)

    if use_symbol:
        option_symbol = st.text_input("完整 optionSymbol", value="SPX   251219C04100000")
    else:
        col1, col2 = st.columns(2)
        with col1:
            ticker = st.text_input("股票代码 (Ticker)")
            expiry = st.date_input("到期日")
        with col2:
            call_put = st.selectbox("Call / Put", ["C", "P"])
            strike = st.number_input("执行价")

        # 自动拼接 OCC optionSymbol
        option_symbol = construct_option_symbol(
            ticker, expiry.strftime("%Y-%m-%d"), call_put, strike
        )
        st.code(f"🧠 自动构造的 optionSymbol: {repr(option_symbol)}")

    col_from, col_to = st.columns(2)
    with col_from:
        from_date = st.date_input("起始日期 (from_)")
    with col_to:
        to_date = st.date_input("结束日期 (to)")

    api_key = st.text_input("API Key", type="password", value=default_key, key="eod_api")

    if st.button("🚀 获取历史 IV"):
        url = "https://restapi.ivolatility.com/equities/eod/single-stock-option-raw-iv"
        params = {
            "apiKey": api_key,
            "symbol": option_symbol,
            "from": from_date.strftime("%Y-%m-%d"),   # 注意 key 叫 from
            "to": to_date.strftime("%Y-%m-%d")
        }

        with st.spinner("请求数据中..."):
            res = requests.get(url, params=params)
            if res.status_code != 200:
                st.error(f"❌ 请求失败，状态码：{res.status_code}")
            else:
                data = res.json()

                # --- 处理可能出现的 PENDING 流程 ---
                if data.get("status", {}).get("code") == "PENDING":
                    pending_url = data["status"].get("urlForDetails")
                    st.warning("⏳ 数据正在后台生成，轮询获取结果...")

                    for _ in range(10):          # 最多轮询 10 次
                        time.sleep(2)
                        pending_res = requests.get(pending_url)
                        try:
                            pending_data = pending_res.json()
                            if isinstance(pending_data, list) and \
                               pending_data[0].get("meta", {}).get("status") == "COMPLETE":
                                download_url = pending_data[0]["data"][0]["urlForDownload"]
                                file_res = requests.get(download_url)
                                with gzip.GzipFile(fileobj=io.BytesIO(file_res.content)) as gz:
                                    df = pd.read_csv(gz)
                                st.success(f"✅ 下载并解析成功，{len(df)} 行")
                                st.dataframe(df)
                                st.download_button("📥 下载 CSV", df.to_csv(index=False),
                                                   "option_iv_eod.csv", mime="text/csv")
                                break
                        except Exception as e:
                            st.error(f"解析失败：{e}")
                    else:
                        st.error("❌ 超时：数据尚未准备完成，请稍后手动访问：")
                        st.markdown(f"[🔗 结果链接]({pending_url})")

                # --- 直接返回数据的情况 ---
                elif isinstance(data.get("data"), list) and len(data["data"]) > 0:
                    df = pd.DataFrame(data["data"])
                    st.success(f"✅ 获取成功，{len(df)} 行")
                    st.dataframe(df)
                    st.download_button("📥 下载 CSV", df.to_csv(index=False),
                                       "option_iv_eod.csv", mime="text/csv")

                    # ✅ 可视化部分
                    if {"date", "volume", "price"}.issubset(df.columns):
                        df["date"] = pd.to_datetime(df["date"])
                        df_sorted  = df.sort_values("date")

                        import matplotlib.pyplot as plt
                        fig, ax1 = plt.subplots(figsize=(10, 6))

                        # --- Volume 柱状 ---
                        ax1.bar(df_sorted["date"], df_sorted["volume"], width=0.6,
                                label="Volume")
                        ax1.set_ylabel("Total Volume")
                        ax1.set_xlabel("Date")
                        ax1.tick_params(axis="x", rotation=45)

                        # --- Avg Price 折线（黄色） ---
                        ax2 = ax1.twinx()
                        ax2.plot(df_sorted["date"], df_sorted["price"],
                                color="#FFC107", marker="o", label="Avg Price ($)")   # ← 这里改黄色
                        ax2.set_ylabel("Avg Price ($)")
                        ax2.tick_params(axis="y")

                        # --- 标题 + 图例 ---
                        plt.title(f"{option_symbol} - Volume & Avg Price (EOD)")
                        lines, labels   = ax1.get_legend_handles_labels()
                        lines2, labels2 = ax2.get_legend_handles_labels()
                        ax2.legend(lines + lines2, labels + labels2, loc="upper left")

                        fig.tight_layout()
                        plt.grid(True, which="both", axis="x", linestyle="--", alpha=0.3)
                        st.pyplot(fig)

                # --- 其他情况 ---
                else:
                    st.info("⚠️ 无数据返回或未知响应结构。请检查参数。")

# ---------- 页面 4: 异常期权交易监测 ----------
elif page == "📊 异常期权交易监测":
    st.title("📊 异常期权交易监测")

    # ① 侧边栏 · Volume 参数
    with st.sidebar.expander("⚙️ Volume / Notional 筛选参数", expanded=False):
        base_date = st.sidebar.date_input("📅 选择基准日期（下载区间将回溯选定数量个工作日（默认15））", value=date.today())
        lookback_days = st.number_input("回溯工作日天数", min_value=1, max_value=252, value=15, step=1)
        win_slider  = st.slider("滚动窗口 (工作日)", 2, 10, 3)
        rel_slider  = st.slider("量比阈值", 0.5, 10.0, 3.0, 0.1)
        notional_k  = st.number_input("名义金额阈值 (千美元)", 100, 50000, 500, step=100)
        vol_abs_thresh = st.slider("绝对量阈值", 100, 100000, 1000, 100)
        vol_gt_oi   = st.checkbox("只保留 Volume > OpenInterest 的记录", value=False)

    with st.sidebar.expander("⚙️ 高级设置 / 工具", expanded=False):
        if st.button("♻️ 重新开始（清空缓存）"):
            st.cache_data.clear()        # 清空 @st.cache_data/@st.cache_resource
            st.cache_resource.clear()
            st.session_state.clear()     # 清空会话级变量

    # ② 基本输入
    symbol  = st.text_input("股票代码（如 AAPL）")
    api_key = st.text_input("API Key", type="password", value=default_key)
    scan_cp = st.radio("扫描范围", ["同时扫描 Call 与 Put", "只扫 Call", "只扫 Put"])

    # ③ STEP-1: 下载 / 刷新数据（只做一次）
    if st.button("📡 下载选定交易日数据") and symbol and api_key:
        workdays  = get_workdays(base_date, lookback_days)
        cps       = ("C", "P") if scan_cp.startswith("同时") else ("C",) if "Call" in scan_cp else ("P",)
        session   = requests.Session()
        all_rows  = []

        for trade_date in reversed(workdays):
            dte_offset = (base_date - trade_date.date()).days
            for cp in cps:
                st.write(f"⏳ {trade_date:%Y-%m-%d} {cp}  DTE=[{dte_offset},{700+dte_offset}]")
                df_day = fetch_option_data_for_day(symbol, trade_date,
                                                   dte_offset, cp, api_key)
                time.sleep(1.1)        # QPS <= 1
                if df_day is not None and not df_day.empty:
                    df_day["tradeDate"] = trade_date
                    all_rows.append(df_day)

        if not all_rows:
            st.error("❌ 没有获取到任何数据。")
        else:
            st.session_state["raw_option_df"] = pd.concat(all_rows, ignore_index=True)
            st.success(f"✅ 数据下载完毕，共 {len(st.session_state['raw_option_df'])} 行。"
                       " 现在可以在侧边栏调参数实时查看结果。")

    # ④ STEP-2: 已有数据 → 实时调参 & 显示
    if "raw_option_df" in st.session_state:
        raw_df = st.session_state["raw_option_df"]

        # 4.1 运行检测
        result_df = detect_abnormal_trades(
            raw_df,
            win=win_slider,
            rel_thresh=rel_slider,
            notional_thresh_k=notional_k,
            vol_gt_oi=vol_gt_oi
        )

        # 4.2 全量结果
        st.subheader("📈 全量记录（含 abnormal 标记）")
        st.dataframe(result_df)
        st.download_button(
            "📥 下载全部记录 CSV",
            result_df.to_csv(index=False).encode("utf-8"),
            file_name=f"{symbol}_all_records.csv",
            mime="text/csv",
        )

        # 4.3 仅异常
        abnormal_df = result_df[result_df["abnormal"]].copy()
        st.subheader("🚨 异常记录 (abnormal == True)")
        if abnormal_df.empty:
            st.info("本次参数设置下未检测到异常记录。")
        else:
            st.dataframe(abnormal_df)
            st.download_button(
                "📥 下载异常记录 CSV",
                abnormal_df.to_csv(index=False).encode("utf-8"),
                file_name=f"{symbol}_abnormal_records.csv",
                mime="text/csv",
            )

                # ---------- NEW PART · 当日异常合约 ----------
            # 1) 先保证有 option_symbol 列（沿用你之前的拼接逻辑）
            if "option_symbol" not in abnormal_df.columns:
                ticker_fixed = symbol.upper().ljust(6)
                abnormal_df["option_symbol"] = (
                    ticker_fixed +
                    pd.to_datetime(abnormal_df["expiry"]).dt.strftime("%y%m%d") +
                    abnormal_df["cp"] +
                    (abnormal_df["strike"]*1000).round().astype(int).astype(str).str.zfill(8)
                )

            # 2) 过滤出最后一个交易日
            last_day  = abnormal_df["tradeDate"].max()
            last_df   = abnormal_df[abnormal_df["tradeDate"] == last_day]

            call_df   = last_df[last_df["cp"] == "C"]
            put_df    = last_df[last_df["cp"] == "P"]

            st.subheader(f"📌 {last_day:%Y-%m-%d} 当日异常合约")

            c1, c2 = st.columns(2)

            # ----- Call 表 + 下载 -----
            with c1:
                st.markdown("### 📘 Call")
                if call_df.empty:
                    st.info("该日无 Call 异常")
                else:
                    st.dataframe(call_df)
                    st.download_button("下载 Call CSV",
                        call_df.to_csv(index=False).encode("utf-8"),
                        file_name=f"call_abn_{last_day:%Y%m%d}.csv")
            # ----- Put 表 + 下载 -----
            with c2:
                st.markdown("### 📕 Put")
                if put_df.empty:
                    st.info("该日无 Put 异常")
                else:
                    st.dataframe(put_df)
                    st.download_button("下载 Put CSV",
                        put_df.to_csv(index=False).encode("utf-8"),
                        file_name=f"put_abn_{last_day:%Y%m%d}.csv")

            # 3) 过滤出该日 abnormal=True 的记录
            if not call_df.empty or not put_df.empty:
                c3, c4 = st.columns(2)
                with c3:
                    sel_call = st.selectbox("选择 Call 合约查看走势",
                                            sorted(call_df["option_symbol"].unique()),
                                            key="sel_call")
                with c4:
                    sel_put  = st.selectbox("选择 Put 合约查看走势",
                                            sorted(put_df["option_symbol"].unique()),
                                            key="sel_put")

                if st.button("📊 同时查看两条合约走势图"):
                    colL, colR = st.columns(2)
                    with colL:
                        st.markdown(f"#### Call {sel_call}")
                        show_contract_chart(sel_call, api_key, cutoff_date=last_day)
                    with colR:
                        st.markdown(f"#### Put {sel_put}")
                        show_contract_chart(sel_put, api_key, cutoff_date=last_day)

            else:
                st.info("最后一个交易日未检测到异常合约")

            # ========== 页面 4 —— 新增：批量绘制 & 下载 ==========
            # --------------------------------------------------
            if not call_df.empty or not put_df.empty:
                st.markdown("#### 🔄 批量绘制并下载当日异常合约走势图")
                if st.button("🚀 生成全部图表"):
                    call_figs, put_figs = {}, {}
                    fail_call, fail_put = [], []
                    total = len(call_df) + len(put_df)
                    prog = st.progress(0)
                    
                    for i, opt in enumerate(call_df["option_symbol"]):
                        fig = generate_contract_chart(opt, api_key, cutoff_date=last_day)
                        if fig is not None:
                            call_figs[opt] = fig
                        else:
                            fail_call.append(opt)
                        prog.progress((i + 1) / total)
                    for j, opt in enumerate(put_df["option_symbol"], start=len(call_df)):
                        fig = generate_contract_chart(opt, api_key, cutoff_date=last_day)
                        if fig is not None:
                            put_figs[opt] = fig
                        else:
                            fail_put.append(opt)
                        prog.progress((j + 1) / total)
                    prog.empty()
                    st.session_state["call_figs_today"] = call_figs
                    st.session_state["put_figs_today"]  = put_figs

                    if fail_call or fail_put:
                        st.warning(f"抓取失败：Call {len(fail_call)} 张, Put {len(fail_put)} 张")

                today_str = last_day.strftime("%Y%m%d")
                if "call_figs_today" in st.session_state and st.session_state["call_figs_today"]:
                    st.download_button(
                        f"📥 下载 {today_str} 全部 Call 图 (ZIP)",
                        figs_to_zip(st.session_state["call_figs_today"]),
                        file_name=f"{today_str}_calls.zip",
                        mime="application/zip"
                    )
                if "put_figs_today" in st.session_state and st.session_state["put_figs_today"]:
                    st.download_button(
                        f"📥 下载 {today_str} 全部 Put 图 (ZIP)",
                        figs_to_zip(st.session_state["put_figs_today"]),
                        file_name=f"{today_str}_puts.zip",
                        mime="application/zip"
                    )

            # ---------- STEP-3: 异常合约清单 + 单合约图 ----------
            if not abnormal_df.empty:

                # 1) 如果已经有 option_symbol 列，直接用
                if "option_symbol" in abnormal_df.columns:
                    unique_opts = sorted(abnormal_df["option_symbol"].dropna().unique())

                else:
                    # —— 用页面最上面的 symbol 输入（单一 ticker）来拼接 —— #
                    ticker_input = symbol.upper().ljust(6)          # 左填充至 6 位
                    abnormal_df["option_symbol"] = (
                        ticker_input +
                        pd.to_datetime(abnormal_df["expiry"]).dt.strftime("%y%m%d") +
                        abnormal_df["cp"] +
                        (abnormal_df["strike"] * 1000).round()
                            .astype(int).astype(str).str.zfill(8)
                    )
                    unique_opts = sorted(abnormal_df["option_symbol"].unique())

                st.subheader("🧐 异常合约清单")
                sel_opt = st.selectbox("选择合约查看过去 3 个月走势", unique_opts)

                if st.button("📊 查看合约走势图"):
                    show_contract_chart(sel_opt, api_key, cutoff_date=last_day)

            # ---------- STEP-4 · 异常记录的 Volume-Weighted Payoff ----------
        if not abnormal_df.empty:

            abnormal_df["expiry"]    = pd.to_datetime(abnormal_df["expiry"],    errors="coerce")
            abnormal_df["tradeDate"] = pd.to_datetime(abnormal_df["tradeDate"], errors="coerce")

            st.markdown("### 💹 一键下载指定交易日的所有到期日 Payoff")
            available_dates = sorted(abnormal_df["tradeDate"].unique(), reverse=True)
            trade_date_sel  = st.selectbox("选择交易日", available_dates, format_func=lambda d: d.strftime("%Y-%m-%d"))

            if st.button("🚀 生成并下载该日所有 Payoff 图"):
                rows_td = abnormal_df[abnormal_df["tradeDate"] == trade_date_sel]
                payoff_figs = {}
                prog = st.progress(0)
                exps = rows_td["expiry"].unique()
                for k, exp in enumerate(exps):
                    df_one = rows_td[rows_td["expiry"] == exp]
                    fig = draw_payoff(df_one, exp, trade_date_sel.strftime("%Y-%m-%d"))
                    payoff_figs[f"{exp}"] = fig
                    prog.progress((k + 1) / len(exps))
                prog.empty()
                st.download_button(
                    f"📥 下载 {trade_date_sel:%Y%m%d} 所有到期日 Payoff (ZIP)",
                    figs_to_zip(payoff_figs),
                    file_name=f"{trade_date_sel:%Y%m%d}_payoffs.zip",
                    mime="application/zip"
                )


            # 基础列已经在前面 copy 并拼接 option_symbol，这里只需确保必要列存在
            payoff_need = {"cp", "strike", "expiry", "tradeDate", "volume", "mid"}
            if not payoff_need.issubset(abnormal_df.columns):
                st.warning("异常表缺少绘制 Payoff 所需列")
            else:
                # 1️⃣ 选择到期日 & 交易日（仅限异常记录）
                payoff_exp_opts = sorted(abnormal_df["expiry"].dt.date.unique())
                st.subheader("🎯 Payoff（仅异常合约）")
                sel_exp = st.selectbox("选择到期日", payoff_exp_opts, key="payoff_exp")

                df_exp = abnormal_df[abnormal_df["expiry"].dt.date == sel_exp]
                payoff_td_opts = sorted(df_exp["tradeDate"].dt.date.unique())
                sel_td = st.selectbox("选择交易日", payoff_td_opts, key="payoff_td")

                df_pay = df_exp[df_exp["tradeDate"].dt.date == sel_td]

                if st.button("📊 绘制异常合约 Payoff", key="payoff_btn"):

                    fig = draw_payoff(df_pay, sel_exp, sel_td)
                    st.pyplot(fig)

                    # 5️⃣ 明细表 & 下载（保持不变）
                    st.markdown("##### 用于计算的异常合约明细")
                    st.dataframe(df_pay)
                    st.download_button(
                        "📥 下载该批异常合约",
                        df_pay.to_csv(index=False).encode("utf-8"),
                        file_name=f"abn_payoff_{sel_exp}_{sel_td}.csv",
                        mime="text/csv",
                    )
        else:
            st.info("当前参数下无异常记录，无法绘制 Payoff")




    else:
        st.info("👉 请输入 Ticker 和 API Key，然后点击 “📡 下载…” 按钮先拉取数据。")

# ---------------------------------------------
elif page == "🆕 Page 5 | 特征筛选":
    st.title("🆕 Page-5 ｜ 特征工程筛选")

    # ========================= STEP-0 · 基础输入区 ========================= #
    symbol  = st.text_input("股票代码（如 AAPL）")
    base_date   = st.sidebar.date_input("📅 选择基准日期（下载区间将回溯选定数量个工作日（默认15））", value=pd.Timestamp.today())
    lookback_days  = st.number_input("回溯工作日天数", min_value=1, max_value=252, value=15, step=1)
    api_key = st.text_input("API Key", type="password", value=default_key)
    scan_cp = st.radio("扫描范围", ["同时扫描 Call 与 Put", "只扫 Call", "只扫 Put"])

    # ==================== STEP-0.1 · 新增阈值 (全部可调) ==================== #
    with st.sidebar.expander("⚙️ 特征阈值", expanded=False):
        notional_bp_input = st.number_input(
            "Notional / 动态市值 ≥ (bp)",
            min_value=0.001, max_value=100.0, value=10.0, step=0.001,
            help="成交额占动态市值（bp）下限"
        )
        dd_adv_pct_input = st.number_input(
            "|Dollar Delta| / (ADV×Underlying) ≥ (%)",
            min_value=0.01, max_value=10.0, value=1.0, step=0.01,
            help="Dollar Delta 与 ADV×标的市价 之比下限"
        )
        adv_min_input = st.number_input(
            "ADV 最低阈值 (股)",
            min_value=1_000, max_value=500_000_000, value=50_000, step=1,
            help="标的过去 30 交易日平均成交量下限"
        )

        win_slider  = st.slider("滚动窗口 (工作日)", 2, 10, 3)
        rel_slider  = st.slider("量比阈值", 0.5, 10.0, 3.0, 0.1)
        vol_abs_thresh = st.slider("绝对量阈值", 100, 100000, 1000, 100)
        vol_gt_oi   = st.checkbox("只保留 Volume > OpenInterest 的记录", value=False)

    with st.sidebar.expander("⚙️ 高级设置 / 工具", expanded=False):
        if st.button("♻️ 重新开始（清空缓存）"):
            st.cache_data.clear()        # 清空 @st.cache_data/@st.cache_resource
            st.cache_resource.clear()
            st.session_state.clear()     # 清空会话级变量

    # ========================= STEP-1 · 数据下载 ========================= #
    if st.button("📡 下载选定交易日数据") and symbol and api_key:
        workdays  = get_workdays(base_date, lookback_days)
        cps       = ("C", "P") if scan_cp.startswith("同时") else ("C",) if "Call" in scan_cp else ("P",)
        session   = requests.Session()
        all_rows  = []

        for trade_date in reversed(workdays):
            dte_offset = (base_date - trade_date.date()).days
            for cp in cps:
                st.write(f"⏳ {trade_date:%Y-%m-%d} {cp}  DTE=[{dte_offset},{700+dte_offset}]")
                df_day = fetch_option_data_for_day(symbol, trade_date,
                                                   dte_offset, cp, api_key)
                time.sleep(1.1)        # QPS <= 1
                if df_day is not None and not df_day.empty:
                    df_day["tradeDate"] = trade_date
                    all_rows.append(df_day)

        if not all_rows:
            st.error("❌ 没有获取到任何数据。")
        else:
            st.session_state["raw_option_df"] = pd.concat(all_rows, ignore_index=True)
            st.success(f"✅ 数据下载完毕，共 {len(st.session_state['raw_option_df'])} 行。"
                       " 现在可以在侧边栏调参数实时查看结果。")
            
    # ========================= STEP-2 · 实时调参 & 检测 ========================= #
    if "raw_option_df" in st.session_state:
        raw_df = st.session_state["raw_option_df"]

        # ------- 2.1 基础检测：滚动量比 + Notional K (复用第四页函数) ------- #
        shares_out = get_shares_outstanding(symbol)
        adv_series = get_adv_series(symbol, lookback_days + 35, base_date)
        adv_series['Date'] = pd.to_datetime(adv_series['Date'])

        if shares_out is None:
            st.warning(f"⚠️ 无法自动获取 {symbol} 的流通股数，请手动输入。")
            shares_out_million = st.number_input(
                "手动输入流通股数（单位：百万股）",
                min_value=0.1,
                max_value=100_000.0,
                value=100.0,       # 默认是 1 亿股
                step=0.1,
                format="%.2f"
            )
            shares_out = shares_out_million * 1_000_000

        st.info(f"✔️ 使用的流通股数为：{shares_out:,.0f} 股")

        base_df = detect_abnormal_trades_v2(
            raw_df,
            shares_out         = shares_out,
            adv_series         = adv_series,
            notional_bp_thresh = notional_bp_input,
            dd_adv_pct_thresh  = dd_adv_pct_input,
            adv_min_thresh     = adv_min_input,
            win                = win_slider,
            rel_thresh         = rel_slider,
            vol_gt_oi          = vol_gt_oi,
        )

        # 4.2 全量结果
        st.subheader("📈 全量记录（含 abnormal 标记）")
        st.dataframe(base_df)
        st.download_button(
            "📥 下载全部记录 CSV",
            base_df.to_csv(index=False).encode("utf-8"),
            file_name=f"{symbol}_all_records.csv",
            mime="text/csv",
        )

        # 4.3 仅异常
        abnormal_df = base_df[base_df["abnormal"]].copy()
        st.subheader("🚨 异常记录 (abnormal == True)")
        if abnormal_df.empty:
            st.info("本次参数设置下未检测到异常记录。")
        else:
            st.dataframe(abnormal_df)
            st.download_button(
                "📥 下载异常记录 CSV",
                abnormal_df.to_csv(index=False).encode("utf-8"),
                file_name=f"{symbol}_abnormal_records.csv",
                mime="text/csv",
            )

                # ---------- NEW PART · 当日异常合约 ----------
            # 1) 先保证有 option_symbol 列（沿用你之前的拼接逻辑）
            if "option_symbol" not in abnormal_df.columns:
                ticker_fixed = symbol.upper().ljust(6)
                abnormal_df["option_symbol"] = (
                    ticker_fixed +
                    pd.to_datetime(abnormal_df["expiry"]).dt.strftime("%y%m%d") +
                    abnormal_df["cp"] +
                    (abnormal_df["strike"]*1000).round().astype(int).astype(str).str.zfill(8)
                )

            # 2) 过滤出最后一个交易日
            last_day  = abnormal_df["tradeDate"].max()
            last_df   = abnormal_df[abnormal_df["tradeDate"] == last_day]

            call_df   = last_df[last_df["cp"] == "C"]
            put_df    = last_df[last_df["cp"] == "P"]

            st.subheader(f"📌 {last_day:%Y-%m-%d} 当日异常合约")

            c1, c2 = st.columns(2)

            # ----- Call 表 + 下载 -----
            with c1:
                st.markdown("### 📘 Call")
                if call_df.empty:
                    st.info("该日无 Call 异常")
                else:
                    st.dataframe(call_df)
                    st.download_button("下载 Call CSV",
                        call_df.to_csv(index=False).encode("utf-8"),
                        file_name=f"call_abn_{last_day:%Y%m%d}.csv")
            # ----- Put 表 + 下载 -----
            with c2:
                st.markdown("### 📕 Put")
                if put_df.empty:
                    st.info("该日无 Put 异常")
                else:
                    st.dataframe(put_df)
                    st.download_button("下载 Put CSV",
                        put_df.to_csv(index=False).encode("utf-8"),
                        file_name=f"put_abn_{last_day:%Y%m%d}.csv")

            # 3) 过滤出该日 abnormal=True 的记录
            if not call_df.empty or not put_df.empty:
                c3, c4 = st.columns(2)
                with c3:
                    sel_call = st.selectbox("选择 Call 合约查看走势",
                                            sorted(call_df["option_symbol"].unique()),
                                            key="sel_call")
                with c4:
                    sel_put  = st.selectbox("选择 Put 合约查看走势",
                                            sorted(put_df["option_symbol"].unique()),
                                            key="sel_put")

                if st.button("📊 同时查看两条合约走势图"):
                    colL, colR = st.columns(2)
                    with colL:
                        st.markdown(f"#### Call {sel_call}")
                        show_contract_chart(sel_call, api_key, cutoff_date=last_day)
                    with colR:
                        st.markdown(f"#### Put {sel_put}")
                        show_contract_chart(sel_put, api_key, cutoff_date=last_day)

            else:
                st.info("最后一个交易日未检测到异常合约")

            # ========== 页面 4 —— 新增：批量绘制 & 下载 ==========
            # --------------------------------------------------
            if not call_df.empty or not put_df.empty:
                st.markdown("#### 🔄 批量绘制并下载当日异常合约走势图")
                if st.button("🚀 生成全部图表"):
                    call_figs, put_figs = {}, {}
                    fail_call, fail_put = [], []
                    total = len(call_df) + len(put_df)
                    prog = st.progress(0)
                    
                    for i, opt in enumerate(call_df["option_symbol"]):
                        fig = generate_contract_chart(opt, api_key, cutoff_date=last_day)
                        if fig is not None:
                            call_figs[opt] = fig
                        else:
                            fail_call.append(opt)
                        prog.progress((i + 1) / total)
                    for j, opt in enumerate(put_df["option_symbol"], start=len(call_df)):
                        fig = generate_contract_chart(opt, api_key, cutoff_date=last_day)
                        if fig is not None:
                            put_figs[opt] = fig
                        else:
                            fail_put.append(opt)
                        prog.progress((j + 1) / total)
                    prog.empty()
                    st.session_state["call_figs_today"] = call_figs
                    st.session_state["put_figs_today"]  = put_figs

                    if fail_call or fail_put:
                        st.warning(f"抓取失败：Call {len(fail_call)} 张, Put {len(fail_put)} 张")

                today_str = last_day.strftime("%Y%m%d")
                if "call_figs_today" in st.session_state and st.session_state["call_figs_today"]:
                    st.download_button(
                        f"📥 下载 {today_str} 全部 Call 图 (ZIP)",
                        figs_to_zip(st.session_state["call_figs_today"]),
                        file_name=f"{today_str}_calls.zip",
                        mime="application/zip"
                    )
                if "put_figs_today" in st.session_state and st.session_state["put_figs_today"]:
                    st.download_button(
                        f"📥 下载 {today_str} 全部 Put 图 (ZIP)",
                        figs_to_zip(st.session_state["put_figs_today"]),
                        file_name=f"{today_str}_puts.zip",
                        mime="application/zip"
                    )

            # ---------- STEP-3: 异常合约清单 + 单合约图 ----------
            if not abnormal_df.empty:

                # 1) 如果已经有 option_symbol 列，直接用
                if "option_symbol" in abnormal_df.columns:
                    unique_opts = sorted(abnormal_df["option_symbol"].dropna().unique())

                else:
                    # —— 用页面最上面的 symbol 输入（单一 ticker）来拼接 —— #
                    ticker_input = symbol.upper().ljust(6)          # 左填充至 6 位
                    abnormal_df["option_symbol"] = (
                        ticker_input +
                        pd.to_datetime(abnormal_df["expiry"]).dt.strftime("%y%m%d") +
                        abnormal_df["cp"] +
                        (abnormal_df["strike"] * 1000).round()
                            .astype(int).astype(str).str.zfill(8)
                    )
                    unique_opts = sorted(abnormal_df["option_symbol"].unique())

                st.subheader("🧐 异常合约清单")
                sel_opt = st.selectbox("选择合约查看过去 3 个月走势", unique_opts)

                if st.button("📊 查看合约走势图"):
                    show_contract_chart(sel_opt, api_key, cutoff_date=last_day)

            # ---------- STEP-4 · 异常记录的 Volume-Weighted Payoff ----------
        if not abnormal_df.empty:

            abnormal_df["expiry"]    = pd.to_datetime(abnormal_df["expiry"],    errors="coerce")
            abnormal_df["tradeDate"] = pd.to_datetime(abnormal_df["tradeDate"], errors="coerce")

            st.markdown("### 💹 一键下载指定交易日的所有到期日 Payoff")
            available_dates = sorted(abnormal_df["tradeDate"].unique(), reverse=True)
            trade_date_sel  = st.selectbox("选择交易日", available_dates, format_func=lambda d: d.strftime("%Y-%m-%d"))

            if st.button("🚀 生成并下载该日所有 Payoff 图"):
                rows_td = abnormal_df[abnormal_df["tradeDate"] == trade_date_sel]
                payoff_figs = {}
                prog = st.progress(0)
                exps = rows_td["expiry"].unique()
                for k, exp in enumerate(exps):
                    df_one = rows_td[rows_td["expiry"] == exp]
                    fig = draw_payoff(df_one, exp, trade_date_sel.strftime("%Y-%m-%d"))
                    payoff_figs[f"{exp}"] = fig
                    prog.progress((k + 1) / len(exps))
                prog.empty()
                st.download_button(
                    f"📥 下载 {trade_date_sel:%Y%m%d} 所有到期日 Payoff (ZIP)",
                    figs_to_zip(payoff_figs),
                    file_name=f"{trade_date_sel:%Y%m%d}_payoffs.zip",
                    mime="application/zip"
                )


            # 基础列已经在前面 copy 并拼接 option_symbol，这里只需确保必要列存在
            payoff_need = {"cp", "strike", "expiry", "tradeDate", "volume", "mid"}
            if not payoff_need.issubset(abnormal_df.columns):
                st.warning("异常表缺少绘制 Payoff 所需列")
            else:
                # 1️⃣ 选择到期日 & 交易日（仅限异常记录）
                payoff_exp_opts = sorted(abnormal_df["expiry"].dt.date.unique())
                st.subheader("🎯 Payoff（仅异常合约）")
                sel_exp = st.selectbox("选择到期日", payoff_exp_opts, key="payoff_exp")

                df_exp = abnormal_df[abnormal_df["expiry"].dt.date == sel_exp]
                payoff_td_opts = sorted(df_exp["tradeDate"].dt.date.unique())
                sel_td = st.selectbox("选择交易日", payoff_td_opts, key="payoff_td")

                df_pay = df_exp[df_exp["tradeDate"].dt.date == sel_td]

                if st.button("📊 绘制异常合约 Payoff", key="payoff_btn"):

                    fig = draw_payoff(df_pay, sel_exp, sel_td)
                    st.pyplot(fig)

                    # 5️⃣ 明细表 & 下载（保持不变）
                    st.markdown("##### 用于计算的异常合约明细")
                    st.dataframe(df_pay)
                    st.download_button(
                        "📥 下载该批异常合约",
                        df_pay.to_csv(index=False).encode("utf-8"),
                        file_name=f"abn_payoff_{sel_exp}_{sel_td}.csv",
                        mime="text/csv",
                    )
        else:
            st.info("当前参数下无异常记录，无法绘制 Payoff")




    else:
        st.info("👉 请输入 Ticker 和 API Key，然后点击 “📡 下载…” 按钮先拉取数据。")


elif page == "🗓️ Label数据下载":
    st.title("🗓️ Label数据下载")

    # ---- Session 缓冲区 ----
    if "eod_buffer" not in st.session_state:
        st.session_state["eod_buffer"] = []

    col1, col2 = st.columns(2)
    with col1:
        ticker = st.text_input("股票代码 (Ticker)")
        expiry = st.date_input("到期日")
        trade_date  = st.date_input("交易日 (EOD 日期)")
    with col2:
        call_put = st.selectbox("Call / Put", ["C", "P"])
        strike = st.number_input("执行价")

    api_key = st.text_input("API Key", type="password", value=default_key, key="eod_api")

    # ---- 构造 optionSymbol ----
    if ticker and strike and api_key:
        option_symbol = construct_option_symbol(
            ticker, expiry.strftime("%Y-%m-%d"), call_put, strike
        )
        st.code(f"🧠 自动构造的 optionSymbol: {repr(option_symbol)}")

    if st.button("🚀 获取该日数据"):
        url = "https://restapi.ivolatility.com/equities/eod/single-stock-option-raw-iv"
        params = {
            "apiKey": api_key,
            "symbol": option_symbol,
            "from":   trade_date.strftime("%Y-%m-%d"),
            "to":     trade_date.strftime("%Y-%m-%d")
        }

        with st.spinner("请求数据中..."):
            res = requests.get(url, params=params)
            if res.status_code != 200:
                st.error(f"❌ 请求失败，状态码：{res.status_code}")
            else:
                data = res.json()

                # 直接返回数据（通常只有 1 行）
                if isinstance(data.get("data"), list) and data["data"]:
                    df = pd.DataFrame(data["data"])
                    st.success("✅ 获取成功！")
                    st.write(df)
                    # 缓存
                    st.session_state["eod_buffer"].extend(df.to_dict("records"))
                else:
                    st.warning("⚠️ 未返回数据，请检查参数或日期。")

    # ---- 汇总 & 下载 ----
    st.subheader("📊 已抓取的数据汇总")
    if st.session_state["eod_buffer"]:
        all_df = pd.DataFrame(st.session_state["eod_buffer"])
        st.dataframe(all_df)
        st.download_button("📥 下载全部 CSV",
                        all_df.to_csv(index=False).encode("utf-8"),
                        file_name="option_eod_batch.csv",
                        mime="text/csv")
        if st.button("🗑️ 清空已抓数据"):
            st.session_state["eod_buffer"] = []
    else:
        st.info("暂无数据")
import streamlit as st
import pandas as pd
import requests
import os
import time
import gzip
import io
import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime, timedelta

if os.getenv("RUNNING_IN_STREAMLIT_CLOUD") != "1":
    from dotenv import load_dotenv
    load_dotenv()

# --- 环境变量加载 ---
# load_dotenv()
default_key = os.getenv("IVOL_API_KEY", "")


@st.cache_data(ttl=86400, show_spinner=False)

# --- OCC optionSymbol 构造 ---
def construct_option_symbol(ticker: str, expiry: str, call_put: str, strike: float) -> str:
    ticker_formatted = ticker.upper().ljust(6)
    expiry_formatted = expiry.replace("-", "")[2:]
    strike_formatted = f"{int(round(strike * 1000)):08d}"
    return f"{ticker_formatted}{expiry_formatted}{call_put.upper()}{strike_formatted}"

def get_workdays(end_date: str | datetime, days: int = 15):
    """返回向前回溯的最近 N 个工作日（含 end_date 当天）"""
    date = pd.to_datetime(end_date)
    workdays = pd.bdate_range(end=date, periods=days)   # ← 不再减一天
    return workdays

def fetch_option_data_for_day(symbol, trade_date, dte_offset, cp, api_key, session):
    """返回指定日期 + cp （C/P）的 DataFrame；429 时自动指数回退"""
    url = "https://restapi.ivolatility.com/equities/eod/stock-opts-by-param"
    params = {
        "apiKey": api_key,
        "symbol": symbol.upper(),
        "tradeDate": trade_date.strftime('%Y-%m-%d'),
        "dteFrom": dte_offset,
        "dteTo": 300 + dte_offset,
        "cp": cp,                       # 必须 C 或 P
        "moneynessFrom": -100,
        "moneynessTo": 100,
        "region": "USA",
    }

    backoff = 2          # 秒
    while True:
        res = session.get(url, params=params)
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
                           rel_thresh=5.0,
                           notional_thresh_k=5000):
    """
    条件：
        • 名义金额 = volume × mid_price × 100  ≥ notional_thresh_k * 1_000
        • volume / rolling_mean(win)          ≥ rel_thresh
    参数单位：
        notional_thresh_k  -- 千美元
    """
    # ---------- 1. 列名统一 ----------
    rename_map = {
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
        "tradeDate": "tradeDate", "c_date": "tradeDate"
    }
    df = raw_df.rename(columns={c: rename_map.get(c, c) for c in raw_df.columns})
    df = df.loc[:, ~df.columns.duplicated()]  # 去重列

    # ---------- 2. 字段检查 ----------
    need = {"cp", "strike", "expiry", "volume", "Bid", "Ask", "tradeDate"}
    if not need.issubset(df.columns):
        missing = ", ".join(need - set(df.columns))
        st.warning(f"⚠️ 缺少字段：{missing}，无法计算名义金额")
        return pd.DataFrame()

    # ---------- 3. 基础清洗 ----------
    df = df[["cp", "strike", "expiry", "volume", "Bid", "Ask", "tradeDate"]].copy()
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
        g["rel"] = g["volume"] / g["roll_mean"].replace(0, np.nan)
        g["abnormal"] = (
            (g["notional"] >= notional_thresh_k * 1_000) &
            (g["rel"] >= rel_thresh)
        )
        out.append(g)

    return pd.concat(out, ignore_index=True)

def fetch_eod(option_symbol: str, api_key: str) -> pd.DataFrame:
    """
    拉取单个 option_symbol 过去 3 个月日线 EOD 数据，缓存 24 h
    """
    end   = datetime.today().date()
    start = end - timedelta(days=90)
    url   = "https://restapi.ivolatility.com/equities/eod/single-stock-option-raw-iv"
    params = {
        "apiKey": api_key,
        "symbol": option_symbol,
        "from": start.strftime("%Y-%m-%d"),
        "to":   end.strftime("%Y-%m-%d")
    }
    res = requests.get(url, params=params).json()
    if isinstance(res.get("data"), list) and res["data"]:
        return pd.DataFrame(res["data"])
    return pd.DataFrame()


def show_contract_chart(option_symbol: str, api_key: str) -> None:
    """画出合约过去 3 个月 Volume + Mid（price）"""
    df = fetch_eod(option_symbol, api_key)
    if df.empty:
        st.warning("未获取到数据")
        return

    # -------- 列名统一 -------- #
    rename_map = {
        "price": "price", "Price": "price", "mid": "price",
        "volume": "volume", "Volume": "volume"
    }
    df = df.rename(columns={c: rename_map.get(c, c) for c in df.columns})

    if {"price", "volume", "date"}.issubset(df.columns) is False:
        st.warning("回包缺少 price / volume / date 列，无法绘图")
        return

    # -------- 数据清洗 -------- #
    df["price"]  = pd.to_numeric(df["price"],  errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    df["date"]   = pd.to_datetime(df["date"],  errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")

    # -------- 绘图 -------- #
    import matplotlib.pyplot as plt
    fig, ax1 = plt.subplots(figsize=(10, 6))

    # Volume 柱状
    ax1.bar(df["date"], df["volume"], width=0.6, label="Volume")
    ax1.set_ylabel("Volume")
    ax1.tick_params(axis="x", rotation=45)

    # Mid-price 折线（黄色）
    ax2 = ax1.twinx()
    ax2.plot(df["date"], df["price"],
             color="#FFC107", marker="o", label="Mid Price ($)")
    ax2.set_ylabel("Mid Price ($)")

    # 标题 + 图例
    plt.title(f"{option_symbol} | Past 3-Month Volume & Mid")
    lines, labels   = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax2.legend(lines + lines2, labels + labels2, loc="upper left")

    fig.tight_layout()
    plt.grid(True, which="both", axis="x",
             linestyle="--", alpha=0.3)
    st.pyplot(fig)




# --- 页面选择 ---
page = st.sidebar.selectbox("选择功能", [
    "📈 获取单个期权 IV 数据（Intraday）",
    "🔍 获取期权集合（按 DTE + Moneyness/Delta）",
    "🗓️ 获取期权历史 EOD 数据",
    "📊 异常期权交易监测"
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
            if res.status_code != 200:
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
    trade_date = st.date_input("交易日期 (YYYY-MM-DD)", value="2021-12-16")
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
            ticker = st.text_input("股票代码 (Ticker)", value="SPX")
            expiry = st.date_input("到期日", value=pd.to_datetime("2025-12-19"))
        with col2:
            call_put = st.selectbox("Call / Put", ["C", "P"])
            strike = st.number_input("执行价", value=4100.0)

        # 自动拼接 OCC optionSymbol
        option_symbol = construct_option_symbol(
            ticker, expiry.strftime("%Y-%m-%d"), call_put, strike
        )
        st.code(f"🧠 自动构造的 optionSymbol: {repr(option_symbol)}")

    col_from, col_to = st.columns(2)
    with col_from:
        from_date = st.date_input("起始日期 (from_)", value=pd.to_datetime("2022-09-29"))
    with col_to:
        to_date = st.date_input("结束日期 (to)", value=pd.to_datetime("2022-10-30"))

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
        win_slider  = st.slider("滚动窗口 (工作日)", 2, 10, 3)
        rel_slider  = st.slider("量比阈值", 1.5, 10.0, 5.0, 0.1)
        notional_k  = st.number_input("名义金额阈值 (千美元)", 500, 50000, 500, step=100)

    # ② 基本输入
    symbol  = st.text_input("股票代码（如 AAPL）")
    api_key = st.text_input("API Key", type="password", value=default_key)
    scan_cp = st.radio("扫描范围", ["同时扫描 Call 与 Put", "只扫 Call", "只扫 Put"])

    # ③ STEP-1: 下载 / 刷新数据（只做一次）
    if st.button("📡 下载最近 15 个交易日数据") and symbol and api_key:
        today     = datetime.today().date()
        workdays  = get_workdays(today, 15)
        cps       = ("C", "P") if scan_cp.startswith("同时") else ("C",) if "Call" in scan_cp else ("P",)
        session   = requests.Session()
        all_rows  = []

        for trade_date in reversed(workdays):
            dte_offset = (today - trade_date.date()).days
            for cp in cps:
                st.write(f"⏳ {trade_date:%Y-%m-%d} {cp}  DTE=[{dte_offset},{300+dte_offset}]")
                df_day = fetch_option_data_for_day(symbol, trade_date,
                                                   dte_offset, cp, api_key, session)
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
            notional_thresh_k=notional_k
        )

        # 4.2 全量结果
        st.subheader("📈 全量记录（含 abnormal 标记）")
        st.dataframe(result_df)
        st.download_button(
            "📥 下载全部记录 CSV",
            result_df.to_csv(index=False).encode("utf-8"),
            file_name="all_records.csv",
            mime="text/csv",
        )

        # 4.3 仅异常
        abnormal_df = result_df[result_df["abnormal"]]
        st.subheader("🚨 异常记录 (abnormal == True)")
        if abnormal_df.empty:
            st.info("本次参数设置下未检测到异常记录。")
        else:
            st.dataframe(abnormal_df)
            st.download_button(
                "📥 下载异常记录 CSV",
                abnormal_df.to_csv(index=False).encode("utf-8"),
                file_name="abnormal_records.csv",
                mime="text/csv",
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
                    show_contract_chart(sel_opt, api_key)



    else:
        st.info("👉 请输入 Ticker 和 API Key，然后点击 “📡 下载…” 按钮先拉取数据。")

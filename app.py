import streamlit as st
import pandas as pd
import requests
import os
import time
import gzip
import io
import matplotlib.pyplot as plt
from datetime import date
import re

from utils.calc_metrics import construct_option_symbol, get_workdays
from utils.equity_enrich import get_adv_series, get_shares_outstanding
from utils.plotting import figs_to_zip, draw_payoff
from wrappers.ivol_streamlit import fetch_option_data_for_day, detect_abnormal_trades, detect_abnormal_trades_v2, show_contract_chart, generate_contract_chart

if os.getenv("RUNNING_IN_STREAMLIT_CLOUD") != "1":
    from dotenv import load_dotenv
    load_dotenv()

# --- 环境变量加载 ---
# load_dotenv()
default_key = os.getenv("IVOL_API_KEY", "")

# --- 页面选择 ---
page = st.sidebar.selectbox("选择功能", [
    "📈 获取单个期权 IV 数据（Intraday）",
    "🔍 获取期权集合（按 DTE + Moneyness/Delta）",
    "🗓️ 获取期权历史 EOD 数据",
    "📊 异常期权交易监测", 
    "🆕 Page 5 | 特征筛选",
    "🗓️ Label数据下载", 
    "🆕 Page 6 | 批量下载"
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
            vol_abs_thresh = vol_abs_thresh,
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
            vol_abs_thresh     = vol_abs_thresh,
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


# ---------------------------------------------
elif page == "🆕 Page 6 | 批量下载":
    st.title("🆕 Page-6 ｜ 批量下载数据")

    # ========================= STEP-0 · 基础输入区 ========================= #
    tickers_raw = st.text_area(
        "输入股票代码（逗号、空格或换行分隔）",
        value="AAPL, MSFT, TSLA"
    )
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("📅 起始日期", value=pd.Timestamp.today() - pd.Timedelta(days=5))
    with col2:
        end_date   = st.date_input("📅 结束日期", value=pd.Timestamp.today())

    api_key = st.text_input("API Key", type="password", value=default_key)
    scan_cp = st.radio("扫描范围", ["同时扫描 Call 与 Put", "只扫 Call", "只扫 Put"])

    # ---------- 批量下载按钮 ----------
    if st.button("📡 批量下载") and tickers_raw and api_key:

        # 0. 校验日期
        if start_date > end_date:
            st.error("❌ 起始日期不能晚于结束日期")
            st.stop()

        # ① 解析多 ticker
        tickers = [
            t.strip().upper()
            for t in re.split(r"[,\s]+", tickers_raw)
            if t.strip()
        ]
        if not tickers:
            st.error("❌ 请输入至少一个有效的股票代码")
            st.stop()

        # ② 生成日期 / CP 组合
        workdays = pd.bdate_range(start=start_date, end=end_date)
        if workdays.empty:
            st.error("❌ 该区间内没有任何工作日")
            st.stop()

        cps = ("C", "P") if scan_cp.startswith("同时") else ("C",) if "Call" in scan_cp else ("P",)

        # ③ 批量循环下载
        session = requests.Session()
        all_rows = []
        total_tasks = len(tickers) * len(workdays) * len(cps)
        progress = st.progress(0)
        done = 0

        for symbol in tickers:
            for trade_date in reversed(workdays):
                dte_offset = (end_date - trade_date.date()).days
                for cp in cps:
                    st.write(f"⏳ {symbol:<6} {trade_date:%Y-%m-%d} {cp}  "
                             f"DTE=[{dte_offset}, {700 + dte_offset}]")
                    df_day = fetch_option_data_for_day(
                        symbol, trade_date, dte_offset, cp, api_key)
                    time.sleep(1.1)  # QPS ≤ 1
                    if df_day is not None and not df_day.empty:
                        df_day["tradeDate"] = trade_date
                        df_day["symbol"] = symbol        # <— 额外记录 ticker
                        all_rows.append(df_day)

                    done += 1
                    progress.progress(done / total_tasks)

        # ④ 结果展示 / 下载
        if not all_rows:
            st.warning("⚠️ 没有下载到任何数据。")
        else:
            big_df = pd.concat(all_rows, ignore_index=True)
            st.success(f"✅ 下载完成，共 {len(big_df):,} 行。")
            st.dataframe(big_df)

            st.download_button(
                "📥 下载汇总 CSV",
                big_df.to_csv(index=False).encode("utf-8"),
                file_name=f"bulk_option_records_{start_date:%Y%m%d}_{end_date:%Y%m%d}.csv",
                mime="text/csv",
            )
import yfinance as yf
import pandas as pd
from datetime import timedelta
from pandas.tseries.offsets import BDay

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
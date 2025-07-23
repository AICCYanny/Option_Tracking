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
    
def enrich_with_yf(data: pd.DataFrame, n_days: int) -> pd.DataFrame:
    """
    扩充数据，通过 yfinance 下载历史数据最早日期前 2 * int 天的股价数据并计算 adv 加入df

    Parameters
    ----------
    data : pd.Dataframe
        期权数据
    n_days : int
        回溯工作日数量 / 2

    Return
    ------
    pd.Dataframe 
        新增股价数据以及 adv
    """
    # 1. 统一 datetime 格式
    df = data.copy()
    df['date'] = pd.to_datetime(df['date'])

    # 2. 取所有 ticker、计算下载区间
    tickers = df['symbol'].unique().tolist()
    start = df['date'].min() - BDay(n_days * 2)
    end   = df['date'].max() + timedelta(days=1)

    # 3. 一次性下载所有 ticker 的历史数据
    raw = yf.download(
        tickers,
        start=start,
        end=end,
        progress=False,
        auto_adjust=False,
        threads=False,
    )

    # 4. 把多级 columns 展平，并把行索引变成列
    raw=raw.stack(level='Ticker', future_stack=True).reset_index()
    raw = raw.rename(columns={
        'Date':      'date',
        'Ticker':    'symbol',
        'Open':      'open_stock',
        'High':      'high_stock',
        'Low':       'low_stock',
        'Close':     'close_stock',
        'Adj Close': 'adj_close_stock',
        'Volume':    'volume_stock',
    })

    # 5. 计算每个 ticker 上的 30 日 ADV
    raw['adv'] = (
        raw
        .sort_values('date')
        .groupby('symbol')['volume_stock']
        .transform(lambda v: v.rolling(window=30, min_periods=30).mean())
    )

    # 6. 最后和原 df 通过 symbol+date 左连接
    #    duplicate rows 会自动复制对应的 price/adv
    out = pd.merge(
        df,
        raw,
        on=['symbol', 'date'],
        how='left'
    )
    return out
    
import pandas as pd
from datetime import datetime

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


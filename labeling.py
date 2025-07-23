from pathlib import Path
import pandas as pd
import numpy as np
from utils.equity_enrich import get_shares_outstanding, enrich_with_yf

# ---------- 0. 路径 ----------
BASE_DIR   = Path(__file__).resolve().parent
SAMPLE_DIR = BASE_DIR / "Data/raw/options"
LABEL_DIR  = BASE_DIR / "Data/raw/Labels"
RESULT_DIR = BASE_DIR / "Data/clean"

for d in (SAMPLE_DIR, LABEL_DIR, RESULT_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ---------- 1. 读入并打 label ----------
def load_and_label(sample_dir: str, label_dir: str) -> pd.DataFrame:
    sample_path = Path(SAMPLE_DIR)
    label_path = Path(LABEL_DIR)

    # Step 1:读取所有期权数据
    sample_dfs = [pd.read_parquet(p) for p in sample_path.rglob("*.parquet")]
    sample_df = pd.concat(sample_dfs, ignore_index=True)

    # Step 2: 读取所有 label 文件
    label_dfs = [pd.read_csv(p) for p in label_path.glob("*.csv")]
    label_df = pd.concat(label_dfs, ignore_index=True)

    # Step 3: 统一列名
    sample_df.columns = (
        sample_df.columns
        .str.strip()
        .str.lower()
        .str.replace(' ', '_')
    )
    sample_df = sample_df.rename(columns={'c_date': 'date'})
    label_df.columns = (
        label_df.columns
        .str.strip()
        .str.lower()
        .str.replace(' ', '_')
    )
    label_df = label_df.rename(columns={'c_date': 'date'})

    # Step 4: 打 Label
    keys = ['date', 'option_symbol']
    label_df = label_df[keys]
    label_df['label'] = 1
    sample_df = (
        sample_df
        .merge(
            label_df,
            on=keys,
            how='left'
        )
    )
    sample_df['label'] = sample_df['label'].fillna(0).astype(int)

    return sample_df

# ---------- 2. 增加股价数据 ----------
def enrich_data(df: pd.DataFrame) -> pd.DataFrame:
    # 增加流通股数
    df['shares_outstanding'] = df.groupby('symbol')['symbol'].transform(lambda x: get_shares_outstanding(x.name))
    # 增加股价数据 ADV
    df = enrich_with_yf(df, 20)

    return df

# ---------- 3. 特征工程 ----------
def feature_engineering(df: pd.DataFrame) -> pd.DataFrame:
    """
    moneyness:       价位深浅
    notional:        成交额
    mcap:            市值
    adv_mcap: ADV    估算市值
    prem_pct_mcap:   成交额市值百分比
    prem_pct_adv:    成交额 adv 估算市值百分比
    delta_dollar:    Delta 对冲金额
    dhedge_pct_mcap: Delta 对冲金额市值百分比
    dhedge_pct_adv:  Delta 对冲金额 adv 估算市值百分比
    gamma_dollar:    Gamma 对冲金额
    ghedge_pct_mcap: Gamma 对冲金额市值百分比
    ghedge_pct_adv:  Gamma 对冲金额 adv 估算市值百分比
    """
    direction = df['call_put'].map({'C': 1, 'P': -1})
    df['moneyness']       = direction * np.log(df['price_strike'] / df['close_stock'])
    df['notional']        = df['volume'] * df['price']
    df['mcap']            = df['close_stock'] * df['shares_outstanding']
    df['adv_mcap']        = df['adv'] * df['close_stock']
    df['prem_pct_mcap']   = df['notional'] / df['mcap'] * 100
    df['prem_pct_adv']    = df['notional'] / df['adv_mcap'] * 100
    df['delta_dollar']    = df['delta'] * df['volume'] * df['close_stock'] * 100
    df['dhedge_pct_mcap'] = df['delta_dollar'] / df['mcap']
    df['dhedge_pct_adv']  = df['delta_dollar'] / df['adv_mcap'] * 100
    df['gamma_dollar']    = df['gamma'] * df['volume'] * df['close_stock'] ** 2 * 100
    df['ghedge_pct_mcap'] = df['gamma_dollar'] / df['mcap']
    df['ghedge_pct_adv']  = df['gamma_dollar'] / df['adv_mcap']

    return df

# ---------- 4. 主流程 ----------
def main():
    df = load_and_label(SAMPLE_DIR, LABEL_DIR)
    df = enrich_data(df)
    df = feature_engineering(df)

    df.to_csv(RESULT_DIR / "feature_table.csv", index=False)

    # --- 简单 sanity check ---
    cnts = (df.groupby("symbol")["label"]
                     .value_counts())
    print("\n>>> 正常/异常成交笔数：")
    print(cnts)

    print("\n>>> feature_table.csv 已写入。")

if __name__ == "__main__":
    main()
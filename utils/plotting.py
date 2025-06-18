import io
import zipfile
import matplotlib as plt
import pandas as pd
import numpy as np

def figs_to_zip(figs: dict[str, "plt.Figure"]) -> bytes:
    """把 {'name': Figure, ...} 打包成 ZIP → bytes"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, fig in figs.items():
            img = io.BytesIO()
            fig.savefig(img, format="png", dpi=150, bbox_inches="tight")
            zf.writestr(f"{name}.png", img.getvalue())
    return buf.getvalue()

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
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

@dataclass
class OptionParts:
    underlying: str     # TSLA
    expiry: str         # YYYY-MM-DD
    right: str          # 'C' or 'P'
    strike: float       # 260.0

def parse_option_symbol(s: str) -> OptionParts:
    """
    Resolve option symbol in the form of 'TSLA251003P00260000'
    [underlying][YYMMDD][C|P][strike(8)]
    """
    if not s or len(s) < 16:
        raise ValueError(f"option symbol too short: {s!r}")
    
    tail = s[-15:]
    underlying = s[:-15]

    yy = int(tail[0:2])
    mm = int(tail[2:4])
    dd = int(tail[4:6])
    right = tail[6].upper()
    if right not in ('C', 'P'):
        raise ValueError(f"invalid right in {s!r}")
    
    strike_raw = tail[7:]
    if not strike_raw.isdigit():
        raise ValueError(f"invalid strike digits in {s!r}")
    strike = int(strike_raw) / 1000.0

    yyyy = 2000 + yy
    expiry_iso = date(yyyy, mm, dd).isoformat()

    return OptionParts(
        underlying=underlying,
        expiry=expiry_iso,
        right=right,
        strike=strike,
    )

# --------------- Greeks -----------------
@dataclass
class GreeksParts:
    date: str       # YYYY-MM-DD
    expiry: str     # YYYY-MM-DD
    dte: int        # days to expiry
    strike: float
    side: Literal['C', 'P']
    option_symbol: str
    delta: float
    gamma: float
    theta: float
    rho: float
    vega: float
    vanna: float
    charm: float
    volatility: float

def get_greeks(greeks: dict, strike: float, pc: Literal['C', 'P']) -> GreeksParts:
    """Resolve greeks from initial dictionary"""
    prefix = 'call_' if pc == 'C' else 'put_'
    for row in greeks['data']:
        if float(row['strike']) == strike:
            date = datetime.strptime(row['date'], "%Y-%m-%d")
            expiry = datetime.strptime(row['expiry'], "%Y-%m-%d")
            dte = (expiry - date).days

            def num(k: str) -> float:
                return float(row[f"{prefix}{k}"])

            return GreeksParts(
                date=row['date'],
                expiry=row['expiry'],
                dte=dte,
                strike=float(row['strike']),
                side=pc,
                option_symbol=row[f"{prefix}option_symbol"],
                delta=num('delta'),
                gamma=num('gamma'),
                theta=num('theta'),
                rho=num('rho'),
                vega=num('vega'),
                vanna=num('vanna'),
                charm=num('charm'),
                volatility=num('volatility'),
            )
    raise ValueError(f"Contract not found for {pc} strike={strike}")
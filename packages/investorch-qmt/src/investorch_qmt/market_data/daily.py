"""Strict raw daily-row normalization shared by live and completed history reads."""

import math
from datetime import datetime
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
FIELDS = ("time", "open", "high", "low", "close", "volume", "amount", "preClose", "suspendFlag")


def normalize_daily_row(values, trading_date):
    row = {field: float(values[field]) for field in FIELDS}
    if not all(math.isfinite(value) for value in row.values()):
        raise ValueError("Nonfinite daily field")
    if datetime.fromtimestamp(row["time"] / 1000, SHANGHAI).date() != trading_date:
        raise ValueError("Daily row has a different trading date")
    if row["suspendFlag"] not in (-1, 0, 1):
        raise ValueError("Invalid suspension flag")
    suspended = row["suspendFlag"] == 1
    if row["volume"] < 0 or row["amount"] < 0 or row["preClose"] <= 0:
        raise ValueError("Invalid volume/turnover/previous close")
    prices = [row[field] for field in ("open", "high", "low", "close")]
    if suspended:
        if row["volume"] != 0 or row["amount"] != 0 or min(prices) < 0:
            raise ValueError("Suspended row carries trades or invalid prices")
    elif min(prices) <= 0 or row["high"] < max(prices) or row["low"] > min(prices):
        raise ValueError("Invalid active daily prices")
    return {
        "datetime": int(trading_date.strftime("%Y%m%d")) * 1000000,
        **{field: row[field] for field in ("open", "high", "low", "close")},
        # Official native daily example: amount/volume implies 100-share hands.
        # https://dict.thinktrader.net/dictionary/stock.html
        "volume": row["volume"] * 100,
        "total_turnover": row["amount"],
        "prev_close": row["preClose"],
        "suspended": suspended,
    }

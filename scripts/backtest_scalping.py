from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv() -> None:
    env_file = ROOT / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def dec(value: object) -> Decimal:
    return Decimal(str(value))


def ema(values: list[Decimal], period: int) -> list[Decimal]:
    if not values:
        return []
    multiplier = Decimal("2") / Decimal(period + 1)
    current = values[0]
    result = []
    for value in values:
        current = (value - current) * multiplier + current
        result.append(current)
    return result


def rsi(values: list[Decimal], period: int = 14) -> list[Decimal]:
    if len(values) < period + 1:
        return [Decimal("50")] * len(values)
    gains = [Decimal("0")]
    losses = [Decimal("0")]
    for index in range(1, len(values)):
        change = values[index] - values[index - 1]
        gains.append(max(change, Decimal("0")))
        losses.append(abs(min(change, Decimal("0"))))
    result = [Decimal("50")] * len(values)
    for index in range(period, len(values)):
        avg_gain = sum(gains[index - period + 1:index + 1]) / Decimal(period)
        avg_loss = sum(losses[index - period + 1:index + 1]) / Decimal(period)
        result[index] = Decimal("100") if avg_loss == 0 else Decimal("100") - (Decimal("100") / (Decimal("1") + avg_gain / avg_loss))
    return result


def atr(candles: list[dict], period: int = 14) -> list[Decimal]:
    highs = [dec(item["high"]) for item in candles]
    lows = [dec(item["low"]) for item in candles]
    closes = [dec(item["close"]) for item in candles]
    if len(candles) < 2:
        return [Decimal("0")] * len(candles)
    true_ranges = [highs[0] - lows[0]]
    for index in range(1, len(candles)):
        true_ranges.append(max(highs[index] - lows[index], abs(highs[index] - closes[index - 1]), abs(lows[index] - closes[index - 1])))
    result = [Decimal("0")] * len(candles)
    for index in range(period - 1, len(candles)):
        result[index] = sum(true_ranges[index - period + 1:index + 1]) / Decimal(period)
    return result


def average(values: list[Decimal]) -> Decimal:
    return sum(values) / Decimal(len(values)) if values else Decimal("0")


def fetch_klines(symbol: str, interval: str, limit: int) -> list[dict]:
    params = urllib.parse.urlencode({"symbol": symbol, "interval": interval, "limit": str(limit)})
    url = f"https://fapi.binance.com/fapi/v1/klines?{params}"
    with urllib.request.urlopen(url, timeout=20) as response:
        rows = json.loads(response.read().decode("utf-8"))
    return [
        {
            "open_time": row[0],
            "open": row[1],
            "high": row[2],
            "low": row[3],
            "close": row[4],
            "volume": row[5],
            "close_time": row[6],
        }
        for row in rows
    ]


def volume_ok(volumes: list[Decimal]) -> bool:
    if len(volumes) < 21:
        return True
    return volumes[-1] >= (sum(volumes[-21:-1]) / Decimal("20")) * dec(os.getenv("VOLUME_MULTIPLIER", "1.2"))


def signal_at(candles: list[dict], trend_candles: list[dict]) -> tuple[str | None, dict]:
    closes = [dec(item["close"]) for item in candles]
    opens = [dec(item["open"]) for item in candles]
    volumes = [dec(item["volume"]) for item in candles]
    trend_closes = [dec(item["close"]) for item in trend_candles]
    fast = ema(closes, int(os.getenv("EMA_FAST", "9")))
    slow = ema(closes, int(os.getenv("EMA_SLOW", "21")))
    trend_fast = ema(trend_closes, int(os.getenv("TREND_EMA_FAST", "21")))
    trend_slow = ema(trend_closes, int(os.getenv("TREND_EMA_SLOW", "55")))
    rsi_values = rsi(closes, int(os.getenv("RSI_PERIOD", "14")))
    trend_rsi_values = rsi(trend_closes, int(os.getenv("RSI_PERIOD", "14")))
    atr_values = atr(candles, int(os.getenv("ATR_PERIOD", "14")))
    atr_now = atr_values[-1]
    atr_avg = average([value for value in atr_values[-21:] if value > 0])
    volatility_ok = atr_avg == 0 or (
        atr_now >= atr_avg * dec(os.getenv("ATR_MIN_MULTIPLIER", "0.75"))
        and atr_now <= atr_avg * dec(os.getenv("ATR_MAX_MULTIPLIER", "1.8"))
    )
    last_close = closes[-1]
    buy_score = 0
    sell_score = 0
    trend_up = trend_fast[-1] > trend_slow[-1] and trend_closes[-1] > trend_fast[-1]
    trend_down = trend_fast[-1] < trend_slow[-1] and trend_closes[-1] < trend_fast[-1]
    if trend_up:
        buy_score += 2
    if trend_down:
        sell_score += 2
    if fast[-1] > slow[-1]:
        buy_score += 1
    if fast[-1] < slow[-1]:
        sell_score += 1
    if rsi_values[-1] >= dec(os.getenv("BUY_RSI", "55")) and trend_rsi_values[-1] >= dec(os.getenv("TREND_BUY_RSI", "52")):
        buy_score += 1
    if rsi_values[-1] <= dec(os.getenv("SELL_RSI", "45")) and trend_rsi_values[-1] <= dec(os.getenv("TREND_SELL_RSI", "48")):
        sell_score += 1
    if last_close > opens[-1]:
        buy_score += 1
    if last_close < opens[-1]:
        sell_score += 1
    if len(closes) >= 6 and last_close > max(closes[-6:-1]):
        buy_score += 1
    if len(closes) >= 6 and last_close < min(closes[-6:-1]):
        sell_score += 1
    if volume_ok(volumes):
        buy_score += 1
        sell_score += 1
    if not volatility_ok:
        buy_score -= 2
        sell_score -= 2
    if env_bool("DISABLE_COUNTER_TREND", "true"):
        if trend_down:
            buy_score -= 3
        if trend_up:
            sell_score -= 3
    threshold = int(os.getenv("SIGNAL_SCORE_THRESHOLD", "5"))
    edge = int(os.getenv("SIGNAL_SCORE_MIN_EDGE", "2"))
    side = None
    if buy_score >= threshold and buy_score - sell_score >= edge:
        side = "BUY"
    elif env_bool("ALLOW_SHORT", "true") and sell_score >= threshold and sell_score - buy_score >= edge:
        side = "SELL"
    return side, {"buy_score": buy_score, "sell_score": sell_score, "close": last_close}


def run() -> None:
    load_dotenv()
    symbol = os.getenv("TRADE_SYMBOL", "BTCUSDT").strip().upper()
    interval = os.getenv("SCALPING_TIMEFRAME", "5m")
    trend_interval = os.getenv("TREND_TIMEFRAME", "15m")
    candles = fetch_klines(symbol, interval, int(os.getenv("BACKTEST_KLINE_LIMIT", "1000")))
    trends = fetch_klines(symbol, trend_interval, int(os.getenv("BACKTEST_TREND_LIMIT", "500")))
    tp = dec(os.getenv("TP_PERCENT", "0.35")) / Decimal("100")
    sl = dec(os.getenv("SL_PERCENT", "0.25")) / Decimal("100")
    leverage = dec(os.getenv("DEFAULT_LEVERAGE", "1"))
    max_bars = max(1, int(dec(os.getenv("MAX_HOLD_SECONDS", "900")) / Decimal("300")))
    cooldown_ms = int(os.getenv("ENTRY_COOLDOWN_SECONDS", "1800")) * 1000
    last_entry_time = 0
    trades = []
    for index in range(120, len(candles) - max_bars - 1):
        if candles[index]["open_time"] - last_entry_time < cooldown_ms:
            continue
        trend_slice = [row for row in trends if row["close_time"] <= candles[index]["close_time"]]
        if len(trend_slice) < 60:
            continue
        side, info = signal_at(candles[: index + 1], trend_slice)
        if not side:
            continue
        entry = info["close"]
        exit_price = dec(candles[index + max_bars]["close"])
        outcome = "TIME"
        for future in candles[index + 1:index + max_bars + 1]:
            high = dec(future["high"])
            low = dec(future["low"])
            if side == "BUY":
                if low <= entry * (Decimal("1") - sl):
                    exit_price = entry * (Decimal("1") - sl)
                    outcome = "SL"
                    break
                if high >= entry * (Decimal("1") + tp):
                    exit_price = entry * (Decimal("1") + tp)
                    outcome = "TP"
                    break
            else:
                if high >= entry * (Decimal("1") + sl):
                    exit_price = entry * (Decimal("1") + sl)
                    outcome = "SL"
                    break
                if low <= entry * (Decimal("1") - tp):
                    exit_price = entry * (Decimal("1") - tp)
                    outcome = "TP"
                    break
        raw_percent = ((exit_price - entry) / entry) * Decimal("100")
        if side == "SELL":
            raw_percent = -raw_percent
        pnl_percent = raw_percent * leverage
        trades.append({"side": side, "outcome": outcome, "pnl_percent": pnl_percent, **info})
        last_entry_time = candles[index]["open_time"]
    wins = [trade for trade in trades if trade["pnl_percent"] > 0]
    losses = [trade for trade in trades if trade["pnl_percent"] <= 0]
    total = sum((trade["pnl_percent"] for trade in trades), Decimal("0"))
    print(f"Symbol: {symbol} | Entry TF: {interval} | Trend TF: {trend_interval}")
    print(f"Data candles: {len(candles)} | Trades: {len(trades)} | Winrate: {(len(wins) / len(trades) * 100) if trades else 0:.2f}%")
    print(f"Total PnL estimasi margin: {total:.2f}% | Avg/trade: {(total / len(trades)) if trades else Decimal('0'):.2f}%")
    print(f"TP: {sum(1 for trade in trades if trade['outcome'] == 'TP')} | SL: {sum(1 for trade in trades if trade['outcome'] == 'SL')} | TIME: {sum(1 for trade in trades if trade['outcome'] == 'TIME')}")
    print("Last 5 trades:")
    for trade in trades[-5:]:
        print(f"  {trade['side']} {trade['outcome']} pnl={trade['pnl_percent']:.2f}% score={trade['buy_score']}/{trade['sell_score']} close={trade['close']}")


if __name__ == "__main__":
    run()

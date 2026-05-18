from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_DOWN
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import websockets

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
DATA_DIR = ROOT / "data"
MEMORY_FILE = DATA_DIR / "conversations.jsonl"
TRADE_STATE_FILE = DATA_DIR / "trade_state.json"
TRADE_MEMORY: dict[str, dict[str, int]] = {}
SIGNAL_NOTIFY_MEMORY: dict[str, int] = {}
AUTO_SYMBOL_MEMORY: dict[str, object] = {"ts": 0, "symbols": []}
SIGNAL_CACHE: dict[str, dict[str, object]] = {}
FUTURES_SYMBOL_MEMORY: dict[str, object] = {"ts": 0, "symbols": set()}
PROFIT_MEMORY_FILE = DATA_DIR / "position_profit_memory.json"

def load_position_profit_memory() -> dict[str, Decimal]:
    try:
        if PROFIT_MEMORY_FILE.exists():
            with open(PROFIT_MEMORY_FILE, "r") as f:
                data = json.load(f)
                return {k: Decimal(str(v)) for k, v in data.items()}
    except Exception as e:
        print(f"[PROFIT MEMORY] Gagal load profit memory: {e}")
    return {}

def save_position_profit_memory(mem: dict[str, Decimal]) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        serializable = {k: float(v) for k, v in mem.items()}
        with open(PROFIT_MEMORY_FILE, "w") as f:
            json.dump(serializable, f, indent=2)
    except Exception as e:
        print(f"[PROFIT MEMORY] Gagal save profit memory: {e}")

POSITION_PROFIT_MEMORY: dict[str, Decimal] = load_position_profit_memory()
BINANCE_WS_MANAGER: BinanceWebSocketManager | None = None


def env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def auto_scalping_enabled() -> bool:
    return env_bool("AUTO_SCALPING", "false")


def cached_auto_signal(symbol: str, force_refresh: bool = False) -> dict:
    clean_symbol = normalize_symbol(symbol)
    ttl_seconds = int(os.getenv("SIGNAL_CACHE_TTL_SECONDS", "60"))
    now = int(time.time())
    cached = SIGNAL_CACHE.get(clean_symbol)
    if (
        not force_refresh
        and cached
        and now - int(cached.get("ts", 0)) < ttl_seconds
        and isinstance(cached.get("signal"), dict)
    ):
        return dict(cached["signal"])
    signal = generate_auto_signal(clean_symbol)
    SIGNAL_CACHE[clean_symbol] = {"ts": now, "signal": signal}
    return signal


def configured_scalping_symbols() -> list[str]:
    raw_symbols = os.getenv("SCALPING_SYMBOLS", "XAUUSDT").strip()
    if raw_symbols.upper() == "AUTO":
        return auto_scalping_symbols()
    return [normalize_symbol(s) for s in raw_symbols.split(",") if s.strip()]


def auto_scalping_symbols() -> list[str]:
    now = int(time.time())
    ttl_seconds = int(os.getenv("AUTO_SYMBOL_REFRESH_SECONDS", "300"))
    cached_symbols = AUTO_SYMBOL_MEMORY.get("symbols")
    cached_ts = int(AUTO_SYMBOL_MEMORY.get("ts", 0))
    if isinstance(cached_symbols, list) and cached_symbols and now - cached_ts < ttl_seconds:
        return [str(symbol) for symbol in cached_symbols]

    client = BinanceClient(symbol=os.getenv("TRADE_SYMBOL", "BTCUSDT"))
    valid_symbols = valid_futures_symbols(client)
    rows = client.public_get("/fapi/v1/ticker/24hr")
    if not isinstance(rows, list):
        return [normalize_symbol(os.getenv("TRADE_SYMBOL", "BTCUSDT"))]

    min_volume = Decimal(os.getenv("AUTO_SYMBOL_MIN_QUOTE_VOLUME", "20000000"))
    limit = int(os.getenv("AUTO_SYMBOL_LIMIT", "8"))
    mode = os.getenv("AUTO_SYMBOL_MODE", "gainers").strip().lower()
    blacklist = {
        item.strip().upper()
        for item in os.getenv("AUTO_SYMBOL_BLACKLIST", "USDCUSDT,FDUSDUSDT").split(",")
        if item.strip()
    }
    candidates: list[dict] = []
    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        if not symbol.endswith("USDT") or symbol in blacklist:
            continue
        if valid_symbols and symbol not in valid_symbols:
            continue
        try:
            quote_volume = Decimal(str(row.get("quoteVolume", "0")))
            change = Decimal(str(row.get("priceChangePercent", "0")))
        except Exception:
            continue
        if quote_volume < min_volume:
            continue
        candidates.append({"symbol": symbol, "change": change, "abs_change": abs(change), "volume": quote_volume})

    if mode == "movers":
        candidates.sort(key=lambda item: (item["abs_change"], item["volume"]), reverse=True)
    else:
        candidates.sort(key=lambda item: (item["change"], item["volume"]), reverse=True)
    symbols = [str(item["symbol"]) for item in candidates[:limit]]
    if not symbols:
        symbols = [normalize_symbol(os.getenv("TRADE_SYMBOL", "BTCUSDT"))]
    AUTO_SYMBOL_MEMORY["ts"] = now
    AUTO_SYMBOL_MEMORY["symbols"] = symbols
    return symbols


def valid_futures_symbols(client: "BinanceClient") -> set[str]:
    now = int(time.time())
    cached_symbols = FUTURES_SYMBOL_MEMORY.get("symbols")
    cached_ts = int(FUTURES_SYMBOL_MEMORY.get("ts", 0))
    if isinstance(cached_symbols, set) and cached_symbols and now - cached_ts < 3600:
        return cached_symbols
    try:
        info = client.public_get("/fapi/v1/exchangeInfo")
    except Exception as error:
        print(f"[SYMBOL FILTER ERROR] {error}")
        return set()
    symbols: set[str] = set()
    for item in info.get("symbols", []) if isinstance(info, dict) else []:
        symbol = str(item.get("symbol", "")).upper()
        status = str(item.get("status", "")).upper()
        contract_type = str(item.get("contractType", "")).upper()
        quote_asset = str(item.get("quoteAsset", "")).upper()
        if symbol and status == "TRADING" and quote_asset == "USDT" and contract_type in {"PERPETUAL", ""}:
            symbols.add(symbol)
    FUTURES_SYMBOL_MEMORY["ts"] = now
    FUTURES_SYMBOL_MEMORY["symbols"] = symbols
    return symbols


def write_env_value(key: str, value: str) -> None:
    env_file = ROOT / ".env"
    line = f"{key}={value}"
    if not env_file.exists():
        env_file.write_text(line + "\n", encoding="utf-8")
        os.environ[key] = value
        return

    lines = env_file.read_text(encoding="utf-8").splitlines()
    updated = False
    result: list[str] = []
    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped and not stripped.startswith("#") and stripped.split("=", 1)[0].strip() == key:
            result.append(line)
            updated = True
        else:
            result.append(raw_line)
    if not updated:
        result.append(line)
    env_file.write_text("\n".join(result) + "\n", encoding="utf-8")
    os.environ[key] = value


def trading_control_status() -> dict:
    return {
        "auto_trade_enabled": env_bool("AUTO_TRADE_ENABLED", "false"),
        "auto_scalping": auto_scalping_enabled(),
        "dry_run": env_bool("DRY_RUN", "true"),
    }


def load_trade_memory() -> None:
    global TRADE_MEMORY
    if not TRADE_STATE_FILE.exists():
        return
    try:
        payload = json.loads(TRADE_STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            TRADE_MEMORY = payload
    except Exception as error:
        print(f"[TRADE MEMORY LOAD ERROR] {error}")


def save_trade_memory() -> None:
    try:
        DATA_DIR.mkdir(exist_ok=True)
        TRADE_STATE_FILE.write_text(json.dumps(TRADE_MEMORY, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as error:
        print(f"[TRADE MEMORY SAVE ERROR] {error}")


def can_auto_enter(symbol: str) -> tuple[bool, str]:
    clean_symbol = normalize_symbol(symbol)
    now = int(time.time())
    today = time.strftime("%Y-%m-%d")
    memory = TRADE_MEMORY.setdefault(clean_symbol, {"last_entry": 0, "date": today, "count": 0})
    if memory.get("date") != today:
        memory["date"] = today
        memory["count"] = 0
        memory["last_entry"] = 0
    cooldown_seconds = int(os.getenv("ENTRY_COOLDOWN_SECONDS", "900"))
    if now - int(memory.get("last_entry", 0)) < cooldown_seconds:
        left = cooldown_seconds - (now - int(memory.get("last_entry", 0)))
        return False, f"cooldown {left}s"
    max_daily_trades = int(os.getenv("MAX_DAILY_TRADES", "3"))
    if max_daily_trades > 0 and int(memory.get("count", 0)) >= max_daily_trades:
        return False, f"MAX_DAILY_TRADES={max_daily_trades} tercapai"
    return True, "ok"


def record_auto_entry(symbol: str) -> None:
    clean_symbol = normalize_symbol(symbol)
    today = time.strftime("%Y-%m-%d")
    memory = TRADE_MEMORY.setdefault(clean_symbol, {"last_entry": 0, "date": today, "count": 0})
    if memory.get("date") != today:
        memory["date"] = today
        memory["count"] = 0
        memory["last_entry"] = 0
    memory["last_entry"] = int(time.time())
    memory["count"] = int(memory.get("count", 0)) + 1
    save_trade_memory()


def record_trade_pnl(symbol: str, pnl_usdt: Decimal) -> dict:
    clean_symbol = normalize_symbol(symbol)
    today = time.strftime("%Y-%m-%d")
    memory = TRADE_MEMORY.setdefault(clean_symbol, {"last_entry": 0, "date": today, "count": 0})
    if memory.get("pnl_date") != today:
        memory["pnl_date"] = today
        memory["today_pnl_usdt"] = "0"
        memory["today_wins"] = 0
        memory["today_losses"] = 0
    memory["total_pnl_usdt"] = str(Decimal(str(memory.get("total_pnl_usdt", "0"))) + pnl_usdt)
    memory["today_pnl_usdt"] = str(Decimal(str(memory.get("today_pnl_usdt", "0"))) + pnl_usdt)
    memory["total_trades"] = int(memory.get("total_trades", 0)) + 1
    if pnl_usdt >= 0:
        memory["total_wins"] = int(memory.get("total_wins", 0)) + 1
        memory["today_wins"] = int(memory.get("today_wins", 0)) + 1
    else:
        memory["total_losses"] = int(memory.get("total_losses", 0)) + 1
        memory["today_losses"] = int(memory.get("today_losses", 0)) + 1
    save_trade_memory()
    return memory


def trade_pnl_summary(symbol: str, idr_rate: Decimal | None = None) -> str:
    clean_symbol = normalize_symbol(symbol)
    today = time.strftime("%Y-%m-%d")
    memory = TRADE_MEMORY.setdefault(clean_symbol, {"last_entry": 0, "date": today, "count": 0})
    total_usdt = Decimal(str(memory.get("total_pnl_usdt", "0")))
    today_usdt = Decimal(str(memory.get("today_pnl_usdt", "0"))) if memory.get("pnl_date") == today else Decimal("0")
    rate = idr_rate if idr_rate is not None else Decimal("0")
    total_idr = total_usdt * rate
    today_idr = today_usdt * rate
    return (
        f"P&L Hari ini: {today_usdt:+.4f} USDT / Rp {today_idr:+,.0f}\n"
        f"P&L Total bot: {total_usdt:+.4f} USDT / Rp {total_idr:+,.0f}\n"
        f"Win/Loss hari ini: {memory.get('today_wins', 0)}/{memory.get('today_losses', 0)}"
    )


def score_trigger_ok(signal_info: dict) -> tuple[bool, str]:
    if not env_bool("REQUIRE_SCORE_TRIGGER", "true"):
        return True, "score trigger tidak diwajibkan"
    side = signal_info.get("signal")
    if side not in {"BUY", "SELL"}:
        return False, "NO SIGNAL"
    if side == "SELL" and not env_bool("ALLOW_SHORT", "true"):
        return False, "ALLOW_SHORT=false"
    threshold = int(os.getenv("SIGNAL_SCORE_THRESHOLD", "6"))
    min_edge = int(os.getenv("SIGNAL_SCORE_MIN_EDGE", "2"))
    buy_score = int(signal_info.get("score_buy", 0))
    sell_score = int(signal_info.get("score_sell", 0))
    if side == "BUY":
        if buy_score < threshold:
            return False, f"BUY score {buy_score} < {threshold}"
        if buy_score - sell_score < min_edge:
            return False, f"BUY edge {buy_score - sell_score} < {min_edge}"
    if side == "SELL":
        if sell_score < threshold:
            return False, f"SELL score {sell_score} < {threshold}"
        if sell_score - buy_score < min_edge:
            return False, f"SELL edge {sell_score - buy_score} < {min_edge}"
    return True, f"{side} score trigger valid"


def telegram_chat_id(token: str) -> str:
    configured = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if configured:
        return configured
    try:
        delete_request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/deleteWebhook?drop_pending_updates=false",
            method="POST",
        )
        with urllib.request.urlopen(delete_request, timeout=10) as response:
            response.read()
    except Exception as error:
        print(f"[TELEGRAM DELETE WEBHOOK ERROR] {error}")
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        for item in reversed(payload.get("result", [])):
            message = item.get("message") or item.get("channel_post") or {}
            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id:
                return str(chat_id)
    except Exception as error:
        print(f"[TELEGRAM CHAT ID ERROR] {error}")
    return ""


def send_telegram(message: str) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not env_bool("TELEGRAM_ENABLED", "false"):
        return
    chat_id = telegram_chat_id(token)
    if not chat_id:
        print("[TELEGRAM SKIP] TELEGRAM_CHAT_ID kosong. Kirim /start ke bot lalu restart.")
        return
    data = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except Exception as error:
        print(f"[TELEGRAM ERROR] {error}")


def telegram_signal_message(signal_info: dict) -> str:
    side = signal_info.get("signal") or "NO SIGNAL"
    score = signal_info.get("score_buy") if side == "BUY" else signal_info.get("score_sell")
    return (
        f"<b>AI Signal {side}</b>\n"
        f"Pair: <b>{signal_info.get('symbol')}</b>\n"
        f"Price: <b>{signal_info.get('close')}</b>\n"
        f"RSI: {signal_info.get('rsi')} | Score: {score}/{os.getenv('SIGNAL_SCORE_THRESHOLD', '6')}\n"
        f"Reason: {signal_info.get('reason')}"
    )


def notify_signal_once(signal_info: dict) -> None:
    if not env_bool("TELEGRAM_NOTIFY_SIGNALS", "true"):
        return
    side = signal_info.get("signal")
    symbol = str(signal_info.get("symbol", ""))
    if side not in {"BUY", "SELL"} or not symbol:
        return
    key = f"{symbol}:{side}"
    now = int(time.time())
    cooldown = int(os.getenv("TELEGRAM_SIGNAL_COOLDOWN_SECONDS", "300"))
    if now - SIGNAL_NOTIFY_MEMORY.get(key, 0) < cooldown:
        return
    SIGNAL_NOTIFY_MEMORY[key] = now
    send_telegram(telegram_signal_message(signal_info))


def normalize_symbol(symbol: str | None, fallback: str = "XAUUSDT") -> str:
    clean_symbol = (symbol or fallback).strip().upper().replace(".P", "")
    if ":" in clean_symbol:
        clean_symbol = clean_symbol.split(":")[-1]
    aliases = {
        "GOLD": "XAUUSDT",
        "XAU": "XAUUSDT",
        "XAUUSD": "XAUUSDT",
    }
    return aliases.get(clean_symbol, clean_symbol)


def symbol_pip_size(symbol: str) -> Decimal:
    clean_symbol = normalize_symbol(symbol)
    env_key = f"{clean_symbol}_PIP_SIZE"
    return Decimal(os.getenv(env_key, os.getenv("GOLD_PIP_SIZE", "0.01")))


def effective_order_usdt(symbol: str, amount: Decimal) -> Decimal:
    clean_symbol = normalize_symbol(symbol)
    if clean_symbol in {"XAUUSDT", "XAUUSD"}:
        minimum = Decimal(os.getenv("GOLD_MIN_ORDER_USDT", "5"))
        amount = max(amount, minimum)
    configured_balance = os.getenv("ACCOUNT_BALANCE_USDT", "").strip()
    if not configured_balance:
        balance_idr = os.getenv("ACCOUNT_BALANCE_IDR", "").strip()
        usdt_idr_rate = os.getenv("USDT_IDR_RATE", "").strip()
        if balance_idr and usdt_idr_rate:
            configured_balance = str(Decimal(balance_idr) / Decimal(usdt_idr_rate))
    max_order_percent = Decimal(os.getenv("MAX_ORDER_BALANCE_PERCENT", "0"))
    if configured_balance and max_order_percent > 0:
        max_amount = Decimal(configured_balance) * max_order_percent / Decimal("100")
        amount = min(amount, max_amount)
    return amount.quantize(Decimal("0.01"))


class BinanceClient:
    def __init__(self, symbol: str | None = None) -> None:
        self.mode = os.getenv("BINANCE_MODE", "testnet").strip().lower()
        self.symbol = normalize_symbol(symbol, os.getenv("TRADE_SYMBOL", "XAUUSDT"))
        self.api_key = os.getenv("BINANCE_API_KEY", "").strip()
        self.api_secret = os.getenv("BINANCE_API_SECRET", "").strip()
        self.key_type = os.getenv("BINANCE_KEY_TYPE", "hmac").strip().lower()
        self.private_key_path = self.resolve_private_key_path()
        self.base_url = self.resolve_base_url()

    def resolve_private_key_path(self) -> Path:
        configured = os.getenv("BINANCE_PRIVATE_KEY_PATH", "private_key.pem").strip()
        path = Path(configured)
        if not path.is_absolute():
            path = ROOT / path
        return path

    def resolve_base_url(self) -> str:
        custom_url = os.getenv("BINANCE_BASE_URL", "").strip().rstrip("/")
        if custom_url:
            return custom_url
        if os.getenv("BINANCE_MARKET_TYPE", "futures").strip().lower() == "futures":
            return "https://fapi.binance.com" if self.mode == "live" else "https://testnet.binancefuture.com"
        return "https://api.binance.com" if self.mode == "live" else "https://testnet.binance.vision"

    def is_futures(self) -> bool:
        return os.getenv("BINANCE_MARKET_TYPE", "futures").strip().lower() == "futures"

    def auto_trade_enabled(self) -> bool:
        return env_bool("AUTO_TRADE_ENABLED", "false")

    def dry_run(self) -> bool:
        return not os.getenv("DRY_RUN", "true").strip().lower() in {"0", "false", "no", "off"}

    def has_signed_credentials(self) -> bool:
        if self.key_type == "rsa":
            return bool(self.api_key and self.private_key_path.exists())
        return bool(self.api_key and self.api_secret)

    def fetch_json(self, request: urllib.request.Request) -> dict | list:
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Binance API error {error.code}: {detail}") from error

    def public_get(self, path: str, params: dict[str, str] | None = None) -> dict | list:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        request = urllib.request.Request(url, method="GET")
        return self.fetch_json(request)

    def public_get_url(self, base_url: str, path: str, params: dict[str, str] | None = None) -> dict | list:
        query = urllib.parse.urlencode(params or {})
        url = f"{base_url.rstrip('/')}{path}"
        if query:
            url = f"{url}?{query}"
        request = urllib.request.Request(url, method="GET")
        return self.fetch_json(request)

    def signed_get(self, path: str, params: dict[str, str] | None = None) -> dict:
        if not self.api_key:
            raise ValueError("BINANCE_API_KEY belum diisi.")
        signed_query = self.signed_query(params or {})
        request = urllib.request.Request(
            f"{self.base_url}{path}?{signed_query}",
            headers={"X-MBX-APIKEY": self.api_key},
            method="GET",
        )
        return self.fetch_json(request)

    def signed_post(self, path: str, params: dict[str, str] | None = None) -> dict:
        if not self.api_key:
            raise ValueError("BINANCE_API_KEY belum diisi.")
        signed_query = self.signed_query(params or {})
        request = urllib.request.Request(
            f"{self.base_url}{path}?{signed_query}",
            headers={"X-MBX-APIKEY": self.api_key},
            method="POST",
        )
        return self.fetch_json(request)

    def signed_delete(self, path: str, params: dict[str, str] | None = None) -> dict:
        if not self.api_key:
            raise ValueError("BINANCE_API_KEY belum diisi.")
        signed_query = self.signed_query(params or {})
        request = urllib.request.Request(
            f"{self.base_url}{path}?{signed_query}",
            headers={"X-MBX-APIKEY": self.api_key},
            method="DELETE",
        )
        return self.fetch_json(request)

    def signed_query(self, params: dict[str, str]) -> str:
        payload = {
            **params,
            "timestamp": str(int(time.time() * 1000)),
            "recvWindow": os.getenv("BINANCE_RECV_WINDOW", "5000"),
        }
        query = urllib.parse.urlencode(payload)
        signature = self.sign_query(query)
        return urllib.parse.urlencode({**payload, "signature": signature})

    def sign_query(self, query: str) -> str:
        if self.key_type == "rsa":
            return self.rsa_signature(query)
        if not self.api_secret:
            raise ValueError("BINANCE_API_SECRET belum diisi untuk key type HMAC.")
        return hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()

    def rsa_signature(self, query: str) -> str:
        if not self.private_key_path.exists():
            raise ValueError(f"Private key RSA tidak ditemukan: {self.private_key_path}")
        result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(self.private_key_path)],
            input=query.encode("utf-8"),
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Gagal membuat RSA signature dengan OpenSSL: {detail}")
        return base64.b64encode(result.stdout).decode("ascii")

    def market_symbols(self) -> dict:
        path = "/fapi/v1/exchangeInfo" if self.is_futures() else "/api/v3/exchangeInfo"
        info = self.public_get(path)
        preferred_quotes = {"USDT", "USDC", "FDUSD", "BTC", "ETH", "BNB"}
        symbols = [
            {
                "symbol": item.get("symbol", ""),
                "base_asset": item.get("baseAsset", ""),
                "quote_asset": item.get("quoteAsset", ""),
                "status": item.get("status", ""),
            }
            for item in info.get("symbols", [])
            if item.get("status") == "TRADING" and item.get("quoteAsset") in preferred_quotes
        ]
        symbols.sort(key=lambda item: (item["quote_asset"] != "USDT", item["symbol"]))
        return {"mode": self.mode, "count": len(symbols), "symbols": symbols}

    def klines_rest(self, interval: str = "1m", limit: int = 120) -> dict:
        allowed_intervals = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d"}
        if interval not in allowed_intervals:
            raise ValueError("Interval candle tidak didukung.")
        safe_limit = max(10, min(limit, 500))
        rows = self.public_get(
            "/fapi/v1/klines" if self.is_futures() else "/api/v3/klines",
            {"symbol": self.symbol, "interval": interval, "limit": str(safe_limit)},
        )
        candles = [
            {
                "open_time": item[0],
                "open": item[1],
                "high": item[2],
                "low": item[3],
                "close": item[4],
                "volume": item[5],
                "close_time": item[6],
            }
            for item in rows
        ]
        return {"symbol": self.symbol, "interval": interval, "candles": candles}

    def klines(self, interval: str = "1m", limit: int = 120) -> dict:
        if BINANCE_WS_MANAGER and BINANCE_WS_MANAGER.running:
            cached = BINANCE_WS_MANAGER.get_cached_klines(self.symbol, interval, limit)
            if cached is not None:
                return cached
        return self.klines_rest(interval, limit)

    def order_book_rest(self, limit: int = 20) -> dict:
        safe_limit = max(5, min(limit, 100))
        rows = self.public_get(
            "/fapi/v1/depth" if self.is_futures() else "/api/v3/depth",
            {"symbol": self.symbol, "limit": str(safe_limit)},
        )
        bids = rows.get("bids", []) if isinstance(rows, dict) else []
        asks = rows.get("asks", []) if isinstance(rows, dict) else []
        bid_notional = sum(Decimal(str(price)) * Decimal(str(qty)) for price, qty in bids)
        ask_notional = sum(Decimal(str(price)) * Decimal(str(qty)) for price, qty in asks)
        total = bid_notional + ask_notional
        bid_ratio = (bid_notional / total) if total > 0 else Decimal("0.5")
        ask_ratio = (ask_notional / total) if total > 0 else Decimal("0.5")
        best_bid = Decimal(str(bids[0][0])) if bids else Decimal("0")
        best_ask = Decimal(str(asks[0][0])) if asks else Decimal("0")
        spread_percent = Decimal("0")
        if best_bid > 0 and best_ask > 0:
            spread_percent = ((best_ask - best_bid) / best_bid) * Decimal("100")
        return {
            "symbol": self.symbol,
            "limit": safe_limit,
            "best_bid": str(best_bid),
            "best_ask": str(best_ask),
            "bid_notional": str(bid_notional),
            "ask_notional": str(ask_notional),
            "bid_ratio": str(bid_ratio),
            "ask_ratio": str(ask_ratio),
            "spread_percent": str(spread_percent),
        }

    def order_book(self, limit: int = 20) -> dict:
        if BINANCE_WS_MANAGER and BINANCE_WS_MANAGER.running:
            cached = BINANCE_WS_MANAGER.get_cached_order_book(self.symbol)
            if cached is not None:
                return cached
        return self.order_book_rest(limit)

    def get_price_rest(self) -> Decimal:
        path = "/fapi/v1/ticker/price" if self.is_futures() else "/api/v3/ticker/price"
        payload = self.public_get(path, {"symbol": self.symbol})
        return Decimal(str(payload["price"]))

    def get_price(self) -> Decimal:
        if BINANCE_WS_MANAGER and BINANCE_WS_MANAGER.running:
            cached = BINANCE_WS_MANAGER.get_cached_price(self.symbol)
            if cached is not None:
                return cached
        return self.get_price_rest()

    def asset_usdt_price(self, asset: str) -> Decimal:
        clean_asset = asset.strip().upper()
        if clean_asset in {"USDT", "FDUSD", "USDC"}:
            return Decimal("1")
        symbol = f"{clean_asset}USDT"
        try:
            payload = self.public_get("/fapi/v1/ticker/price" if self.is_futures() else "/api/v3/ticker/price", {"symbol": symbol})
        except Exception:
            try:
                payload = self.public_get_url("https://api.binance.com", "/api/v3/ticker/price", {"symbol": symbol})
            except Exception as error:
                print(f"[ASSET PRICE SKIP] {clean_asset}: {error}")
                return Decimal("0")
        return Decimal(str(payload["price"]))

    def usdt_idr_rate(self) -> Decimal | None:
        configured = os.getenv("USDT_IDR_RATE", "").strip()
        if configured:
            return Decimal(configured)
        try:
            payload = self.public_get_url("https://api.binance.com", "/api/v3/ticker/price", {"symbol": "USDTIDR"})
            return Decimal(str(payload["price"]))
        except Exception:
            return None

    def futures_balance_summary(self, balances: list[dict]) -> dict:
        wallet_usdt = Decimal("0")
        available_usdt = Decimal("0")
        margin_usdt = Decimal("0")
        unrealized_usdt = Decimal("0")
        idr_rate = self.usdt_idr_rate()
        for balance in balances:
            asset = str(balance.get("asset", "")).upper()
            price = self.asset_usdt_price(asset)
            if price <= 0 and asset not in {"USDT", "FDUSD", "USDC"}:
                balance["priceError"] = f"{asset}USDT tidak tersedia"
                balance["usdtPrice"] = "0"
                balance["walletUsdt"] = "0"
                balance["availableUsdt"] = "0"
                balance["marginUsdt"] = "0"
                balance["unrealizedUsdt"] = "0"
                continue
            balance_wallet_usdt = Decimal(str(balance.get("walletBalance", "0"))) * price
            balance_available_usdt = Decimal(str(balance.get("availableBalance", "0"))) * price
            balance_margin_usdt = Decimal(str(balance.get("marginBalance", "0"))) * price
            balance_unrealized_usdt = Decimal(str(balance.get("unrealizedProfit", "0"))) * price
            balance["usdtPrice"] = str(price)
            balance["walletUsdt"] = str(balance_wallet_usdt)
            balance["availableUsdt"] = str(balance_available_usdt)
            balance["marginUsdt"] = str(balance_margin_usdt)
            balance["unrealizedUsdt"] = str(balance_unrealized_usdt)
            if idr_rate:
                balance["walletIdr"] = str(balance_wallet_usdt * idr_rate)
                balance["availableIdr"] = str(balance_available_usdt * idr_rate)
                balance["marginIdr"] = str(balance_margin_usdt * idr_rate)
                balance["unrealizedIdr"] = str(balance_unrealized_usdt * idr_rate)
            wallet_usdt += balance_wallet_usdt
            available_usdt += balance_available_usdt
            margin_usdt += balance_margin_usdt
            unrealized_usdt += balance_unrealized_usdt
        summary = {
            "wallet_usdt": str(wallet_usdt),
            "available_usdt": str(available_usdt),
            "margin_usdt": str(margin_usdt),
            "unrealized_usdt": str(unrealized_usdt),
            "usdt_idr_rate": str(idr_rate) if idr_rate else None,
        }
        if idr_rate:
            summary.update(
                {
                    "wallet_idr": str(wallet_usdt * idr_rate),
                    "available_idr": str(available_usdt * idr_rate),
                    "margin_idr": str(margin_usdt * idr_rate),
                    "unrealized_idr": str(unrealized_usdt * idr_rate),
                }
            )
        return summary

    def futures_account_summary(self, account: dict, balances: list[dict]) -> dict:
        summary = self.futures_balance_summary(balances)
        idr_rate = self.usdt_idr_rate()
        wallet_usdt = Decimal(str(account.get("totalWalletBalance", summary.get("wallet_usdt", "0"))))
        available_usdt = Decimal(str(account.get("availableBalance", summary.get("available_usdt", "0"))))
        margin_usdt = Decimal(str(account.get("totalMarginBalance", summary.get("margin_usdt", "0"))))
        unrealized_usdt = Decimal(str(account.get("totalUnrealizedProfit", summary.get("unrealized_usdt", "0"))))
        summary.update(
            {
                "wallet_usdt": str(wallet_usdt),
                "available_usdt": str(available_usdt),
                "margin_usdt": str(margin_usdt),
                "unrealized_usdt": str(unrealized_usdt),
                "usdt_idr_rate": str(idr_rate) if idr_rate else summary.get("usdt_idr_rate"),
            }
        )
        if idr_rate:
            summary.update(
                {
                    "wallet_idr": str(wallet_usdt * idr_rate),
                    "available_idr": str(available_usdt * idr_rate),
                    "margin_idr": str(margin_usdt * idr_rate),
                    "unrealized_idr": str(unrealized_usdt * idr_rate),
                }
            )
        return summary

    def exchange_symbol_info(self) -> dict:
        path = "/fapi/v1/exchangeInfo" if self.is_futures() else "/api/v3/exchangeInfo"
        info = self.public_get(path)
        for item in info.get("symbols", []):
            if item.get("symbol") == self.symbol:
                return item
        raise ValueError(f"Symbol tidak ditemukan di Binance: {self.symbol}")

    def quantity_step(self) -> Decimal:
        symbol_info = self.exchange_symbol_info()
        for item in symbol_info.get("filters", []):
            if item.get("filterType") == "LOT_SIZE":
                return Decimal(str(item.get("stepSize", "0.001")))
        return Decimal("0.001")

    def price_tick(self) -> Decimal:
        symbol_info = self.exchange_symbol_info()
        for item in symbol_info.get("filters", []):
            if item.get("filterType") == "PRICE_FILTER":
                return Decimal(str(item.get("tickSize", "0.0001")))
        return Decimal("0.0001")

    def min_notional(self) -> Decimal:
        symbol_info = self.exchange_symbol_info()
        for item in symbol_info.get("filters", []):
            if item.get("filterType") in {"MIN_NOTIONAL", "NOTIONAL"}:
                return Decimal(str(item.get("notional", item.get("minNotional", "0"))))
        return Decimal("0")

    def round_quantity(self, quantity: Decimal) -> str:
        step = self.quantity_step()
        rounded = (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step
        return format(rounded.normalize(), "f")

    def round_price(self, price: Decimal) -> str:
        tick = self.price_tick()
        rounded = (price / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        return format(rounded.normalize(), "f")

    def tp_sl_prices(self, entry_side: str, entry_price: Decimal) -> tuple[Decimal, Decimal]:
        if env_bool("USE_PIP_TP_SL", "true"):
            pip_size = symbol_pip_size(self.symbol)
            tp_pips = Decimal(os.getenv("TAKE_PROFIT_PIPS", "50"))
            sl_pips = Decimal(os.getenv("STOP_LOSS_PIPS", "35"))
            if entry_side == "BUY":
                return entry_price + (tp_pips * pip_size), entry_price - (sl_pips * pip_size)
            return entry_price - (tp_pips * pip_size), entry_price + (sl_pips * pip_size)

        tp_percent = Decimal(os.getenv("TP_PERCENT", os.getenv("TAKE_PROFIT_PERCENT", "1")))
        sl_percent = Decimal(os.getenv("SL_PERCENT", os.getenv("STOP_LOSS_PERCENT", "0.5")))
        if entry_side == "BUY":
            return (
                entry_price * (Decimal("1") + tp_percent / Decimal("100")),
                entry_price * (Decimal("1") - sl_percent / Decimal("100")),
            )
        return (
            entry_price * (Decimal("1") - tp_percent / Decimal("100")),
            entry_price * (Decimal("1") + sl_percent / Decimal("100")),
        )

    def calculate_quantity(self, usdt_amount: Decimal | None = None) -> str:
        amount = usdt_amount or Decimal(os.getenv("ORDER_USDT", "5"))
        amount = effective_order_usdt(self.symbol, amount)
        leverage = Decimal(os.getenv("DEFAULT_LEVERAGE", "3"))
        notional = amount * leverage
        min_notional = self.min_notional()
        if min_notional > 0 and notional < min_notional:
            minimum_margin = (min_notional / leverage).quantize(Decimal("0.01"))
            raise ValueError(
                f"Notional {notional} USDT terlalu kecil untuk {self.symbol}. "
                f"Minimum {min_notional} USDT. Naikkan ORDER_USDT minimal sekitar {minimum_margin}."
            )
        price = self.get_price()
        quantity = notional / price
        rounded = self.round_quantity(quantity)
        if Decimal(rounded) <= 0:
            raise ValueError("Quantity menjadi 0. Naikkan ORDER_USDT atau pilih symbol lain.")
        rounded_notional = Decimal(rounded) * price
        if min_notional > 0 and rounded_notional < min_notional:
            step = self.quantity_step()
            minimum_quantity = ((min_notional / price) / step).to_integral_value(rounding=ROUND_DOWN) * step + step
            minimum_margin = ((minimum_quantity * price) / leverage).quantize(Decimal("0.01"))
            raise ValueError(
                f"Notional setelah pembulatan {rounded_notional} USDT terlalu kecil untuk {self.symbol}. "
                f"Naikkan ORDER_USDT minimal sekitar {minimum_margin}."
            )
        return rounded

    def status_rest(self) -> dict:
        result = {
            "mode": self.mode,
            "market_type": "futures" if self.is_futures() else "spot",
            "symbol": self.symbol,
            "base_url": self.base_url,
            "key_type": self.key_type,
            "has_keys": self.has_signed_credentials(),
            "private_key_configured": self.key_type == "rsa" and self.private_key_path.exists(),
            "auto_trade_enabled": self.auto_trade_enabled(),
            "dry_run": self.dry_run(),
            "auto_scalping": auto_scalping_enabled(),
        }
        result["server_time"] = self.public_get("/fapi/v1/time" if self.is_futures() else "/api/v3/time")
        result["price"] = self.public_get(
            "/fapi/v1/ticker/price" if self.is_futures() else "/api/v3/ticker/price",
            {"symbol": self.symbol},
        )
        if self.has_signed_credentials():
            try:
                account = self.signed_get("/fapi/v2/account" if self.is_futures() else "/api/v3/account")
                if self.is_futures():
                    balances = [
                        asset
                        for asset in account.get("assets", [])
                        if (
                            abs(float(asset.get("walletBalance", "0"))) > 0
                            or abs(float(asset.get("marginBalance", "0"))) > 0
                            or abs(float(asset.get("unrealizedProfit", "0"))) > 0
                        )
                    ]
                    result["balances"] = balances
                    result["balance_summary"] = self.futures_account_summary(account, balances)
                    result["open_positions"] = self.open_positions()
                else:
                    result["balances"] = [
                        bal
                        for bal in account.get("balances", [])
                        if float(bal.get("free", "0")) > 0 or float(bal.get("locked", "0")) > 0
                    ]
            except Exception as error:
                result["account_error"] = str(error)
                result["balances"] = []
                result["open_positions"] = []
        return result

    def status(self) -> dict:
        if BINANCE_WS_MANAGER and BINANCE_WS_MANAGER.running:
            cached = BINANCE_WS_MANAGER.get_cached_status()
            if cached is not None:
                res = dict(cached)
                res["symbol"] = self.symbol
                res["price"] = {"symbol": self.symbol, "price": str(self.get_price())}
                res["open_positions"] = self.open_positions()
                res["server_time"] = {"serverTime": int(time.time() * 1000)}
                return res
        return self.status_rest()

    def open_positions_rest(self) -> list[dict]:
        rows = self.signed_get("/fapi/v2/positionRisk")
        positions = [pos for pos in rows if abs(float(pos.get("positionAmt", "0"))) > 0]
        for position in positions:
            entry = Decimal(str(position.get("entryPrice", "0")))
            mark = Decimal(str(position.get("markPrice", "0")))
            amount = Decimal(str(position.get("positionAmt", "0")))
            unrealized_profit = Decimal(str(position.get("unRealizedProfit", position.get("unrealizedProfit", "0"))))
            leverage = Decimal(str(position.get("leverage", os.getenv("DEFAULT_LEVERAGE", "1"))))
            notional = abs(Decimal(str(position.get("notional", "0"))))
            tp_percent = Decimal(os.getenv("TP_PERCENT", os.getenv("TAKE_PROFIT_PERCENT", "1")))
            sl_percent = Decimal(os.getenv("SL_PERCENT", os.getenv("STOP_LOSS_PERCENT", "0.5")))
            if entry > 0 and mark > 0:
                if amount > 0:
                    pnl_percent = ((mark - entry) / entry) * Decimal("100")
                    tp_price = entry * (Decimal("1") + tp_percent / Decimal("100"))
                    sl_price = entry * (Decimal("1") - sl_percent / Decimal("100"))
                    position["tpHitDirection"] = "mark >= TP" if mark >= tp_price else "mark < TP"
                else:
                    pnl_percent = ((entry - mark) / entry) * Decimal("100")
                    tp_price = entry * (Decimal("1") - tp_percent / Decimal("100"))
                    sl_price = entry * (Decimal("1") + sl_percent / Decimal("100"))
                    position["tpHitDirection"] = "mark <= TP" if mark <= tp_price else "mark > TP"
                position["pnlPercent"] = str(pnl_percent)
                position["takeProfitPrice"] = self.round_price(tp_price)
                position["stopLossPrice"] = self.round_price(sl_price)
            if notional > 0 and leverage > 0:
                initial_margin = notional / leverage
                position["roePercent"] = str((unrealized_profit / initial_margin) * Decimal("100"))
        return positions

    def open_positions(self) -> list[dict]:
        if BINANCE_WS_MANAGER and BINANCE_WS_MANAGER.running:
            cached = BINANCE_WS_MANAGER.get_cached_open_positions()
            if cached is not None:
                positions_copy = []
                for p in cached:
                    pos = dict(p)
                    latest_price = BINANCE_WS_MANAGER.get_cached_price(pos.get("symbol", ""))
                    if latest_price is not None:
                        pos["markPrice"] = str(latest_price)
                    entry = Decimal(str(pos.get("entryPrice", "0")))
                    mark = Decimal(str(pos.get("markPrice", "0")))
                    amount = Decimal(str(pos.get("positionAmt", "0")))
                    if entry > 0 and mark > 0:
                        if amount > 0:
                            pnl_percent = ((mark - entry) / entry) * Decimal("100")
                        else:
                            pnl_percent = ((entry - mark) / entry) * Decimal("100")
                        pos["pnlPercent"] = str(pnl_percent)
                        leverage = Decimal(str(pos.get("leverage", os.getenv("DEFAULT_LEVERAGE", "1"))))
                        unrealized_profit = Decimal(str(pos.get("unRealizedProfit", pos.get("unrealizedProfit", "0"))))
                        notional = abs(Decimal(str(pos.get("notional", "0"))))
                        if notional > 0 and leverage > 0:
                            initial_margin = notional / leverage
                            pos["roePercent"] = str((unrealized_profit / initial_margin) * Decimal("100"))
                    positions_copy.append(pos)
                return positions_copy
        return self.open_positions_rest()

    def has_open_position(self) -> bool:
        for position in self.open_positions():
            if position.get("symbol") == self.symbol and abs(float(position.get("positionAmt", "0"))) > 0:
                return True
        return False

    def has_reached_max_open_positions(self) -> bool:
        max_positions = int(os.getenv("MAX_OPEN_POSITIONS", "1"))
        if max_positions <= 0:
            return False
        return len(self.open_positions()) >= max_positions

    def open_orders_rest(self, symbol: str | None = None) -> list[dict]:
        if self.dry_run():
            return []
        target_symbol = normalize_symbol(symbol or self.symbol)
        rows = self.signed_get("/fapi/v1/openOrders", {"symbol": target_symbol})
        if isinstance(rows, list):
            return rows
        return []

    def open_orders(self, symbol: str | None = None) -> list[dict]:
        if BINANCE_WS_MANAGER and BINANCE_WS_MANAGER.running:
            target = symbol or self.symbol
            cached = BINANCE_WS_MANAGER.get_cached_open_orders(target)
            if cached is not None:
                return cached
        return self.open_orders_rest(symbol)

    def cancel_order(self, symbol: str, order_id: str | int) -> dict:
        if self.dry_run():
            return {"dry_run": True, "symbol": normalize_symbol(symbol), "orderId": str(order_id)}
        return self.signed_delete(
            "/fapi/v1/order",
            {"symbol": normalize_symbol(symbol), "orderId": str(order_id)},
        )

    def set_leverage(self, leverage: int | None = None) -> dict:
        if not self.is_futures():
            return {"skipped": True, "reason": "Leverage hanya untuk futures."}
        safe_leverage = int(leverage or int(os.getenv("DEFAULT_LEVERAGE", "3")))
        safe_leverage = max(1, min(safe_leverage, 20))
        if self.dry_run():
            return {"dry_run": True, "symbol": self.symbol, "leverage": safe_leverage}
        return self.signed_post("/fapi/v1/leverage", {"symbol": self.symbol, "leverage": str(safe_leverage)})

    def is_hedge_mode(self) -> bool:
        if not self.is_futures() or self.dry_run():
            return env_bool("BINANCE_HEDGE_MODE", "false")
        payload = self.signed_get("/fapi/v1/positionSide/dual")
        return bool(payload.get("dualSidePosition"))

    def order_position_side(self, side: str, position_side: str | None = None) -> str | None:
        if not self.is_hedge_mode():
            return None
        if position_side:
            clean_position_side = position_side.strip().upper()
            if clean_position_side in {"LONG", "SHORT"}:
                return clean_position_side
        return "LONG" if side.strip().upper() == "BUY" else "SHORT"

    def place_futures_market_order(
        self,
        side: str,
        quantity: str | None = None,
        reduce_only: bool = False,
        position_side: str | None = None,
    ) -> dict:
        if not self.is_futures():
            raise ValueError("Auto order saat ini hanya dibuat untuk Binance Futures USDT-M.")
        clean_side = side.strip().upper()
        if clean_side not in {"BUY", "SELL"}:
            raise ValueError("side harus BUY atau SELL.")
        qty = quantity or self.calculate_quantity()
        params = {"symbol": self.symbol, "side": clean_side, "type": "MARKET", "quantity": qty}
        resolved_position_side = self.order_position_side(clean_side, position_side)
        if resolved_position_side:
            params["positionSide"] = resolved_position_side
        if reduce_only and not resolved_position_side:
            params["reduceOnly"] = "true"
        if self.dry_run():
            return {"dry_run": True, "endpoint": "/fapi/v1/order", "params": params}
        return self.signed_post("/fapi/v1/order", params)

    def place_futures_limit_order(
        self,
        side: str,
        price: Decimal,
        quantity: str | None = None,
        reduce_only: bool = False,
        position_side: str | None = None,
        client_order_id: str | None = None,
    ) -> dict:
        if not self.is_futures():
            raise ValueError("Auto order saat ini hanya dibuat untuk Binance Futures USDT-M.")
        clean_side = side.strip().upper()
        if clean_side not in {"BUY", "SELL"}:
            raise ValueError("side harus BUY atau SELL.")
        qty = quantity or self.calculate_quantity()
        params = {
            "symbol": self.symbol,
            "side": clean_side,
            "type": "LIMIT",
            "timeInForce": os.getenv("ENTRY_LIMIT_TIME_IN_FORCE", "GTC").strip().upper(),
            "quantity": qty,
            "price": self.round_price(price),
        }
        if client_order_id:
            params["newClientOrderId"] = client_order_id[:36]
        resolved_position_side = self.order_position_side(clean_side, position_side)
        if resolved_position_side:
            params["positionSide"] = resolved_position_side
        if reduce_only and not resolved_position_side:
            params["reduceOnly"] = "true"
        if self.dry_run():
            return {"dry_run": True, "endpoint": "/fapi/v1/order", "params": params}
        return self.signed_post("/fapi/v1/order", params)

    def entry_limit_price(self, side: str, reference_price: Decimal) -> Decimal:
        offset_percent = Decimal(os.getenv("ENTRY_LIMIT_OFFSET_PERCENT", "0.03"))
        if offset_percent < 0:
            offset_percent = Decimal("0")
        if side.upper() == "BUY":
            return reference_price * (Decimal("1") - offset_percent / Decimal("100"))
        return reference_price * (Decimal("1") + offset_percent / Decimal("100"))

    def place_tp_sl_orders(self, entry_side: str, entry_price: Decimal) -> dict:
        if not env_bool("USE_TP_SL_ORDERS", "true"):
            return {
                "skipped": True,
                "reason": "USE_TP_SL_ORDERS=false. Exit dikelola auto force-close profit/SL bot.",
            }
        entry_side = entry_side.upper()
        if entry_side == "BUY":
            close_side = "SELL"
        elif entry_side == "SELL":
            close_side = "BUY"
        else:
            raise ValueError("entry_side harus BUY atau SELL.")
        tp_price, sl_price = self.tp_sl_prices(entry_side, entry_price)
        use_algo_orders = env_bool("USE_ALGO_TP_SL_ORDERS", "true")
        tp_params = {
            "symbol": self.symbol,
            "side": close_side,
            "type": "TAKE_PROFIT_MARKET",
            "closePosition": "true",
            "workingType": "MARK_PRICE",
        }
        sl_params = {
            "symbol": self.symbol,
            "side": close_side,
            "type": "STOP_MARKET",
            "closePosition": "true",
            "workingType": "MARK_PRICE",
        }
        if use_algo_orders:
            tp_params["algoType"] = "CONDITIONAL"
            tp_params["triggerPrice"] = self.round_price(tp_price)
            sl_params["algoType"] = "CONDITIONAL"
            sl_params["triggerPrice"] = self.round_price(sl_price)
        else:
            tp_params["stopPrice"] = self.round_price(tp_price)
            sl_params["stopPrice"] = self.round_price(sl_price)
        resolved_position_side = self.order_position_side(entry_side)
        if resolved_position_side:
            tp_params["positionSide"] = resolved_position_side
            sl_params["positionSide"] = resolved_position_side
        if self.dry_run():
            return {"dry_run": True, "algo": use_algo_orders, "tp_order": tp_params, "sl_order": sl_params}
        endpoint = "/fapi/v1/algoOrder" if use_algo_orders else "/fapi/v1/order"
        tp_result = self.signed_post(endpoint, tp_params)
        sl_result = self.signed_post(endpoint, sl_params)
        return {"tp_order": tp_result, "sl_order": sl_result}

    def close_position_market(self, position_side: str) -> dict:
        clean = position_side.strip().upper()
        if clean == "LONG":
            return self.place_futures_market_order("SELL", reduce_only=True, position_side="LONG")
        if clean == "SHORT":
            return self.place_futures_market_order("BUY", reduce_only=True, position_side="SHORT")
        raise ValueError("position_side harus LONG atau SHORT.")

    def close_position_amount(self, amount: Decimal) -> dict:
        """Close posisi sesuai jumlah positionAmt dari Binance positionRisk.

        amount > 0 berarti LONG, maka close dengan SELL.
        amount < 0 berarti SHORT, maka close dengan BUY.
        Ini lebih aman untuk force close profit karena memakai quantity posisi aktual,
        bukan menghitung quantity baru dari ORDER_USDT.
        """
        if amount == 0:
            return {"skipped": True, "reason": "Tidak ada position amount untuk ditutup."}

        quantity = self.round_quantity(abs(amount))
        if Decimal(quantity) <= 0:
            raise ValueError(f"Quantity close menjadi 0 untuk {self.symbol}.")

        if amount > 0:
            return self.place_futures_market_order(
                "SELL",
                quantity=quantity,
                reduce_only=True,
                position_side="LONG",
            )

        return self.place_futures_market_order(
            "BUY",
            quantity=quantity,
            reduce_only=True,
            position_side="SHORT",
        )

    def handle_webhook_signal(self, payload: dict) -> dict:
        token = os.getenv("WEBHOOK_TOKEN", "").strip()
        if token and str(payload.get("token", "")).strip() != token:
            raise PermissionError("Webhook token salah.")
        if not self.auto_trade_enabled():
            return {
                "accepted": False,
                "reason": "AUTO_TRADE_ENABLED=false. Sinyal diterima tapi order tidak dieksekusi.",
                "payload": payload,
            }
        self.symbol = normalize_symbol(str(payload.get("symbol", self.symbol)), self.symbol)
        raw_side = str(payload.get("side", payload.get("signal", ""))).strip().upper()
        side_map = {"LONG": "BUY", "BUY": "BUY", "SHORT": "SELL", "SELL": "SELL"}
        side = side_map.get(raw_side)
        if not side:
            raise ValueError("Webhook harus berisi side/signal: BUY, SELL, LONG, atau SHORT.")
        if env_bool("PREVENT_DOUBLE_POSITION", "true") and self.has_open_position():
            return {"accepted": False, "reason": f"Masih ada posisi terbuka di {self.symbol}. Entry baru dibatalkan.", "symbol": self.symbol}
        if self.has_reached_max_open_positions():
            return {"accepted": False, "reason": "Batas MAX_OPEN_POSITIONS tercapai. Entry baru dibatalkan.", "symbol": self.symbol}
        leverage = int(payload.get("leverage", os.getenv("DEFAULT_LEVERAGE", "3")))
        usdt_amount = effective_order_usdt(
            self.symbol,
            Decimal(str(payload.get("usdt", os.getenv("ORDER_USDT", "5")))),
        )
        quantity = self.calculate_quantity(usdt_amount)
        leverage_result = self.set_leverage(leverage)
        entry_price = self.get_price()
        entry_order_type = os.getenv("ENTRY_ORDER_TYPE", "MARKET").strip().upper()
        if entry_order_type == "LIMIT":
            limit_price = self.entry_limit_price(side, entry_price)
            client_order_id = f"mecbot_{self.symbol}_{side.lower()}_{int(time.time())}"
            order_result = self.place_futures_limit_order(
                side,
                price=limit_price,
                quantity=quantity,
                client_order_id=client_order_id,
            )
        else:
            order_result = self.place_futures_market_order(side, quantity=quantity)
        try:
            tp_sl_result = self.place_tp_sl_orders(side, entry_price)
        except RuntimeError as error:
            tp_sl_result = {
                "skipped": True,
                "reason": "TP/SL order gagal dibuat, tapi entry order sudah terkirim.",
                "error": str(error),
            }
        return {
            "accepted": True,
            "dry_run": self.dry_run(),
            "symbol": self.symbol,
            "side": side,
            "usdt_margin": str(usdt_amount),
            "leverage": leverage,
            "quantity": quantity,
            "entry_price_reference": str(entry_price),
            "entry_order_type": entry_order_type,
            "leverage_result": leverage_result,
            "order_result": order_result,
            "tp_sl_result": tp_sl_result,
        }


# =========================================================
# AUTO SCALPING TANPA TRADINGVIEW
# =========================================================

def to_decimal_list(values: list[str]) -> list[Decimal]:
    return [Decimal(str(value)) for value in values]


def ema(values: list[Decimal], period: int) -> list[Decimal]:
    if not values:
        return []
    multiplier = Decimal("2") / Decimal(period + 1)
    result: list[Decimal] = []
    current = values[0]
    for value in values:
        current = (value - current) * multiplier + current
        result.append(current)
    return result


def rsi(values: list[Decimal], period: int = 14) -> list[Decimal]:
    if len(values) < period + 1:
        return [Decimal("50")] * len(values)
    gains: list[Decimal] = [Decimal("0")]
    losses: list[Decimal] = [Decimal("0")]
    for index in range(1, len(values)):
        change = values[index] - values[index - 1]
        gains.append(max(change, Decimal("0")))
        losses.append(abs(min(change, Decimal("0"))))
    result: list[Decimal] = [Decimal("50")] * len(values)
    for index in range(period, len(values)):
        avg_gain = sum(gains[index - period + 1:index + 1]) / Decimal(period)
        avg_loss = sum(losses[index - period + 1:index + 1]) / Decimal(period)
        if avg_loss == 0:
            result[index] = Decimal("100")
        else:
            rs = avg_gain / avg_loss
            result[index] = Decimal("100") - (Decimal("100") / (Decimal("1") + rs))
    return result


def atr(candles: list[dict], period: int = 14) -> list[Decimal]:
    if len(candles) < 2:
        return [Decimal("0")] * len(candles)
    highs = to_decimal_list([item["high"] for item in candles])
    lows = to_decimal_list([item["low"] for item in candles])
    closes = to_decimal_list([item["close"] for item in candles])
    true_ranges: list[Decimal] = [highs[0] - lows[0]]
    for index in range(1, len(candles)):
        high_low = highs[index] - lows[index]
        high_close = abs(highs[index] - closes[index - 1])
        low_close = abs(lows[index] - closes[index - 1])
        true_ranges.append(max(high_low, high_close, low_close))
    result: list[Decimal] = [Decimal("0")] * len(candles)
    for index in range(period - 1, len(candles)):
        result[index] = sum(true_ranges[index - period + 1:index + 1]) / Decimal(period)
    return result


def average(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values) / Decimal(len(values))


def volume_ok(volumes: list[Decimal]) -> bool:
    if len(volumes) < 21:
        return True
    last_volume = volumes[-1]
    avg_volume = sum(volumes[-21:-1]) / Decimal("20")
    multiplier = Decimal(os.getenv("VOLUME_MULTIPLIER", "1.2"))
    return last_volume >= avg_volume * multiplier


def pivot_highs(values: list[Decimal], left: int, right: int) -> list[tuple[int, Decimal]]:
    pivots: list[tuple[int, Decimal]] = []
    if len(values) < left + right + 1:
        return pivots
    for index in range(left, len(values) - right):
        level = values[index]
        window = values[index - left:index + right + 1]
        if level == max(window) and window.count(level) == 1:
            pivots.append((index, level))
    return pivots


def pivot_lows(values: list[Decimal], left: int, right: int) -> list[tuple[int, Decimal]]:
    pivots: list[tuple[int, Decimal]] = []
    if len(values) < left + right + 1:
        return pivots
    for index in range(left, len(values) - right):
        level = values[index]
        window = values[index - left:index + right + 1]
        if level == min(window) and window.count(level) == 1:
            pivots.append((index, level))
    return pivots


def market_structure_signal(candles: list[dict], lookback: int) -> dict:
    highs = to_decimal_list([item["high"] for item in candles])
    lows = to_decimal_list([item["low"] for item in candles])
    closes = to_decimal_list([item["close"] for item in candles])
    if len(closes) < lookback * 2 + 3:
        return {"side": None, "tag": None, "level": None}

    high_pivots = pivot_highs(highs, lookback, lookback)
    low_pivots = pivot_lows(lows, lookback, lookback)
    previous_close = closes[-2]
    last_close = closes[-1]
    side = None
    tag = None
    level = None

    if high_pivots:
        last_high_index, last_high = high_pivots[-1]
        if last_high_index < len(closes) - 1 and previous_close <= last_high < last_close:
            side = "BUY"
            level = last_high
            if len(low_pivots) >= 2:
                tag = "CHoCH+" if low_pivots[-1][1] > low_pivots[-2][1] else "CHoCH"
            else:
                tag = "BOS"

    if low_pivots:
        last_low_index, last_low = low_pivots[-1]
        if last_low_index < len(closes) - 1 and previous_close >= last_low > last_close:
            side = "SELL"
            level = last_low
            if len(high_pivots) >= 2:
                tag = "CHoCH+" if high_pivots[-1][1] < high_pivots[-2][1] else "CHoCH"
            else:
                tag = "BOS"

    return {"side": side, "tag": tag, "level": str(level) if level is not None else None}


def latest_fvg_signal(candles: list[dict]) -> dict:
    highs = to_decimal_list([item["high"] for item in candles])
    lows = to_decimal_list([item["low"] for item in candles])
    closes = to_decimal_list([item["close"] for item in candles])
    opens = to_decimal_list([item["open"] for item in candles])
    if len(candles) < 4:
        return {"side": None, "gap": None}

    index = len(candles) - 1
    max_width = Decimal(os.getenv("PAC_FVG_MAX_WIDTH_PERCENT", "2"))
    recent_range = max(highs[-100:]) - min(lows[-100:]) if len(highs) >= 100 else max(highs) - min(lows)
    min_gap = recent_range * max(max_width, Decimal("0.1")) / Decimal("100")

    if opens[index] > closes[index] and lows[index - 2] > highs[index]:
        gap_size = lows[index - 2] - highs[index]
        if gap_size >= min_gap:
            return {"side": "SELL", "gap": str(gap_size)}
    if lows[index] > highs[index - 2]:
        gap_size = lows[index] - highs[index - 2]
        if gap_size >= min_gap:
            return {"side": "BUY", "gap": str(gap_size)}
    return {"side": None, "gap": None}


def fvg_zones(candles: list[dict]) -> list[dict]:
    highs = to_decimal_list([item["high"] for item in candles])
    lows = to_decimal_list([item["low"] for item in candles])
    opens = to_decimal_list([item["open"] for item in candles])
    closes = to_decimal_list([item["close"] for item in candles])
    zones: list[dict] = []
    if len(candles) < 4:
        return zones
    max_zones = int(os.getenv("PAC_FVG_MAX_ACTIVE", "8"))
    max_width = Decimal(os.getenv("PAC_FVG_MAX_WIDTH_PERCENT", "2"))
    recent_range = max(highs[-100:]) - min(lows[-100:]) if len(highs) >= 100 else max(highs) - min(lows)
    min_gap = recent_range * max(max_width, Decimal("0.1")) / Decimal("100")
    for index in range(2, len(candles)):
        if opens[index] > closes[index] and lows[index - 2] > highs[index]:
            gap_size = lows[index - 2] - highs[index]
            if gap_size >= min_gap:
                zones.append({"side": "SELL", "low": highs[index], "high": lows[index - 2], "index": index})
        elif lows[index] > highs[index - 2]:
            gap_size = lows[index] - highs[index - 2]
            if gap_size >= min_gap:
                zones.append({"side": "BUY", "low": highs[index - 2], "high": lows[index], "index": index})

    mitigation = os.getenv("PAC_FVG_MITIGATION", "Close").strip().lower()
    active: list[dict] = []
    for zone in zones[-max_zones * 3:]:
        mitigated = False
        for index in range(int(zone["index"]) + 1, len(candles)):
            high = highs[index]
            low = lows[index]
            close = closes[index]
            midpoint = (Decimal(str(zone["low"])) + Decimal(str(zone["high"]))) / Decimal("2")
            if zone["side"] == "BUY":
                mitigated = close < Decimal(str(zone["low"])) if mitigation == "close" else low <= midpoint
            else:
                mitigated = close > Decimal(str(zone["high"])) if mitigation == "close" else high >= midpoint
            if mitigated:
                break
        if not mitigated:
            active.append(zone)
    return active[-max_zones:]


def reversal_band_signal(candles: list[dict], atr_values: list[Decimal]) -> dict:
    closes = to_decimal_list([item["close"] for item in candles])
    if len(closes) < 31 or not atr_values:
        return {"side": None}
    length = int(os.getenv("PAC_REVERSAL_BANDS_LENGTH", "30"))
    basis = average(closes[-length:])
    span = atr_values[-1]
    last_close = closes[-1]
    upper_1 = basis + span * Decimal("3")
    lower_1 = basis - span * Decimal("3")
    if last_close <= lower_1:
        return {"side": "BUY", "zone": "lower"}
    if last_close >= upper_1:
        return {"side": "SELL", "zone": "upper"}
    return {"side": None}


def volume_profile_signal(candles: list[dict]) -> dict:
    lookback = int(os.getenv("PAC_PROFILE_LOOKBACK", "200"))
    rows = int(os.getenv("PAC_PROFILE_ROWS", "25"))
    if len(candles) < 10 or rows <= 0:
        return {"side": None}
    window = candles[-min(lookback, len(candles)):]
    highs = to_decimal_list([item["high"] for item in window])
    lows = to_decimal_list([item["low"] for item in window])
    closes = to_decimal_list([item["close"] for item in window])
    volumes = to_decimal_list([item["volume"] for item in window])
    low_price = min(lows)
    high_price = max(highs)
    step = (high_price - low_price) / Decimal(rows)
    if step <= 0:
        return {"side": None}
    buckets = [Decimal("0")] * rows
    bullish_buckets = [Decimal("0")] * rows
    for candle, volume in zip(window, volumes):
        candle_high = Decimal(str(candle["high"]))
        candle_low = Decimal(str(candle["low"]))
        candle_open = Decimal(str(candle["open"]))
        candle_close = Decimal(str(candle["close"]))
        span = max(candle_high - candle_low, step)
        for row in range(rows):
            row_low = low_price + step * Decimal(row)
            row_high = row_low + step
            overlap = max(Decimal("0"), min(candle_high, row_high) - max(candle_low, row_low))
            if overlap <= 0:
                continue
            portion = overlap / span
            value = volume * portion
            buckets[row] += value
            if candle_close > candle_open:
                bullish_buckets[row] += value
    poc_index = max(range(rows), key=lambda row: buckets[row])
    poc = low_price + step * (Decimal(poc_index) + Decimal("0.5"))
    last_close = closes[-1]
    total = buckets[poc_index]
    bullish_ratio = bullish_buckets[poc_index] / total if total > 0 else Decimal("0.5")
    threshold = Decimal(os.getenv("PAC_PROFILE_POC_DISTANCE_PERCENT", "0.35"))
    distance_percent = abs(last_close - poc) / last_close * Decimal("100") if last_close > 0 else Decimal("0")
    side = None
    if distance_percent <= threshold:
        side = "BUY" if last_close >= poc and bullish_ratio >= Decimal("0.5") else "SELL" if last_close <= poc and bullish_ratio <= Decimal("0.5") else None
    return {"side": side, "poc": str(poc), "bullish_ratio": str(bullish_ratio), "distance_percent": str(distance_percent)}


def premium_discount_signal(candles: list[dict]) -> dict:
    lookback = int(os.getenv("PAC_SWING_LOOKBACK", "50"))
    highs = to_decimal_list([item["high"] for item in candles])
    lows = to_decimal_list([item["low"] for item in candles])
    closes = to_decimal_list([item["close"] for item in candles])
    if len(candles) < lookback:
        return {"side": None}
    swing_high = max(highs[-lookback:])
    swing_low = min(lows[-lookback:])
    span = swing_high - swing_low
    if span <= 0:
        return {"side": None}
    equilibrium = (swing_high + swing_low) / Decimal("2")
    position = (closes[-1] - swing_low) / span
    discount_level = Decimal(os.getenv("PAC_DISCOUNT_LEVEL", "0.35"))
    premium_level = Decimal(os.getenv("PAC_PREMIUM_LEVEL", "0.65"))
    side = "BUY" if position <= discount_level else "SELL" if position >= premium_level else None
    return {"side": side, "position": str(position), "equilibrium": str(equilibrium), "swing_high": str(swing_high), "swing_low": str(swing_low)}


def order_block_signal(candles: list[dict], atr_values: list[Decimal]) -> dict:
    lookback = int(os.getenv("PAC_INTERNAL_LOOKBACK", "5"))
    highs = to_decimal_list([item["high"] for item in candles])
    lows = to_decimal_list([item["low"] for item in candles])
    closes = to_decimal_list([item["close"] for item in candles])
    opens = to_decimal_list([item["open"] for item in candles])
    if len(candles) < lookback * 2 + 5:
        return {"side": None}
    high_pivots = pivot_highs(highs, lookback, lookback)
    low_pivots = pivot_lows(lows, lookback, lookback)
    atr_now = atr_values[-1] if atr_values else Decimal("0")
    tolerance = atr_now * Decimal(os.getenv("PAC_ORDER_BLOCK_TOUCH_ATR", "0.35"))
    zones: list[dict] = []
    if high_pivots and closes[-1] > high_pivots[-1][1]:
        for index in range(high_pivots[-1][0], max(0, high_pivots[-1][0] - 12), -1):
            if closes[index] < opens[index]:
                zones.append({"side": "BUY", "low": lows[index], "high": highs[index], "index": index})
                break
    if low_pivots and closes[-1] < low_pivots[-1][1]:
        for index in range(low_pivots[-1][0], max(0, low_pivots[-1][0] - 12), -1):
            if closes[index] > opens[index]:
                zones.append({"side": "SELL", "low": lows[index], "high": highs[index], "index": index})
                break
    last_close = closes[-1]
    for zone in zones:
        low = Decimal(str(zone["low"]))
        high = Decimal(str(zone["high"]))
        touched = low - tolerance <= last_close <= high + tolerance
        if touched:
            return {**zone, "low": str(low), "high": str(high)}
    return zones[-1] if zones else {"side": None}


def strong_weak_signal(candles: list[dict]) -> dict:
    lookback = int(os.getenv("PAC_SWING_LOOKBACK", "50"))
    highs = to_decimal_list([item["high"] for item in candles])
    lows = to_decimal_list([item["low"] for item in candles])
    closes = to_decimal_list([item["close"] for item in candles])
    if len(candles) < lookback + 2:
        return {"side": None}
    swing_high = max(highs[-lookback:-1])
    swing_low = min(lows[-lookback:-1])
    if closes[-1] > swing_high:
        return {"side": "BUY", "tag": "weak high break", "level": str(swing_high)}
    if closes[-1] < swing_low:
        return {"side": "SELL", "tag": "weak low break", "level": str(swing_low)}
    return {"side": None, "level_high": str(swing_high), "level_low": str(swing_low)}


def price_action_concepts(candles: list[dict], atr_values: list[Decimal]) -> dict:
    internal = market_structure_signal(candles, int(os.getenv("PAC_INTERNAL_LOOKBACK", "5")))
    swing = market_structure_signal(candles, int(os.getenv("PAC_SWING_LOOKBACK", "50")))
    fvg = latest_fvg_signal(candles)
    active_fvg = fvg_zones(candles)
    reversal = reversal_band_signal(candles, atr_values)
    profile = volume_profile_signal(candles)
    premium_discount = premium_discount_signal(candles)
    order_block = order_block_signal(candles, atr_values)
    strong_weak = strong_weak_signal(candles)
    return {
        "internal": internal,
        "swing": swing,
        "fvg": fvg,
        "active_fvg": active_fvg,
        "reversal": reversal,
        "profile": profile,
        "premium_discount": premium_discount,
        "order_block": order_block,
        "strong_weak": strong_weak,
    }


def order_book_confirmation(client: BinanceClient, side: str) -> tuple[bool, dict, str]:
    if not env_bool("USE_ORDER_BOOK_CONFIRMATION", "true"):
        return True, {}, "order book filter off"
    depth_limit = int(os.getenv("ORDER_BOOK_DEPTH_LIMIT", "20"))
    max_spread_percent = Decimal(os.getenv("ORDER_BOOK_MAX_SPREAD_PERCENT", "0.08"))
    min_imbalance = Decimal(os.getenv("ORDER_BOOK_MIN_IMBALANCE", "0.55"))
    book = client.order_book(limit=depth_limit)
    bid_ratio = Decimal(str(book.get("bid_ratio", "0.5")))
    ask_ratio = Decimal(str(book.get("ask_ratio", "0.5")))
    spread_percent = Decimal(str(book.get("spread_percent", "0")))
    if spread_percent > max_spread_percent:
        return False, book, f"spread lebar {spread_percent.quantize(Decimal('0.0001'))}%"
    if side == "BUY" and bid_ratio < min_imbalance:
        return False, book, f"bid support kurang {bid_ratio.quantize(Decimal('0.01'))}"
    if side == "SELL" and ask_ratio < min_imbalance:
        return False, book, f"ask pressure kurang {ask_ratio.quantize(Decimal('0.01'))}"
    label = "bid support" if side == "BUY" else "ask pressure"
    ratio = bid_ratio if side == "BUY" else ask_ratio
    return True, book, f"{label} valid {ratio.quantize(Decimal('0.01'))}"


def generate_auto_signal(symbol: str) -> dict:
    interval = os.getenv("SCALPING_TIMEFRAME", "5m")
    limit = int(os.getenv("SCALPING_KLINE_LIMIT", "120"))
    client = BinanceClient(symbol=symbol)
    market = client.klines(interval=interval, limit=limit)
    candles = market["candles"]
    closes = to_decimal_list([item["close"] for item in candles])
    opens = to_decimal_list([item["open"] for item in candles])
    volumes = to_decimal_list([item["volume"] for item in candles])
    fast_period = int(os.getenv("EMA_FAST", "9"))
    slow_period = int(os.getenv("EMA_SLOW", "21"))
    rsi_period = int(os.getenv("RSI_PERIOD", "14"))
    ema_fast = ema(closes, fast_period)
    ema_slow = ema(closes, slow_period)
    rsi_values = rsi(closes, rsi_period)
    fast_now = ema_fast[-1]
    slow_now = ema_slow[-1]
    fast_prev = ema_fast[-2]
    slow_prev = ema_slow[-2]
    rsi_now = rsi_values[-1]
    last_close = closes[-1]
    last_open = opens[-1]
    buy_rsi = Decimal(os.getenv("BUY_RSI", "55"))
    sell_rsi = Decimal(os.getenv("SELL_RSI", "45"))
    
    # Gold bisa punya ritme beda; biarkan threshold-nya tetap bisa diatur dari .env.
    if symbol in {"XAUUSDT", "XAUUSD"}:
        buy_rsi = Decimal(os.getenv("GOLD_BUY_RSI", "58"))
        sell_rsi = Decimal(os.getenv("GOLD_SELL_RSI", "42"))

    has_volume = volume_ok(volumes)
    ema_trend_up = fast_now > slow_now
    ema_trend_down = fast_now < slow_now
    gold_momentum_mode = symbol in {"XAUUSDT", "XAUUSD"} and env_bool("GOLD_MOMENTUM_MODE", "false")
    strategy_mode = os.getenv("SIGNAL_STRATEGY_MODE", "score").strip().lower()
    signal_score_threshold = int(os.getenv("SIGNAL_SCORE_THRESHOLD", "4"))
    signal = None
    reason = "NO SIGNAL"

    if strategy_mode == "score":
        trend_interval = os.getenv("TREND_TIMEFRAME", "5m")
        trend_limit = int(os.getenv("TREND_KLINE_LIMIT", "120"))
        trend_market = client.klines(interval=trend_interval, limit=trend_limit)
        trend_candles = trend_market["candles"]
        trend_closes = to_decimal_list([item["close"] for item in trend_candles])
        trend_fast = ema(trend_closes, int(os.getenv("TREND_EMA_FAST", "21")))
        trend_slow = ema(trend_closes, int(os.getenv("TREND_EMA_SLOW", "55")))
        trend_rsi_values = rsi(trend_closes, rsi_period)
        trend_up = trend_fast[-1] > trend_slow[-1] and trend_closes[-1] > trend_fast[-1]
        trend_down = trend_fast[-1] < trend_slow[-1] and trend_closes[-1] < trend_fast[-1]
        trend_rsi = trend_rsi_values[-1]
        atr_values = atr(candles, int(os.getenv("ATR_PERIOD", "14")))
        atr_now = atr_values[-1]
        atr_avg = average([value for value in atr_values[-21:] if value > 0])
        pac = price_action_concepts(candles, atr_values) if env_bool("ENABLE_PRICE_ACTION_CONCEPTS", "true") else {}
        volatility_ok = atr_avg == 0 or (
            atr_now >= atr_avg * Decimal(os.getenv("ATR_MIN_MULTIPLIER", "0.75"))
            and atr_now <= atr_avg * Decimal(os.getenv("ATR_MAX_MULTIPLIER", "1.8"))
        )
        bullish_candle = last_close > last_open
        bearish_candle = last_close < last_open
        recent_high = max(closes[-6:-1]) if len(closes) >= 6 else last_close
        recent_low = min(closes[-6:-1]) if len(closes) >= 6 else last_close
        breakout_up = last_close > recent_high
        breakout_down = last_close < recent_low

        buy_score = 0
        sell_score = 0
        buy_reasons: list[str] = []
        sell_reasons: list[str] = []

        if trend_up:
            buy_score += 2
            buy_reasons.append(f"{trend_interval} trend naik")
        if trend_down:
            sell_score += 2
            sell_reasons.append(f"{trend_interval} trend turun")
        if ema_trend_up:
            buy_score += 1
            buy_reasons.append(f"{interval} EMA naik")
        if ema_trend_down:
            sell_score += 1
            sell_reasons.append(f"{interval} EMA turun")
        if rsi_now >= buy_rsi and trend_rsi >= Decimal(os.getenv("TREND_BUY_RSI", "52")):
            buy_score += 1
            buy_reasons.append(f"RSI kuat {rsi_now.quantize(Decimal('0.01'))}")
        if rsi_now <= sell_rsi and trend_rsi <= Decimal(os.getenv("TREND_SELL_RSI", "48")):
            sell_score += 1
            sell_reasons.append(f"RSI lemah {rsi_now.quantize(Decimal('0.01'))}")
        rsi_regime = {
            "enabled": env_bool("USE_RSI_REGIME_FILTER", "false"),
            "overbought": str(Decimal(os.getenv("RSI_OVERBOUGHT", "70"))),
            "oversold": str(Decimal(os.getenv("RSI_OVERSOLD", "30"))),
            "long_block_above": str(Decimal(os.getenv("RSI_LONG_BLOCK_ABOVE", "72"))),
            "short_block_below": str(Decimal(os.getenv("RSI_SHORT_BLOCK_BELOW", "28"))),
        }
        if rsi_regime["enabled"]:
            rsi_overbought = Decimal(os.getenv("RSI_OVERBOUGHT", "70"))
            rsi_oversold = Decimal(os.getenv("RSI_OVERSOLD", "30"))
            rsi_long_block_above = Decimal(os.getenv("RSI_LONG_BLOCK_ABOVE", "72"))
            rsi_short_block_below = Decimal(os.getenv("RSI_SHORT_BLOCK_BELOW", "28"))
            rsi_reversal_long_below = Decimal(os.getenv("RSI_REVERSAL_LONG_BELOW", "35"))
            rsi_reversal_short_above = Decimal(os.getenv("RSI_REVERSAL_SHORT_ABOVE", "65"))
            rsi_regime_weight = int(os.getenv("RSI_REGIME_WEIGHT", "1"))

            if rsi_now <= rsi_oversold or rsi_now <= rsi_reversal_long_below:
                buy_score += rsi_regime_weight
                buy_reasons.append(f"RSI oversold/rebound {rsi_now.quantize(Decimal('0.01'))}")
            if rsi_now >= rsi_overbought or rsi_now >= rsi_reversal_short_above:
                sell_score += rsi_regime_weight
                sell_reasons.append(f"RSI overbought/rejection {rsi_now.quantize(Decimal('0.01'))}")
            if rsi_now >= rsi_long_block_above or trend_rsi >= rsi_long_block_above:
                buy_score -= rsi_regime_weight * 2
                buy_reasons.append("RSI terlalu panas, long dikurangi")
            if rsi_now <= rsi_short_block_below or trend_rsi <= rsi_short_block_below:
                sell_score -= rsi_regime_weight * 2
                sell_reasons.append("RSI terlalu jenuh jual, short dikurangi")
        if bullish_candle:
            buy_score += 1
            buy_reasons.append("candle hijau")
        if bearish_candle:
            sell_score += 1
            sell_reasons.append("candle merah")
        if breakout_up:
            buy_score += 1
            buy_reasons.append("break recent high")
        if breakout_down:
            sell_score += 1
            sell_reasons.append("break recent low")
        if has_volume:
            buy_score += 1
            sell_score += 1
            buy_reasons.append("volume valid")
            sell_reasons.append("volume valid")
        if pac:
            internal = pac.get("internal", {})
            swing = pac.get("swing", {})
            fvg = pac.get("fvg", {})
            active_fvg = pac.get("active_fvg", [])
            reversal = pac.get("reversal", {})
            profile = pac.get("profile", {})
            premium_discount = pac.get("premium_discount", {})
            order_block = pac.get("order_block", {})
            strong_weak = pac.get("strong_weak", {})
            internal_weight = int(os.getenv("PAC_INTERNAL_STRUCTURE_WEIGHT", "2"))
            swing_weight = int(os.getenv("PAC_SWING_STRUCTURE_WEIGHT", "3"))
            fvg_weight = int(os.getenv("PAC_FVG_WEIGHT", "1"))
            reversal_weight = int(os.getenv("PAC_REVERSAL_BAND_WEIGHT", "1"))
            profile_weight = int(os.getenv("PAC_PROFILE_WEIGHT", "1"))
            premium_discount_weight = int(os.getenv("PAC_PREMIUM_DISCOUNT_WEIGHT", "1"))
            order_block_weight = int(os.getenv("PAC_ORDER_BLOCK_WEIGHT", "2"))
            strong_weak_weight = int(os.getenv("PAC_STRONG_WEAK_WEIGHT", "1"))

            if internal.get("side") == "BUY":
                buy_score += internal_weight
                buy_reasons.append(f"internal {internal.get('tag')}")
            elif internal.get("side") == "SELL":
                sell_score += internal_weight
                sell_reasons.append(f"internal {internal.get('tag')}")

            if swing.get("side") == "BUY":
                buy_score += swing_weight
                buy_reasons.append(f"swing {swing.get('tag')}")
            elif swing.get("side") == "SELL":
                sell_score += swing_weight
                sell_reasons.append(f"swing {swing.get('tag')}")

            if fvg.get("side") == "BUY":
                buy_score += fvg_weight
                buy_reasons.append("bullish FVG")
            elif fvg.get("side") == "SELL":
                sell_score += fvg_weight
                sell_reasons.append("bearish FVG")

            if reversal.get("side") == "BUY":
                buy_score += reversal_weight
                buy_reasons.append("reversal lower band")
            elif reversal.get("side") == "SELL":
                sell_score += reversal_weight
                sell_reasons.append("reversal upper band")

            if profile.get("side") == "BUY":
                buy_score += profile_weight
                buy_reasons.append("PoC support")
            elif profile.get("side") == "SELL":
                sell_score += profile_weight
                sell_reasons.append("PoC resistance")

            if premium_discount.get("side") == "BUY":
                buy_score += premium_discount_weight
                buy_reasons.append("discount zone")
            elif premium_discount.get("side") == "SELL":
                sell_score += premium_discount_weight
                sell_reasons.append("premium zone")

            if order_block.get("side") == "BUY":
                buy_score += order_block_weight
                buy_reasons.append("bullish order block")
            elif order_block.get("side") == "SELL":
                sell_score += order_block_weight
                sell_reasons.append("bearish order block")

            if strong_weak.get("side") == "BUY":
                buy_score += strong_weak_weight
                buy_reasons.append(str(strong_weak.get("tag", "strong/weak break")))
            elif strong_weak.get("side") == "SELL":
                sell_score += strong_weak_weight
                sell_reasons.append(str(strong_weak.get("tag", "strong/weak break")))

            for zone in active_fvg:
                if zone.get("side") == "BUY":
                    buy_score += fvg_weight
                    buy_reasons.append("active bullish FVG")
                    break
            for zone in active_fvg:
                if zone.get("side") == "SELL":
                    sell_score += fvg_weight
                    sell_reasons.append("active bearish FVG")
                    break
        if not volatility_ok:
            buy_score -= 2
            sell_score -= 2
        if env_bool("DISABLE_COUNTER_TREND", "true"):
            if trend_down:
                buy_score -= 3
            if trend_up:
                sell_score -= 3

        book: dict = {}
        book_reason = "-"
        if buy_score >= signal_score_threshold and buy_score > sell_score:
            book_ok, book, book_reason = order_book_confirmation(client, "BUY")
            if book_ok:
                signal = "BUY"
                reason = f"SCORE BUY {buy_score}/{signal_score_threshold}: " + ", ".join(buy_reasons + [book_reason])
            else:
                reason = f"NO SIGNAL: BUY score valid tapi order book belum confirm ({book_reason})"
        elif sell_score >= signal_score_threshold and sell_score > buy_score:
            book_ok, book, book_reason = order_book_confirmation(client, "SELL")
            if book_ok:
                signal = "SELL"
                reason = f"SCORE SELL {sell_score}/{signal_score_threshold}: " + ", ".join(sell_reasons + [book_reason])
            else:
                reason = f"NO SIGNAL: SELL score valid tapi order book belum confirm ({book_reason})"
        else:
            reason = (
                f"NO SIGNAL: buy_score={buy_score}, sell_score={sell_score}, need={signal_score_threshold}, "
                f"trend_rsi={trend_rsi.quantize(Decimal('0.01'))}, atr_ok={volatility_ok}, volume_ok={has_volume}"
            )
        return {
            "symbol": symbol,
            "interval": interval,
            "signal": signal,
            "reason": reason,
            "close": str(last_close),
            "ema_fast": str(fast_now),
            "ema_slow": str(slow_now),
            "rsi": str(rsi_now.quantize(Decimal("0.01"))),
            "score_buy": buy_score,
            "score_sell": sell_score,
            "trend_interval": trend_interval,
            "trend_rsi": str(trend_rsi.quantize(Decimal("0.01"))),
            "rsi_regime": rsi_regime,
            "atr": str(atr_now),
            "price_action_concepts": pac,
            "order_book": book,
            "order_book_reason": book_reason,
        }

    if fast_prev <= slow_prev and ema_trend_up and rsi_now >= buy_rsi and has_volume:
        signal = "BUY"
        reason = "EMA fast cross up + RSI kuat"
    elif fast_prev >= slow_prev and ema_trend_down and rsi_now <= sell_rsi and has_volume:
        signal = "SELL"
        reason = "EMA fast cross down + RSI lemah"
    elif env_bool("FAST_SIGNAL_MODE", "false") and has_volume:
        if ema_trend_up and rsi_now >= buy_rsi:
            signal = "BUY"
            reason = "FAST MODE: trend EMA naik + RSI cukup kuat"
        elif ema_trend_down and rsi_now <= sell_rsi:
            signal = "SELL"
            reason = "FAST MODE: trend EMA turun + RSI cukup lemah"
    if not signal and gold_momentum_mode and has_volume:
        if rsi_now >= buy_rsi:
            signal = "BUY"
            reason = "GOLD MOMENTUM: RSI kuat, masuk tanpa tunggu EMA cross"
        elif rsi_now <= sell_rsi:
            signal = "SELL"
            reason = "GOLD MOMENTUM: RSI lemah, masuk tanpa tunggu EMA cross"
    if not signal:
        ema_text = "naik" if ema_trend_up else "turun" if ema_trend_down else "datar"
        reason = (
            f"NO SIGNAL: EMA {ema_text}, RSI={rsi_now.quantize(Decimal('0.01'))}, "
            f"buy>={buy_rsi}, sell<={sell_rsi}, volume_ok={has_volume}"
        )
    return {
        "symbol": symbol,
        "interval": interval,
        "signal": signal,
        "reason": reason,
        "close": str(last_close),
        "ema_fast": str(fast_now),
        "ema_slow": str(slow_now),
        "rsi": str(rsi_now.quantize(Decimal("0.01"))),
    }


def cancel_stale_limit_orders(symbols: list[str]) -> None:
    if not env_bool("CANCEL_STALE_LIMIT_ORDERS", "true"):
        return
    max_age_seconds = int(os.getenv("LIMIT_ORDER_MAX_AGE_SECONDS", "180"))
    if max_age_seconds <= 0:
        return
    now_ms = int(time.time() * 1000)
    for symbol in symbols:
        try:
            client = BinanceClient(symbol=symbol)
            for order in client.open_orders(symbol):
                order_type = str(order.get("type", "")).upper()
                reduce_only = str(order.get("reduceOnly", "false")).lower() == "true"
                close_position = str(order.get("closePosition", "false")).lower() == "true"
                client_order_id = str(order.get("clientOrderId", ""))
                if order_type != "LIMIT" or reduce_only or close_position:
                    continue
                if not client_order_id.startswith("mecbot_"):
                    continue
                order_time = int(order.get("time", order.get("updateTime", "0")) or "0")
                age_seconds = int((now_ms - order_time) / 1000) if order_time > 0 else max_age_seconds + 1
                if age_seconds < max_age_seconds:
                    continue
                order_id = order.get("orderId")
                result = client.cancel_order(symbol, order_id)
                print(f"[CANCEL STALE LIMIT] {symbol} order={order_id} age={age_seconds}s result={json.dumps(result, ensure_ascii=False)}")
                send_telegram(
                    f"<b>CANCEL LIMIT ORDER</b>\n"
                    f"{symbol}\n"
                    f"Order: {order_id}\n"
                    f"Age: {age_seconds}s"
                )
        except Exception as error:
            print(f"[CANCEL STALE LIMIT ERROR] {symbol}: {error}")


def monitor_open_positions(symbols: list[str]) -> int:
    position_client = BinanceClient(symbol=symbols[0] if symbols else None)
    positions = position_client.open_positions()
    quick_profit_close = env_bool("PROFIT_QUICK_CLOSE", "true")
    force_close_loss = env_bool("FORCE_CLOSE_LOSS", "true")
    max_hold_seconds = int(os.getenv("MAX_HOLD_SECONDS", "300"))
    min_profit_hold_seconds = int(os.getenv("FORCE_CLOSE_MIN_HOLD_SECONDS", "0"))
    force_close_profit_usdt = Decimal(os.getenv("FORCE_CLOSE_PROFIT_USDT", "0"))
    force_close_profit_percent = Decimal(os.getenv("FORCE_CLOSE_PROFIT_PERCENT", "0.15"))
    force_close_loss_usdt = Decimal(os.getenv("FORCE_CLOSE_LOSS_USDT", "0"))
    force_close_loss_percent = Decimal(os.getenv("FORCE_CLOSE_LOSS_PERCENT", os.getenv("SL_PERCENT", "0.25")))
    force_close_profit_pips = Decimal(os.getenv("FORCE_CLOSE_PROFIT_PIPS", os.getenv("TAKE_PROFIT_PIPS", "50")))
    force_close_loss_pips = Decimal(os.getenv("FORCE_CLOSE_LOSS_PIPS", os.getenv("STOP_LOSS_PIPS", "35")))
    break_even_stop = env_bool("USE_BREAK_EVEN_STOP", "true")
    break_even_activate_usdt = Decimal(os.getenv("BREAK_EVEN_ACTIVATE_PROFIT_USDT", "0.01"))
    break_even_lock_usdt = Decimal(os.getenv("BREAK_EVEN_LOCK_PROFIT_USDT", "0.001"))
    trailing_profit = env_bool("USE_TRAILING_PROFIT_LOCK", "true")
    trailing_drawdown_usdt = Decimal(os.getenv("TRAILING_PROFIT_DRAWDOWN_USDT", "0.015"))

    open_count = len(positions)
    active_keys: set[str] = set()
    for position in positions:
        try:
            position_symbol = str(position.get("symbol", "")).upper()
            position_amount = Decimal(str(position.get("positionAmt", "0")))
            if not position_symbol or position_amount == 0:
                continue

            pnl_percent = Decimal(str(position.get("pnlPercent", "0")))
            pnl_usdt = Decimal(str(position.get("unRealizedProfit", position.get("unrealizedProfit", "0"))))
            side_label = "LONG" if position_amount > 0 else "SHORT"
            profit_key = f"{position_symbol}:{side_label}"
            active_keys.add(profit_key)
            
            peak_profit = max(POSITION_PROFIT_MEMORY.get(profit_key, pnl_usdt), pnl_usdt)
            if profit_key not in POSITION_PROFIT_MEMORY or peak_profit > POSITION_PROFIT_MEMORY[profit_key]:
                POSITION_PROFIT_MEMORY[profit_key] = peak_profit
                save_position_profit_memory(POSITION_PROFIT_MEMORY)
                
            update_time_ms = int(position.get("updateTime", "0") or "0")
            hold_seconds = int(time.time() - (update_time_ms / 1000)) if update_time_ms > 0 else max_hold_seconds

            entry_price = Decimal(str(position.get("entryPrice", "0")))
            mark_price = Decimal(str(position.get("markPrice", "0")))
            pip_size = symbol_pip_size(position_symbol)
            price_move = Decimal("0")
            if entry_price > 0 and mark_price > 0 and pip_size > 0:
                price_move = (mark_price - entry_price) / pip_size if position_amount > 0 else (entry_price - mark_price) / pip_size

            profit_target_hit = pnl_percent >= force_close_profit_percent
            if force_close_profit_usdt > 0:
                profit_target_hit = pnl_usdt >= force_close_profit_usdt
            if force_close_profit_pips > 0:
                profit_target_hit = price_move >= force_close_profit_pips
            profit_hold_ok = hold_seconds >= min_profit_hold_seconds
            time_exit_ok = (not quick_profit_close) and hold_seconds >= max_hold_seconds
            loss_limit_hit = force_close_loss and pnl_percent <= -force_close_loss_percent
            if force_close_loss and force_close_loss_usdt > 0:
                loss_limit_hit = pnl_usdt <= -force_close_loss_usdt
            if force_close_loss and force_close_loss_pips > 0:
                loss_limit_hit = price_move <= -force_close_loss_pips
            break_even_hit = break_even_stop and peak_profit >= break_even_activate_usdt and pnl_usdt <= break_even_lock_usdt
            trailing_profit_hit = (
                trailing_profit
                and peak_profit >= break_even_activate_usdt
                and trailing_drawdown_usdt > 0
                and peak_profit - pnl_usdt >= trailing_drawdown_usdt
                and pnl_usdt > break_even_lock_usdt
            )

            if (
                profit_target_hit and (quick_profit_close and profit_hold_ok or time_exit_ok)
                or loss_limit_hit
                or break_even_hit
                or trailing_profit_hit
            ):
                close_client = BinanceClient(symbol=position_symbol)
                if loss_limit_hit:
                    close_reason = "LOSS"
                elif break_even_hit:
                    close_reason = "BREAK EVEN"
                elif trailing_profit_hit:
                    close_reason = "TRAILING PROFIT"
                else:
                    close_reason = "PROFIT"
                print(
                    f"[FORCE CLOSE {close_reason}] {position_symbol} {side_label} "
                    f"pnl={pnl_percent}% usdt={pnl_usdt} peak={peak_profit} pips={price_move} hold={hold_seconds}s quick={quick_profit_close}"
                )
                close_result = close_client.close_position_amount(position_amount)
                POSITION_PROFIT_MEMORY.pop(profit_key, None)
                save_position_profit_memory(POSITION_PROFIT_MEMORY)
                print("[FORCE CLOSE RESULT]", json.dumps(close_result, ensure_ascii=False))
                send_telegram(
                    f"<b>FORCE CLOSE {close_reason}</b>\n"
                    f"{position_symbol} {side_label}\n"
                    f"PnL: {pnl_percent}% | {pnl_usdt} USDT | Pips: {price_move}\n"
                    f"Peak: {peak_profit} USDT\n"
                    f"Result: {json.dumps(close_result, ensure_ascii=False)}"
                )
                open_count = max(0, open_count - 1)
        except Exception as close_error:
            print(f"[FORCE CLOSE ERROR] {close_error}")

    memory_changed = False
    for key in list(POSITION_PROFIT_MEMORY):
        if key not in active_keys:
            POSITION_PROFIT_MEMORY.pop(key, None)
            memory_changed = True
    if memory_changed:
        save_position_profit_memory(POSITION_PROFIT_MEMORY)
    return open_count


def auto_scalping_loop() -> None:
    interval_seconds = int(os.getenv("SCALPING_INTERVAL", "60"))
    position_interval_seconds = int(os.getenv("POSITION_MONITOR_INTERVAL_SECONDS", "3"))
    last_signal_scan_ts = 0
    print("=" * 70)
    print("AUTO SCALPING LOOP START")
    print("Symbols:", os.getenv("SCALPING_SYMBOLS", "XAUUSDT"))
    print("Interval:", interval_seconds, "seconds")
    print("Position monitor:", position_interval_seconds, "seconds")
    print("=" * 70)
    while True:
        try:
            symbols = configured_scalping_symbols()
            cancel_stale_limit_orders(symbols)
            open_count = monitor_open_positions(symbols)
            max_positions = int(os.getenv("MAX_OPEN_POSITIONS", "1"))
            now = int(time.time())
            if now - last_signal_scan_ts < interval_seconds:
                time.sleep(position_interval_seconds)
                continue
            last_signal_scan_ts = now

            for symbol in symbols:
                try:
                    signal_info = cached_auto_signal(symbol)
                    print(
                        f"[SCAN] {symbol} | signal={signal_info['signal']} | "
                        f"rsi={signal_info['rsi']} | open={open_count}/{max_positions} | "
                        f"reason={signal_info['reason']}"
                    )
                    if not signal_info["signal"]:
                        continue
                    trigger_ok, trigger_reason = score_trigger_ok(signal_info)
                    if not trigger_ok:
                        print(f"[SKIP] {symbol} {trigger_reason}.")
                        continue
                    notify_signal_once(signal_info)
                    can_enter, enter_reason = can_auto_enter(symbol)
                    if not can_enter:
                        print(f"[SKIP] {symbol} {enter_reason}.")
                        continue
                    client = BinanceClient(symbol=symbol)
                    if env_bool("PREVENT_DOUBLE_POSITION", "true") and client.has_open_position():
                        print(f"[SKIP] {symbol} masih punya posisi terbuka.")
                        continue
                    if client.has_reached_max_open_positions():
                        print(f"[SKIP] {symbol} batas MAX_OPEN_POSITIONS tercapai.")
                        continue
                    payload = {
                        "symbol": symbol,
                        "side": signal_info["signal"],
                        "usdt": str(effective_order_usdt(symbol, Decimal(os.getenv("ORDER_USDT", "5")))),
                        "leverage": os.getenv("DEFAULT_LEVERAGE", "3"),
                    }
                    print(f"[AUTO ENTRY] {payload}")
                    result = client.handle_webhook_signal(payload)
                    print("[AUTO RESULT]", json.dumps(result, ensure_ascii=False))
                    send_telegram(
                        f"<b>AUTO RESULT</b>\n"
                        f"{symbol} {signal_info['signal']}\n"
                        f"Accepted: {result.get('accepted')}\n"
                        f"Reason: {result.get('reason', '-')}\n"
                        f"Qty: {result.get('quantity', '-')}"
                    )
                    if result.get("accepted"):
                        record_auto_entry(symbol)
                        open_count += 1
                except Exception as error:
                    print(f"[PAIR ERROR] {symbol}: {error}")
            time.sleep(position_interval_seconds)
        except Exception as error:
            print("[AUTO SCALPING ERROR]", error)
            time.sleep(position_interval_seconds)


# =========================================================
# LOCAL AI / MEMORY
# =========================================================

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


def append_memory(role: str, content: str) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    event = {"ts": int(time.time()), "role": role, "content": content}
    with MEMORY_FILE.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")


def recent_context(limit: int = 12) -> str:
    if not MEMORY_FILE.exists():
        return ""
    lines = MEMORY_FILE.read_text(encoding="utf-8").splitlines()[-limit:]
    entries: list[str] = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        role = "User" if event.get("role") == "user" else "Bot"
        entries.append(f"{role}: {event.get('content', '')}")
    return "\n".join(entries)


def local_reply(message: str) -> str:
    text = message.lower()
    if any(word in text for word in ["binance", "testnet", "btc", "crypto"]):
        return (
            "Bot Binance lokal aktif. Untuk RSA official, isi `BINANCE_KEY_TYPE=rsa`, "
            "`BINANCE_API_KEY`, dan `BINANCE_PRIVATE_KEY_PATH` di `.env`. "
            "Untuk auto scalping, aktifkan `AUTO_SCALPING=true`."
        )
    if any(word in text for word in ["xau", "gold", "emas", "trading", "signal"]):
        return "Mode lokal aktif. Bot bisa membaca Binance Futures dan menjalankan auto scalping."
    if any(word in text for word in ["api", "openai", "chatgpt"]):
        return "Bot lokal siap. Untuk respons OpenAI, isi `OPENAI_API_KEY` di `.env`, lalu restart server."
    return "Bot lokal aktif. Dashboard, Binance status, webhook, dan auto scalping tersedia."


def openai_reply(message: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return local_reply(message)
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
    instructions = os.getenv(
        "BOT_INSTRUCTIONS",
        "Anda adalah asisten lokal berbahasa Indonesia untuk membantu membuat bot trading dan analisis data. "
        "Jawab praktis, jelas, dan jangan menjanjikan profit pasti.",
    )
    context = recent_context()
    prompt = message if not context else f"Konteks percakapan terakhir:\n{context}\n\nPesan terbaru:\n{message}"
    payload = {"model": model, "instructions": instructions, "input": prompt}
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        return f"OpenAI API error {error.code}: {detail}"
    except Exception as error:
        return f"Gagal menghubungi OpenAI API: {error}"
    output_text = body.get("output_text")
    if output_text:
        return output_text
    chunks: list[str] = []
    for item in body.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"}:
                chunks.append(content.get("text", ""))
    return "\n".join(chunks).strip() or "Respons kosong dari OpenAI API."


class BotHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def end_headers(self) -> None:
        request_origin = self.headers.get("Origin", "")
        allowed_origins = {
            origin.strip()
            for origin in os.getenv(
                "CORS_ORIGINS",
                "http://127.0.0.1:3000,http://localhost:3000,http://127.0.0.1:3001,http://localhost:3001",
            ).split(",")
            if origin.strip()
        }
        if request_origin in allowed_origins:
            self.send_header("Access-Control-Allow-Origin", request_origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/trading-control":
            self.send_json({"ok": True, "control": trading_control_status()})
            return
        if parsed.path == "/api/binance/status":
            query = urllib.parse.parse_qs(parsed.query)
            symbol = query.get("symbol", [None])[0]
            client = BinanceClient(symbol=symbol)
            try:
                self.send_json({"ok": True, "binance": client.status()})
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, status=502)
            return
        if parsed.path == "/api/binance/klines":
            query = urllib.parse.parse_qs(parsed.query)
            symbol = query.get("symbol", [None])[0]
            interval = query.get("interval", ["1m"])[0]
            try:
                limit = int(query.get("limit", ["120"])[0])
            except ValueError:
                limit = 120
            client = BinanceClient(symbol=symbol)
            try:
                self.send_json({"ok": True, "market": client.klines(interval=interval, limit=limit)})
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, status=502)
            return
        if parsed.path == "/api/binance/symbols":
            client = BinanceClient()
            try:
                self.send_json({"ok": True, "market": client.market_symbols()})
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, status=502)
            return
        if parsed.path == "/api/scalping/symbols":
            try:
                self.send_json({"ok": True, "symbols": configured_scalping_symbols()})
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, status=502)
            return
        if parsed.path == "/api/auto-signal":
            query = urllib.parse.parse_qs(parsed.query)
            symbol = query.get("symbol", [os.getenv("TRADE_SYMBOL", "XAUUSDT")])[0]
            try:
                self.send_json({"ok": True, "signal": cached_auto_signal(normalize_symbol(symbol))})
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, status=502)
            return
        if parsed.path == "/api/bot/pnl":
            query = urllib.parse.parse_qs(parsed.query)
            symbol = normalize_symbol(query.get("symbol", [os.getenv("TRADE_SYMBOL", "XAUUSDT")])[0])
            client = BinanceClient(symbol=symbol)
            try:
                load_trade_memory()
                status = client.status()
                idr_rate = Decimal(str(status.get("balance_summary", {}).get("usdt_idr_rate", "0")))
                memory = TRADE_MEMORY.get(symbol, {})
                self.send_json(
                    {
                        "ok": True,
                        "symbol": symbol,
                        "summary": trade_pnl_summary(symbol, idr_rate),
                        "state": memory,
                        "usdt_idr_rate": str(idr_rate),
                    }
                )
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, status=502)
            return
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8")
        try:
            body = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
            self.send_json({"error": "JSON tidak valid"}, status=400)
            return
        if parsed.path == "/api/trading-control":
            enabled = body.get("auto_trade_enabled")
            if not isinstance(enabled, bool):
                self.send_json({"ok": False, "error": "auto_trade_enabled harus boolean."}, status=400)
                return
            write_env_value("AUTO_TRADE_ENABLED", "true" if enabled else "false")
            self.send_json({"ok": True, "control": trading_control_status()})
            return
        if parsed.path == "/api/position/take-profit":
            symbol = normalize_symbol(str(body.get("symbol", "")))
            if not symbol:
                self.send_json({"ok": False, "error": "symbol wajib diisi."}, status=400)
                return
            client = BinanceClient(symbol=symbol)
            try:
                positions = client.open_positions()
                position = next(
                    (
                        item
                        for item in positions
                        if str(item.get("symbol", "")).upper() == symbol
                        and Decimal(str(item.get("positionAmt", "0"))) != 0
                    ),
                    None,
                )
                if not position:
                    self.send_json({"ok": False, "error": f"Tidak ada posisi terbuka untuk {symbol}."}, status=404)
                    return
                pnl_usdt = Decimal(str(position.get("unRealizedProfit", position.get("unrealizedProfit", "0"))))
                pnl_percent = Decimal(str(position.get("pnlPercent", "0")))
                if pnl_usdt <= 0:
                    self.send_json(
                        {
                            "ok": False,
                            "error": f"Posisi {symbol} belum profit. PnL {pnl_usdt} USDT.",
                            "position": position,
                        },
                        status=400,
                    )
                    return
                position_amount = Decimal(str(position.get("positionAmt", "0")))
                result = client.close_position_amount(position_amount)
                self.send_json(
                    {
                        "ok": True,
                        "symbol": symbol,
                        "closed": True,
                        "pnl_usdt": str(pnl_usdt),
                        "pnl_percent": str(pnl_percent),
                        "result": result,
                    }
                )
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, status=400)
            return
        if parsed.path == "/api/tradingview/webhook":
            client = BinanceClient(symbol=str(body.get("symbol", os.getenv("TRADE_SYMBOL", "XAUUSDT"))))
            try:
                result = client.handle_webhook_signal(body)
                self.send_json({"ok": True, "result": result})
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, status=400)
            return
        if parsed.path == "/api/auto-signal/trade":
            if not env_bool("ALLOW_MANUAL_SIGNAL_TRADE", "false"):
                self.send_json(
                    {
                        "ok": False,
                        "error": "Manual signal trade nonaktif. Entry hanya dari AUTO_SCALPING bot atau TradingView webhook.",
                    },
                    status=403,
                )
                return
            symbol = normalize_symbol(str(body.get("symbol", os.getenv("TRADE_SYMBOL", "XAUUSDT"))))
            signal_info = cached_auto_signal(symbol, force_refresh=True)
            side = signal_info.get("signal")
            if not side:
                self.send_json({"ok": False, "error": signal_info.get("reason", "NO SIGNAL"), "signal": signal_info}, status=400)
                return
            trigger_ok, trigger_reason = score_trigger_ok(signal_info)
            if not trigger_ok:
                self.send_json({"ok": False, "error": trigger_reason, "signal": signal_info}, status=400)
                return
            client = BinanceClient(symbol=symbol)
            payload = {
                "symbol": symbol,
                "side": side,
                "usdt": str(effective_order_usdt(symbol, Decimal(str(body.get("usdt", os.getenv("ORDER_USDT", "5")))))),
                "leverage": str(body.get("leverage", os.getenv("DEFAULT_LEVERAGE", "3"))),
            }
            try:
                result = client.handle_webhook_signal(payload)
                self.send_json({"ok": True, "signal": signal_info, "result": result})
            except Exception as error:
                self.send_json({"ok": False, "error": str(error), "signal": signal_info}, status=400)
            return
        if parsed.path != "/api/chat":
            self.send_error(404, "Not found")
            return
        message = str(body.get("message", "")).strip()
        if not message:
            self.send_json({"error": "Pesan tidak boleh kosong"}, status=400)
            return
        append_memory("user", message)
        reply = openai_reply(message)
        append_memory("assistant", reply)
        self.send_json({"reply": reply})

    def send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class BinanceWebSocketManager:
    def __init__(self):
        self.loop = None
        self.public_ws = None
        self.private_ws = None
        self.listen_key = None
        self.symbols = set()
        self.running = False
        self.lock = threading.Lock()
        
        # Caches
        self.prices = {}
        self.klines = {}
        self.order_books = {}
        self.positions = None
        self.cached_status = None
        self.open_orders = {}

    def get_ws_base_url(self) -> str:
        client = BinanceClient()
        if client.is_futures():
            return "wss://fstream.binance.com" if client.mode == "live" else "wss://testnet.binancefuture.com"
        return "wss://stream.binance.com:9443" if client.mode == "live" else "wss://testnet.binance.vision"

    def fetch_listen_key(self) -> str:
        client = BinanceClient()
        url = f"{client.base_url}/fapi/v1/listenKey" if client.is_futures() else f"{client.base_url}/api/v3/listenKey"
        req = urllib.request.Request(url, method="POST")
        req.add_header("X-MBX-APIKEY", client.api_key)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["listenKey"]
        except Exception as err:
            print(f"[WS LISTEN KEY ERROR] Gagal mengambil listenKey: {err}")
            raise

    def keep_alive_listen_key(self, listen_key: str) -> None:
        client = BinanceClient()
        url = f"{client.base_url}/fapi/v1/listenKey" if client.is_futures() else f"{client.base_url}/api/v3/listenKey"
        url_with_param = f"{url}?listenKey={listen_key}"
        req = urllib.request.Request(url_with_param, method="PUT")
        req.add_header("X-MBX-APIKEY", client.api_key)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                pass
        except Exception as err:
            print(f"[WS LISTEN KEY ERROR] Gagal keep alive listenKey: {err}")

    def build_public_ws_url(self, symbols: list[str]) -> str:
        base = self.get_ws_base_url()
        scalping_tf = os.getenv("SCALPING_TIMEFRAME", "5m").strip().lower()
        trend_tf = os.getenv("TREND_TIMEFRAME", "15m").strip().lower()
        timeframes = {scalping_tf, trend_tf}
        
        streams = []
        for symbol in symbols:
            s_lower = symbol.lower()
            streams.append(f"{s_lower}@miniTicker")
            streams.append(f"{s_lower}@depth20@100ms")
            for tf in timeframes:
                streams.append(f"{s_lower}@kline_{tf}")
                
        streams_str = "/".join(streams)
        return f"{base}/stream?streams={streams_str}"

    async def start(self):
        self.running = True
        self.loop = asyncio.get_running_loop()
        
        # Load initial symbols
        initial_symbols = await asyncio.to_thread(configured_scalping_symbols)
        initial_set = set(initial_symbols)
        try:
            client = BinanceClient()
            if client.has_signed_credentials():
                positions = await asyncio.to_thread(client.open_positions_rest)
                for p in positions:
                    sym = p.get("symbol")
                    if sym:
                        initial_set.add(sym.upper())
        except Exception as e:
            print(f"[WS START WARNING] Gagal load open positions untuk initial symbols: {e}")
            
        with self.lock:
            self.symbols = initial_set
            
        tasks = [
            asyncio.create_task(self.public_ws_loop()),
            asyncio.create_task(self.private_ws_loop()),
            asyncio.create_task(self.keep_alive_loop()),
            asyncio.create_task(self.symbol_monitor_loop()),
            asyncio.create_task(self.private_polling_loop())
        ]
        
        await asyncio.gather(*tasks, return_exceptions=True)

    async def private_polling_loop(self):
        while self.running:
            try:
                await asyncio.to_thread(self.refresh_private_data)
            except Exception as e:
                print(f"[WS PRIVATE POLLING ERROR] Gagal refresh: {e}")
            await asyncio.sleep(10)

    async def keep_alive_loop(self):
        while self.running:
            await asyncio.sleep(1800)  # 30 menit
            if self.listen_key:
                try:
                    await asyncio.to_thread(self.keep_alive_listen_key, self.listen_key)
                    print("[WS KEEP-ALIVE] ListenKey diperpanjang sukses.")
                except Exception as e:
                    print(f"[WS KEEP-ALIVE ERROR] Gagal keep alive: {e}")

    async def symbol_monitor_loop(self):
        while self.running:
            await asyncio.sleep(15)
            try:
                current_symbols = set(await asyncio.to_thread(configured_scalping_symbols))
                try:
                    client = BinanceClient()
                    if client.has_signed_credentials():
                        open_pos = self.get_cached_open_positions()
                        if open_pos is None:
                            open_pos = await asyncio.to_thread(client.open_positions_rest)
                        for p in open_pos:
                            sym = p.get("symbol")
                            if sym:
                                current_symbols.add(sym.upper())
                except Exception as ex:
                    print(f"[WS MONITOR WARNING] Gagal ambil open positions untuk dynamic symbols: {ex}")
                    
                if current_symbols and current_symbols != self.symbols:
                    print(f"[WS SYMBOLS CHANGED] Ganti symbols dari {self.symbols} ke {current_symbols}. Reconnecting WebSocket...")
                    with self.lock:
                        self.symbols = current_symbols
                    if self.public_ws:
                        await self.public_ws.close()
            except Exception as e:
                print(f"[WS MONITOR ERROR] {e}")

    async def public_ws_loop(self):
        while self.running:
            with self.lock:
                symbols = list(self.symbols)
            if not symbols:
                await asyncio.sleep(2)
                continue
                
            url = self.build_public_ws_url(symbols)
            print(f"[WS PUBLIC] Koneksi ke combined streams: {url}")
            try:
                async with websockets.connect(url) as ws:
                    self.public_ws = ws
                    print("[WS PUBLIC] WebSocket Combined Terkoneksi!")
                    async for raw_msg in ws:
                        msg = json.loads(raw_msg)
                        stream = msg.get("stream", "")
                        data = msg.get("data", {})
                        event_type = data.get("e", "")
                        symbol = data.get("s", "").upper()
                        
                        if event_type == "kline":
                            kline_data = data.get("k", {})
                            self.handle_ws_kline(symbol, kline_data)
                        elif event_type == "24hrMiniTicker":
                            close_price = data.get("c", "0")
                            with self.lock:
                                self.prices[symbol] = Decimal(str(close_price))
                        elif "depth" in stream:
                            self.process_ws_depth(symbol, data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[WS PUBLIC ERROR] Koneksi terputus: {e}. Menghubungkan ulang dalam 5 detik...")
                self.public_ws = None
                await asyncio.sleep(5)

    def handle_ws_kline(self, symbol: str, kline_data: dict):
        interval = kline_data["i"]
        open_time = kline_data["t"]
        candle = {
            "open_time": open_time,
            "open": kline_data["o"],
            "high": kline_data["h"],
            "low": kline_data["l"],
            "close": kline_data["c"],
            "volume": kline_data["v"],
            "close_time": kline_data["T"]
        }
        
        key = (symbol, interval)
        with self.lock:
            if key not in self.klines:
                self.klines[key] = []
                threading.Thread(target=self.load_kline_history_sync, args=(symbol, interval), daemon=True).start()
                return
                
            history = self.klines[key]
            if not history:
                history.append(candle)
            elif history[-1]["open_time"] == open_time:
                history[-1] = candle
            elif open_time > history[-1]["open_time"]:
                history.append(candle)
                if len(history) > 500:
                    history.pop(0)

    def load_kline_history_sync(self, symbol: str, interval: str):
        key = (symbol, interval)
        try:
            client = BinanceClient(symbol=symbol)
            market = client.klines_rest(interval=interval, limit=200)
            candles = market["candles"]
            with self.lock:
                self.klines[key] = candles
            print(f"[WS KLINES] Sukses pre-populate history {symbol} {interval} ({len(candles)} candles).")
        except Exception as e:
            print(f"[WS KLINES ERROR] Gagal pre-populate history {symbol} {interval}: {e}")

    def process_ws_depth(self, symbol: str, data: dict):
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        if not bids or not asks:
            return
            
        try:
            bid_notional = sum(Decimal(str(price)) * Decimal(str(qty)) for price, qty in bids)
            ask_notional = sum(Decimal(str(price)) * Decimal(str(qty)) for price, qty in asks)
            total = bid_notional + ask_notional
            bid_ratio = (bid_notional / total) if total > 0 else Decimal("0.5")
            ask_ratio = (ask_notional / total) if total > 0 else Decimal("0.5")
            best_bid = Decimal(str(bids[0][0])) if bids else Decimal("0")
            best_ask = Decimal(str(asks[0][0])) if asks else Decimal("0")
            spread_percent = Decimal("0")
            if best_bid > 0 and best_ask > 0:
                spread_percent = ((best_ask - best_bid) / best_bid) * Decimal("100")
                
            book = {
                "symbol": symbol,
                "limit": len(bids),
                "best_bid": str(best_bid),
                "best_ask": str(best_ask),
                "bid_notional": str(bid_notional),
                "ask_notional": str(ask_notional),
                "bid_ratio": str(bid_ratio),
                "ask_ratio": str(ask_ratio),
                "spread_percent": str(spread_percent),
            }
            with self.lock:
                self.order_books[symbol] = book
        except Exception as e:
            print(f"[WS DEPTH ERROR] {symbol}: {e}")

    async def private_ws_loop(self):
        while self.running:
            client = BinanceClient()
            if not client.has_signed_credentials():
                await asyncio.sleep(5)
                continue
                
            try:
                listen_key = await asyncio.to_thread(self.fetch_listen_key)
                self.listen_key = listen_key
                
                base = self.get_ws_base_url()
                url = f"{base}/ws/{listen_key}"
                print(f"[WS PRIVATE] Koneksi ke User Data Stream: {url}")
                
                await asyncio.to_thread(self.init_snapshots)
                
                async with websockets.connect(url) as ws:
                    self.private_ws = ws
                    print("[WS PRIVATE] User Data Stream WebSocket Terkoneksi!")
                    async for raw_msg in ws:
                        msg = json.loads(raw_msg)
                        event_type = msg.get("e", "")
                        print(f"[WS PRIVATE EVENT] Event {event_type} masuk.")
                        if event_type in {"ACCOUNT_UPDATE", "ORDER_TRADE_UPDATE", "MARGIN_CALL"}:
                            asyncio.create_task(self.async_refresh_private_data())
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[WS PRIVATE ERROR] Koneksi private terputus: {e}. Hubungkan ulang dalam 5 detik...")
                self.private_ws = None
                self.listen_key = None
                await asyncio.sleep(5)

    def init_snapshots(self):
        try:
            client = BinanceClient()
            if client.has_signed_credentials():
                # Initial positions
                positions = client.open_positions_rest()
                with self.lock:
                    self.positions = positions
                
                # Initial status
                status = client.status_rest()
                with self.lock:
                    self.cached_status = status
                    
                # Initial orders
                with self.lock:
                    symbols = list(self.symbols)
                for symbol in symbols:
                    orders = client.open_orders_rest(symbol)
                    with self.lock:
                        self.open_orders[symbol] = orders
            print("[WS INIT] Sukses mengambil snapshots awal.")
        except Exception as e:
            print(f"[WS INIT ERROR] Gagal load initial snapshot: {e}")

    def refresh_private_data(self):
        try:
            client = BinanceClient()
            if client.has_signed_credentials():
                positions = client.open_positions_rest()
                with self.lock:
                    self.positions = positions
                
                status = client.status_rest()
                with self.lock:
                    self.cached_status = status
                    
                with self.lock:
                    symbols = list(self.symbols)
                for symbol in symbols:
                    orders = client.open_orders_rest(symbol)
                    with self.lock:
                        self.open_orders[symbol] = orders
                print("[WS PRIVATE REFRESH] Data internal di-refresh dengan sukses.")
        except Exception as e:
            print(f"[WS PRIVATE REFRESH ERROR] Gagal refresh data: {e}")

    async def async_refresh_private_data(self):
        await asyncio.sleep(0.5)
        await asyncio.to_thread(self.refresh_private_data)

    def get_cached_price(self, symbol: str) -> Decimal | None:
        with self.lock:
            val = self.prices.get(symbol.upper())
            return Decimal(str(val)) if val is not None else None

    def get_cached_klines(self, symbol: str, interval: str, limit: int) -> dict | None:
        key = (symbol.upper(), interval)
        with self.lock:
            if key not in self.klines or not self.klines[key]:
                return None
            candles = list(self.klines[key])
        return {"symbol": symbol, "interval": interval, "candles": candles[-limit:]}

    def get_cached_order_book(self, symbol: str) -> dict | None:
        with self.lock:
            return self.order_books.get(symbol.upper())

    def get_cached_open_positions(self) -> list[dict] | None:
        with self.lock:
            return list(self.positions) if self.positions is not None else None

    def get_cached_open_orders(self, symbol: str) -> list[dict] | None:
        with self.lock:
            orders = self.open_orders.get(symbol.upper())
            return list(orders) if orders is not None else []

    def get_cached_status(self) -> dict | None:
        with self.lock:
            return dict(self.cached_status) if self.cached_status is not None else None


def run_ws_manager():
    global BINANCE_WS_MANAGER
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(BINANCE_WS_MANAGER.start())


def start_ws_manager() -> None:
    global BINANCE_WS_MANAGER
    if BINANCE_WS_MANAGER is None:
        BINANCE_WS_MANAGER = BinanceWebSocketManager()
    if not BINANCE_WS_MANAGER.running:
        print("[WS MANAGER] Memulai WebSocket manager di background thread...")
        thread = threading.Thread(target=run_ws_manager, daemon=True)
        thread.start()
        time.sleep(1.5)  # Beri jeda agar WebSocket siap dan snapshot awal ter-load


def main() -> None:
    load_dotenv()
    start_ws_manager()
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8765"))
    if auto_scalping_enabled():
        print("=" * 70)
        print("AUTO SCALPING ACTIVE")
        print("AUTO_TRADE_ENABLED =", os.getenv("AUTO_TRADE_ENABLED", "false"))
        print("DRY_RUN =", os.getenv("DRY_RUN", "true"))
        print("=" * 70)
        thread = threading.Thread(target=auto_scalping_loop, daemon=True)
        thread.start()
    server = ThreadingHTTPServer((host, port), BotHandler)
    print(f"Bot lokal jalan di http://{host}:{port}")
    print("Tekan Ctrl+C untuk berhenti.")
    server.serve_forever()


if __name__ == "__main__":
    main()

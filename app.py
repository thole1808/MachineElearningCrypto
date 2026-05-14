from __future__ import annotations

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

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
DATA_DIR = ROOT / "data"
MEMORY_FILE = DATA_DIR / "conversations.jsonl"


def env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def auto_scalping_enabled() -> bool:
    return env_bool("AUTO_SCALPING", "false")


class BinanceClient:
    def __init__(self, symbol: str | None = None) -> None:
        self.mode = os.getenv("BINANCE_MODE", "testnet").strip().lower()
        self.symbol = (symbol or os.getenv("TRADE_SYMBOL", "XRPUSDT")).strip().upper()
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

    def klines(self, interval: str = "1m", limit: int = 120) -> dict:
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

    def get_price(self) -> Decimal:
        path = "/fapi/v1/ticker/price" if self.is_futures() else "/api/v3/ticker/price"
        payload = self.public_get(path, {"symbol": self.symbol})
        return Decimal(str(payload["price"]))

    def asset_usdt_price(self, asset: str) -> Decimal:
        clean_asset = asset.strip().upper()
        if clean_asset in {"USDT", "FDUSD", "USDC"}:
            return Decimal("1")
        symbol = f"{clean_asset}USDT"
        try:
            payload = self.public_get("/fapi/v1/ticker/price" if self.is_futures() else "/api/v3/ticker/price", {"symbol": symbol})
        except Exception:
            payload = self.public_get_url("https://api.binance.com", "/api/v3/ticker/price", {"symbol": symbol})
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

    def round_quantity(self, quantity: Decimal) -> str:
        step = self.quantity_step()
        rounded = (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step
        return format(rounded.normalize(), "f")

    def round_price(self, price: Decimal) -> str:
        tick = self.price_tick()
        rounded = (price / tick).to_integral_value(rounding=ROUND_DOWN) * tick
        return format(rounded.normalize(), "f")

    def calculate_quantity(self, usdt_amount: Decimal | None = None) -> str:
        amount = usdt_amount or Decimal(os.getenv("ORDER_USDT", "5"))
        leverage = Decimal(os.getenv("DEFAULT_LEVERAGE", "3"))
        notional = amount * leverage
        quantity = notional / self.get_price()
        rounded = self.round_quantity(quantity)
        if Decimal(rounded) <= 0:
            raise ValueError("Quantity menjadi 0. Naikkan ORDER_USDT atau pilih symbol lain.")
        return rounded

    def status(self) -> dict:
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
            account = self.signed_get("/fapi/v2/account" if self.is_futures() else "/api/v3/account")
            if self.is_futures():
                balances = [
                    asset
                    for asset in account.get("assets", [])
                    if float(asset.get("walletBalance", "0")) > 0 or float(asset.get("availableBalance", "0")) > 0
                ]
                result["balances"] = balances
                result["balance_summary"] = self.futures_balance_summary(balances)
                result["open_positions"] = self.open_positions()
            else:
                result["balances"] = [
                    bal
                    for bal in account.get("balances", [])
                    if float(bal.get("free", "0")) > 0 or float(bal.get("locked", "0")) > 0
                ]
        return result

    def open_positions(self) -> list[dict]:
        if self.dry_run():
            return []
        rows = self.signed_get("/fapi/v2/positionRisk")
        positions = [pos for pos in rows if abs(float(pos.get("positionAmt", "0"))) > 0]
        for position in positions:
            entry = Decimal(str(position.get("entryPrice", "0")))
            mark = Decimal(str(position.get("markPrice", "0")))
            amount = Decimal(str(position.get("positionAmt", "0")))
            if entry > 0 and mark > 0:
                if amount > 0:
                    pnl_percent = ((mark - entry) / entry) * Decimal("100")
                    position["tpHitDirection"] = "mark >= TP" if mark >= entry else "mark < TP"
                else:
                    pnl_percent = ((entry - mark) / entry) * Decimal("100")
                    position["tpHitDirection"] = "mark <= TP" if mark <= entry else "mark > TP"
                position["pnlPercent"] = str(pnl_percent)
        return positions

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

    def place_tp_sl_orders(self, entry_side: str, entry_price: Decimal) -> dict:
        tp_percent = Decimal(os.getenv("TP_PERCENT", os.getenv("TAKE_PROFIT_PERCENT", "1")))
        sl_percent = Decimal(os.getenv("SL_PERCENT", os.getenv("STOP_LOSS_PERCENT", "0.5")))
        if tp_percent <= 0 or sl_percent <= 0:
            return {"skipped": True, "reason": "TP_PERCENT / SL_PERCENT tidak aktif."}
        entry_side = entry_side.upper()
        if entry_side == "BUY":
            close_side = "SELL"
            tp_price = entry_price * (Decimal("1") + tp_percent / Decimal("100"))
            sl_price = entry_price * (Decimal("1") - sl_percent / Decimal("100"))
        elif entry_side == "SELL":
            close_side = "BUY"
            tp_price = entry_price * (Decimal("1") - tp_percent / Decimal("100"))
            sl_price = entry_price * (Decimal("1") + sl_percent / Decimal("100"))
        else:
            raise ValueError("entry_side harus BUY atau SELL.")
        tp_params = {
            "symbol": self.symbol,
            "side": close_side,
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": self.round_price(tp_price),
            "closePosition": "true",
            "workingType": "MARK_PRICE",
        }
        sl_params = {
            "symbol": self.symbol,
            "side": close_side,
            "type": "STOP_MARKET",
            "stopPrice": self.round_price(sl_price),
            "closePosition": "true",
            "workingType": "MARK_PRICE",
        }
        resolved_position_side = self.order_position_side(entry_side)
        if resolved_position_side:
            tp_params["positionSide"] = resolved_position_side
            sl_params["positionSide"] = resolved_position_side
        if self.dry_run():
            return {"dry_run": True, "tp_order": tp_params, "sl_order": sl_params}
        tp_result = self.signed_post("/fapi/v1/order", tp_params)
        sl_result = self.signed_post("/fapi/v1/order", sl_params)
        return {"tp_order": tp_result, "sl_order": sl_result}

    def close_position_market(self, position_side: str) -> dict:
        clean = position_side.strip().upper()
        if clean == "LONG":
            return self.place_futures_market_order("SELL", reduce_only=True, position_side="LONG")
        if clean == "SHORT":
            return self.place_futures_market_order("BUY", reduce_only=True, position_side="SHORT")
        raise ValueError("position_side harus LONG atau SHORT.")

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
        incoming_symbol = str(payload.get("symbol", self.symbol)).strip().upper().replace(".P", "")
        self.symbol = incoming_symbol or self.symbol
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
        usdt_amount = Decimal(str(payload.get("usdt", os.getenv("ORDER_USDT", "5"))))
        quantity = self.calculate_quantity(usdt_amount)
        leverage_result = self.set_leverage(leverage)
        entry_price = self.get_price()
        order_result = self.place_futures_market_order(side, quantity=quantity)
        tp_sl_result = self.place_tp_sl_orders(side, entry_price)
        return {
            "accepted": True,
            "dry_run": self.dry_run(),
            "symbol": self.symbol,
            "side": side,
            "usdt_margin": str(usdt_amount),
            "leverage": leverage,
            "quantity": quantity,
            "entry_price_reference": str(entry_price),
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


def volume_ok(volumes: list[Decimal]) -> bool:
    if len(volumes) < 21:
        return True
    last_volume = volumes[-1]
    avg_volume = sum(volumes[-21:-1]) / Decimal("20")
    multiplier = Decimal(os.getenv("VOLUME_MULTIPLIER", "1"))
    return last_volume >= avg_volume * multiplier


def generate_auto_signal(symbol: str) -> dict:
    interval = os.getenv("SCALPING_TIMEFRAME", "5m")
    limit = int(os.getenv("SCALPING_KLINE_LIMIT", "120"))
    client = BinanceClient(symbol=symbol)
    market = client.klines(interval=interval, limit=limit)
    candles = market["candles"]
    closes = to_decimal_list([item["close"] for item in candles])
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
    buy_rsi = Decimal(os.getenv("BUY_RSI", "55"))
    sell_rsi = Decimal(os.getenv("SELL_RSI", "45"))
    has_volume = volume_ok(volumes)
    signal = None
    reason = "NO SIGNAL"
    if fast_prev <= slow_prev and fast_now > slow_now and rsi_now >= buy_rsi and has_volume:
        signal = "BUY"
        reason = "EMA fast cross up + RSI kuat"
    elif fast_prev >= slow_prev and fast_now < slow_now and rsi_now <= sell_rsi and has_volume:
        signal = "SELL"
        reason = "EMA fast cross down + RSI lemah"
    elif env_bool("FAST_SIGNAL_MODE", "false") and has_volume:
        if fast_now > slow_now and rsi_now >= buy_rsi:
            signal = "BUY"
            reason = "FAST MODE: trend EMA naik + RSI cukup kuat"
        elif fast_now < slow_now and rsi_now <= sell_rsi:
            signal = "SELL"
            reason = "FAST MODE: trend EMA turun + RSI cukup lemah"
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


def auto_scalping_loop() -> None:
    symbols = [s.strip().upper() for s in os.getenv("SCALPING_SYMBOLS", "XRPUSDT,SOLUSDT,DOGEUSDT").split(",") if s.strip()]
    interval_seconds = int(os.getenv("SCALPING_INTERVAL", "60"))
    print("=" * 70)
    print("AUTO SCALPING LOOP START")
    print("Symbols:", ", ".join(symbols))
    print("Interval:", interval_seconds, "seconds")
    print("=" * 70)
    while True:
        try:
            for symbol in symbols:
                try:
                    signal_info = generate_auto_signal(symbol)
                    print(
                        f"[SCAN] {symbol} | signal={signal_info['signal']} | "
                        f"rsi={signal_info['rsi']} | reason={signal_info['reason']}"
                    )
                    if not signal_info["signal"]:
                        continue
                    client = BinanceClient(symbol=symbol)
                    if env_bool("PREVENT_DOUBLE_POSITION", "true") and client.has_open_position():
                        print(f"[SKIP] {symbol} masih punya posisi terbuka.")
                        continue
                    payload = {
                        "symbol": symbol,
                        "side": signal_info["signal"],
                        "usdt": os.getenv("ORDER_USDT", "5"),
                        "leverage": os.getenv("DEFAULT_LEVERAGE", "3"),
                    }
                    print(f"[AUTO ENTRY] {payload}")
                    result = client.handle_webhook_signal(payload)
                    print("[AUTO RESULT]", json.dumps(result, ensure_ascii=False))
                except Exception as error:
                    print(f"[PAIR ERROR] {symbol}: {error}")
            time.sleep(interval_seconds)
        except Exception as error:
            print("[AUTO SCALPING ERROR]", error)
            time.sleep(10)


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
        if parsed.path == "/api/auto-signal":
            query = urllib.parse.parse_qs(parsed.query)
            symbol = query.get("symbol", [os.getenv("TRADE_SYMBOL", "XRPUSDT")])[0]
            try:
                self.send_json({"ok": True, "signal": generate_auto_signal(symbol.upper())})
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
        if parsed.path == "/api/tradingview/webhook":
            client = BinanceClient(symbol=str(body.get("symbol", os.getenv("TRADE_SYMBOL", "XRPUSDT"))))
            try:
                result = client.handle_webhook_signal(body)
                self.send_json({"ok": True, "result": result})
            except Exception as error:
                self.send_json({"ok": False, "error": str(error)}, status=400)
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
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    load_dotenv()
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

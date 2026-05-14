from __future__ import annotations

import json
import base64
import hashlib
import hmac
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
DATA_DIR = ROOT / "data"
MEMORY_FILE = DATA_DIR / "conversations.jsonl"


class BinanceClient:
    def __init__(self, symbol: str | None = None) -> None:
        self.mode = os.getenv("BINANCE_MODE", "testnet").strip().lower()
        self.symbol = (symbol or os.getenv("TRADE_SYMBOL", "BTCUSDT")).strip().upper()
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
        if self.mode == "live":
            return "https://api.binance.com"
        return "https://testnet.binance.vision"

    def public_get(self, path: str, params: dict[str, str] | None = None) -> dict:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"
        request = urllib.request.Request(url, method="GET")
        return self.fetch_json(request)

    def market_symbols(self) -> dict:
        info = self.public_get("/api/v3/exchangeInfo")
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
        return {
            "mode": self.mode,
            "count": len(symbols),
            "symbols": symbols,
        }

    def signed_get(self, path: str, params: dict[str, str] | None = None) -> dict:
        if not self.api_key:
            raise ValueError("BINANCE_API_KEY belum diisi.")

        payload = {
            **(params or {}),
            "timestamp": str(int(time.time() * 1000)),
            "recvWindow": "5000",
        }
        query = urllib.parse.urlencode(payload)
        signature = self.sign_query(query)
        signed_query = urllib.parse.urlencode({**payload, "signature": signature})
        url = f"{self.base_url}{path}?{signed_query}"
        request = urllib.request.Request(
            url,
            headers={"X-MBX-APIKEY": self.api_key},
            method="GET",
        )
        return self.fetch_json(request)

    def sign_query(self, query: str) -> str:
        if self.key_type == "rsa":
            return self.rsa_signature(query)

        if not self.api_secret:
            raise ValueError("BINANCE_API_SECRET belum diisi untuk key type HMAC.")
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature

    def rsa_signature(self, query: str) -> str:
        if not self.private_key_path.exists():
            raise ValueError(f"Private key RSA tidak ditemukan: {self.private_key_path}")

        command = [
            "openssl",
            "dgst",
            "-sha256",
            "-sign",
            str(self.private_key_path),
        ]
        result = subprocess.run(
            command,
            input=query.encode("utf-8"),
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Gagal membuat RSA signature dengan OpenSSL: {detail}")
        return base64.b64encode(result.stdout).decode("ascii")

    def fetch_json(self, request: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Binance API error {error.code}: {detail}") from error

    def status(self) -> dict:
        result = {
            "mode": self.mode,
            "symbol": self.symbol,
            "base_url": self.base_url,
            "key_type": self.key_type,
            "has_keys": self.has_signed_credentials(),
            "private_key_configured": self.key_type == "rsa" and self.private_key_path.exists(),
        }
        result["server_time"] = self.public_get("/api/v3/time")
        result["price"] = self.public_get("/api/v3/ticker/price", {"symbol": self.symbol})
        if self.has_signed_credentials():
            account = self.signed_get("/api/v3/account")
            result["balances"] = [
                balance
                for balance in account.get("balances", [])
                if float(balance.get("free", "0")) > 0 or float(balance.get("locked", "0")) > 0
            ]
        return result

    def klines(self, interval: str = "1m", limit: int = 120) -> dict:
        allowed_intervals = {
            "1m",
            "3m",
            "5m",
            "15m",
            "30m",
            "1h",
            "2h",
            "4h",
            "6h",
            "8h",
            "12h",
            "1d",
        }
        if interval not in allowed_intervals:
            raise ValueError("Interval candle tidak didukung.")
        safe_limit = max(10, min(limit, 500))
        rows = self.public_get(
            "/api/v3/klines",
            {
                "symbol": self.symbol,
                "interval": interval,
                "limit": str(safe_limit),
            },
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
        return {
            "symbol": self.symbol,
            "interval": interval,
            "candles": candles,
        }

    def has_signed_credentials(self) -> bool:
        if self.key_type == "rsa":
            return bool(self.api_key and self.private_key_path.exists())
        return bool(self.api_key and self.api_secret)


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
    event = {
        "ts": int(time.time()),
        "role": role,
        "content": content,
    }
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
            "Saya sudah bisa disiapkan untuk Binance. Untuk key RSA official, isi "
            "`BINANCE_KEY_TYPE=rsa`, `BINANCE_API_KEY`, dan `BINANCE_PRIVATE_KEY_PATH` di `.env`. "
            "Bot saat ini hanya membaca status/saldo; live order tetap belum diaktifkan sampai "
            "backtest dan batas risiko siap."
        )

    if any(word in text for word in ["xau", "gold", "emas", "trading", "signal"]):
        return (
            "Mode lokal aktif. Untuk bot XAU/USD, langkah aman berikutnya adalah: "
            "1. kumpulkan data OHLCV, 2. buat fitur teknikal, 3. backtest strategi, "
            "4. baru hubungkan ke broker/exchange. Saya belum akan memberi sinyal beli/jual "
            "langsung tanpa data, karena itu rawan menipu hasil."
        )

    if any(word in text for word in ["api", "openai", "chatgpt"]):
        return (
            "Bot lokal sudah siap. Agar responsnya memakai OpenAI, buat file `.env` dari "
            "`.env.example`, isi `OPENAI_API_KEY`, lalu restart server."
        )

    return (
        "Saya berjalan dalam mode lokal tanpa API key. Saya bisa menyimpan percakapan, "
        "menjawab dasar, dan menjadi kerangka untuk bot berikutnya. Untuk respons AI penuh, "
        "aktifkan `OPENAI_API_KEY` di `.env`."
    )


def openai_reply(message: str) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return local_reply(message)

    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
    instructions = os.getenv(
        "BOT_INSTRUCTIONS",
        "Anda adalah asisten lokal berbahasa Indonesia untuk membantu membuat bot, "
        "analisis data, dan eksperimen machine learning XAU/USD. Jawab praktis, jelas, "
        "dan jangan memberi nasihat finansial pasti.",
    )

    context = recent_context()
    prompt = message if not context else f"Konteks percakapan terakhir:\n{context}\n\nPesan terbaru:\n{message}"
    payload = {
        "model": model,
        "instructions": instructions,
        "input": prompt,
    }

    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        return f"OpenAI API error {error.code}: {detail}"
    except Exception as error:  # noqa: BLE001 - return the local runtime error to the UI.
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
            except Exception as error:  # noqa: BLE001 - surface setup/API problems to the local UI.
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
            except Exception as error:  # noqa: BLE001 - surface setup/API problems to the local UI.
                self.send_json({"ok": False, "error": str(error)}, status=502)
            return

        if parsed.path == "/api/binance/symbols":
            client = BinanceClient()
            try:
                self.send_json({"ok": True, "market": client.market_symbols()})
            except Exception as error:  # noqa: BLE001 - surface setup/API problems to the local UI.
                self.send_json({"ok": False, "error": str(error)}, status=502)
            return

        super().do_GET()

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self.send_error(404, "Not found")
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8")
        try:
            body = json.loads(raw_body)
        except json.JSONDecodeError:
            self.send_json({"error": "JSON tidak valid"}, status=400)
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
    server = ThreadingHTTPServer((host, port), BotHandler)
    print(f"Bot lokal jalan di http://{host}:{port}")
    print("Tekan Ctrl+C untuk berhenti.")
    server.serve_forever()


if __name__ == "__main__":
    main()

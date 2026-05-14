from __future__ import annotations

import json
import hashlib
import hmac
import os
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
    def __init__(self) -> None:
        self.mode = os.getenv("BINANCE_MODE", "testnet").strip().lower()
        self.symbol = os.getenv("TRADE_SYMBOL", "BTCUSDT").strip().upper()
        self.api_key = os.getenv("BINANCE_API_KEY", "").strip()
        self.api_secret = os.getenv("BINANCE_API_SECRET", "").strip()
        self.base_url = self.resolve_base_url()

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

    def signed_get(self, path: str, params: dict[str, str] | None = None) -> dict:
        if not self.api_key or not self.api_secret:
            raise ValueError("BINANCE_API_KEY dan BINANCE_API_SECRET belum diisi.")

        payload = {
            **(params or {}),
            "timestamp": str(int(time.time() * 1000)),
            "recvWindow": "5000",
        }
        query = urllib.parse.urlencode(payload)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        url = f"{self.base_url}{path}?{query}&signature={signature}"
        request = urllib.request.Request(
            url,
            headers={"X-MBX-APIKEY": self.api_key},
            method="GET",
        )
        return self.fetch_json(request)

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
            "has_keys": bool(self.api_key and self.api_secret),
        }
        result["server_time"] = self.public_get("/api/v3/time")
        result["price"] = self.public_get("/api/v3/ticker/price", {"symbol": self.symbol})
        if self.api_key and self.api_secret:
            account = self.signed_get("/api/v3/account")
            result["balances"] = [
                balance
                for balance in account.get("balances", [])
                if float(balance.get("free", "0")) > 0 or float(balance.get("locked", "0")) > 0
            ]
        return result


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
            "Saya sudah bisa disiapkan untuk Binance, tapi kunci aman dulu: gunakan Testnet. "
            "Isi `BINANCE_API_KEY` dan `BINANCE_API_SECRET` di `.env`, set `BINANCE_MODE=testnet`, "
            "lalu klik tombol Cek Binance di UI. Live trading baru kita aktifkan setelah backtest "
            "dan paper trading stabil."
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
        if self.path == "/api/binance/status":
            client = BinanceClient()
            try:
                self.send_json({"ok": True, "binance": client.status()})
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

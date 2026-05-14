# Panduan Auto Buy/Sell dari Laptop Lokal

Bot ini sudah ditambah endpoint webhook:

```text
POST /api/tradingview/webhook
```

Alur:

```text
TradingView Alert -> ngrok -> bot lokal Python -> Binance Futures
```

## 1. Setting API Binance

Di Binance API Management, aktifkan:

- Aktifkan Pembacaan
- Aktifkan Futures

Jangan aktifkan:

- Penarikan / Withdrawal

Simpan `private_key.pem` di root project. Jangan commit file ini.

## 2. Setting `.env`

Copy contoh env:

```bash
cp .env.example .env
```

Isi minimal:

```env
HOST=127.0.0.1
PORT=8765
BINANCE_MODE=live
BINANCE_MARKET_TYPE=futures
BINANCE_KEY_TYPE=rsa
BINANCE_API_KEY=isi_api_key_binance_anda
BINANCE_PRIVATE_KEY_PATH=private_key.pem
TRADE_SYMBOL=BTCUSDT
DEFAULT_LEVERAGE=3
ORDER_USDT=5
WEBHOOK_TOKEN=ganti-token-rahasia

# Awal wajib aman dulu
AUTO_TRADE_ENABLED=false
DRY_RUN=true
```

Arti mode aman:

- `AUTO_TRADE_ENABLED=false`: sinyal diterima, tapi order tidak dieksekusi.
- `DRY_RUN=true`: bot menampilkan rencana order, tapi tidak kirim order real.

Kalau sudah yakin baru ubah:

```env
AUTO_TRADE_ENABLED=true
DRY_RUN=false
```

## 3. Jalankan bot lokal

```bash
python3 app.py
```

Cek status:

```bash
curl http://127.0.0.1:8765/api/binance/status
```

## 4. Buka webhook lokal pakai ngrok

Install ngrok lalu jalankan:

```bash
ngrok http 8765
```

Ambil URL HTTPS dari ngrok, contoh:

```text
https://abc123.ngrok-free.app
```

Webhook URL di TradingView:

```text
https://abc123.ngrok-free.app/api/tradingview/webhook
```

## 5. Pesan Alert TradingView

Gunakan isi file `tradingview_alert_message.json`:

```json
{
  "token": "ganti-token-rahasia",
  "symbol": "{{ticker}}",
  "side": "{{strategy.order.action}}",
  "leverage": 3,
  "usdt": 5
}
```

Catatan: untuk Binance Futures biasanya symbol harus seperti `BTCUSDT`. Jika TradingView mengirim `BINANCE:BTCUSDT.P`, ganti manual alert menjadi:

```json
{
  "token": "ganti-token-rahasia",
  "symbol": "BTCUSDT",
  "side": "{{strategy.order.action}}",
  "leverage": 3,
  "usdt": 5
}
```

## 6. Test webhook tanpa order real

Pastikan `.env` masih:

```env
AUTO_TRADE_ENABLED=false
DRY_RUN=true
```

Test:

```bash
curl -X POST http://127.0.0.1:8765/api/tradingview/webhook \
  -H "Content-Type: application/json" \
  -d '{"token":"ganti-token-rahasia","symbol":"BTCUSDT","side":"BUY","leverage":3,"usdt":5}'
```

Kalau sudah ada response JSON, webhook jalan.

## 7. Mode order real

Gunakan hanya kalau sudah paham risikonya:

```env
AUTO_TRADE_ENABLED=true
DRY_RUN=false
```

Restart bot:

```bash
python3 app.py
```

## Aturan aman modal 100K

- Futures isolated
- Leverage maksimal 3x
- `ORDER_USDT=5` dulu untuk test
- Jangan aktifkan withdrawal
- Jangan share `private_key.pem`, `.env`, API key, atau screenshot yang menampilkan key
- Tidak ada bot yang menjamin profit

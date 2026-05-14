# Local Bot XAU/USD

Bot lokal sederhana untuk melanjutkan eksperimen ChatGPT di laptop. App ini bisa jalan tanpa dependency eksternal dan punya fallback lokal jika `OPENAI_API_KEY` belum diisi.

## Jalankan

```bash
python3 app.py
```

Buka:

```text
http://127.0.0.1:8765
```

## Pakai OpenAI API

```bash
cp .env.example .env
```

Isi `OPENAI_API_KEY` di `.env`, lalu restart server.

## Pakai Binance Testnet

Bot ini disiapkan untuk Binance Testnet lebih dulu. Jangan mulai dari live trading.

1. Buat API key di Binance Spot Testnet.
2. Salin `.env.example` menjadi `.env`.
3. Isi:

```env
BINANCE_MODE=testnet
BINANCE_API_KEY=isi_api_key_testnet
BINANCE_API_SECRET=isi_api_secret_testnet
TRADE_SYMBOL=BTCUSDT
```

4. Jalankan ulang server:

```bash
python3 app.py
```

5. Buka `http://127.0.0.1:8765`, lalu klik **Cek Binance**.

Live trading sengaja belum diaktifkan. Tahap aman berikutnya adalah backtest, paper trading, pembatas risiko, lalu modal kecil jika semua stabil.

Jika muncul error `CERTIFICATE_VERIFY_FAILED` di macOS, jalankan installer sertifikat Python dari Finder atau Terminal. Umumnya ada di:

```text
/Applications/Python 3.12/Install Certificates.command
```

## Struktur

- `app.py` - server lokal dan endpoint chat.
- `web/` - UI chat di browser.
- `data/conversations.jsonl` - riwayat chat lokal, dibuat otomatis saat dipakai.

## Catatan

Bot ini belum memberi sinyal trading otomatis. Fondasi ini disiapkan dulu supaya nanti bisa ditambah modul data XAU/USD, fitur teknikal, backtest, model machine learning, dan integrasi broker secara bertahap.

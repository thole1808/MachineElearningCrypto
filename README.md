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

## Dashboard Next.js

Dashboard web berada di folder `dashboard/`.

```bash
cd dashboard
npm install
cp .env.local.example .env.local
npm run dev
```

Buka:

```text
http://127.0.0.1:3000
```

Pastikan backend Python tetap berjalan di terminal lain:

```bash
python3 app.py
```

## Pakai OpenAI API

```bash
cp .env.example .env
```

Isi `OPENAI_API_KEY` di `.env`, lalu restart server.

## Pakai Binance Testnet

Bot ini disiapkan untuk Binance Testnet lebih dulu. Jangan mulai dari live trading.

1. Buka `https://testnet.binance.vision/`.
2. Login dengan akun yang diminta oleh Binance Testnet.
3. Buat API key testnet dari menu API Key.
4. Simpan API Key dan Secret Key sekali itu saja. Jangan kirim ke chat dan jangan commit ke git.
5. Salin `.env.example` menjadi `.env`.
6. Isi:

```env
BINANCE_MODE=testnet
BINANCE_KEY_TYPE=hmac
BINANCE_API_KEY=isi_api_key_testnet
BINANCE_API_SECRET=isi_api_secret_testnet
TRADE_SYMBOL=BTCUSDT
```

7. Jalankan ulang server:

```bash
python3 app.py
```

8. Buka `http://127.0.0.1:8765`, lalu klik **Cek Binance**.

Live trading sengaja belum diaktifkan. Tahap aman berikutnya adalah backtest, paper trading, pembatas risiko, lalu modal kecil jika semua stabil.

## Pakai Binance Official RSA

Jika API key dibuat langsung di Binance official dengan tipe RSA:

1. Simpan private key lokal sebagai `private_key.pem` di root project.
2. Jangan commit `private_key.pem`; file `.pem` sudah di-ignore.
3. Isi `.env`:

```env
BINANCE_MODE=live
BINANCE_KEY_TYPE=rsa
BINANCE_API_KEY=isi_api_key_baru_anda
BINANCE_PRIVATE_KEY_PATH=private_key.pem
TRADE_SYMBOL=BTCUSDT
```

Bot saat ini hanya mengecek koneksi, harga, dan saldo. Fitur order live sengaja belum dibuat sampai strategi, backtest, dan batas risiko siap.

Untuk keamanan API key official:

- Jangan aktifkan withdrawal.
- Matikan permission yang belum dipakai, misalnya Futures jika belum dipakai.
- Gunakan IP restriction jika memungkinkan.
- Revoke dan buat ulang API key jika pernah terlihat di chat, screenshot, livestream, atau GitHub.

Jika muncul error `CERTIFICATE_VERIFY_FAILED` di macOS, jalankan installer sertifikat Python dari Finder atau Terminal. Umumnya ada di:

```text
/Applications/Python 3.12/Install Certificates.command
```

## Struktur

- `app.py` - server lokal dan endpoint chat.
- `web/` - UI chat di browser.
- `dashboard/` - dashboard monitoring berbasis Next.js.
- `data/conversations.jsonl` - riwayat chat lokal, dibuat otomatis saat dipakai.

## Catatan

Bot ini belum memberi sinyal trading otomatis. Fondasi ini disiapkan dulu supaya nanti bisa ditambah modul data XAU/USD, fitur teknikal, backtest, model machine learning, dan integrasi broker secara bertahap.

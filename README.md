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

## Struktur

- `app.py` - server lokal dan endpoint chat.
- `web/` - UI chat di browser.
- `data/conversations.jsonl` - riwayat chat lokal, dibuat otomatis saat dipakai.

## Catatan

Bot ini belum memberi sinyal trading otomatis. Fondasi ini disiapkan dulu supaya nanti bisa ditambah modul data XAU/USD, fitur teknikal, backtest, model machine learning, dan integrasi broker secara bertahap.

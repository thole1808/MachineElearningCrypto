"use client";

import { useEffect, useMemo, useState } from "react";

type Balance = {
  asset: string;
  free: string;
  locked: string;
};

type BinanceStatus = {
  mode: string;
  symbol: string;
  base_url: string;
  key_type: string;
  has_keys: boolean;
  private_key_configured: boolean;
  server_time?: { serverTime: number };
  price?: { symbol: string; price: string };
  balances?: Balance[];
};

type ApiResponse = {
  ok: boolean;
  binance?: BinanceStatus;
  error?: string;
};

export default function Home() {
  const [status, setStatus] = useState<BinanceStatus | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [checked, setChecked] = useState(false);

  const serverTime = useMemo(() => {
    const value = status?.server_time?.serverTime;
    if (!value) return "-";
    return new Intl.DateTimeFormat("id-ID", {
      dateStyle: "medium",
      timeStyle: "medium",
    }).format(new Date(value));
  }, [status]);

  async function checkBinance() {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/binance/status");
      const body = (await response.json()) as ApiResponse;
      if (!response.ok || !body.ok || !body.binance) {
        throw new Error(body.error || "Backend belum bisa membaca status Binance.");
      }
      setStatus(body.binance);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Terjadi error tidak dikenal.");
    } finally {
      setChecked(true);
      setLoading(false);
    }
  }

  useEffect(() => {
    const timer = window.setTimeout(() => {
      checkBinance();
    }, 0);

    return () => window.clearTimeout(timer);
  }, []);

  const setupMessages = useMemo(() => {
    const messages: string[] = [];
    if (!status) return messages;
    if (status.mode !== "live") {
      messages.push("Mode masih testnet. Buat file .env di root project dan set BINANCE_MODE=live.");
    }
    if (status.key_type !== "rsa") {
      messages.push("Key type masih HMAC. Untuk API key Binance official RSA, set BINANCE_KEY_TYPE=rsa.");
    }
    if (!status.has_keys) {
      messages.push("API key belum terbaca. Isi BINANCE_API_KEY di file .env, lalu restart ./start-web.sh.");
    }
    if (!status.private_key_configured && status.key_type === "rsa") {
      messages.push("Private key RSA belum terdeteksi. Pastikan BINANCE_PRIVATE_KEY_PATH=private_key.pem.");
    }
    return messages;
  }, [status]);

  return (
    <main className="page">
      <section className="hero">
        <div>
          <p className="eyebrow">Local Binance Testnet Monitor</p>
          <h1>Machine Elearning Crypto</h1>
          <p className="subcopy">
            Pantau koneksi bot lokal, harga simbol, API key, private key RSA, dan saldo Binance.
          </p>
        </div>
        <button className="primary" disabled={loading} onClick={checkBinance}>
          {loading ? "Mengecek..." : "Cek Binance"}
        </button>
      </section>

      {error ? <section className="alert">{error}</section> : null}
      {!error && setupMessages.length ? (
        <section className="alert">
          <strong>Setup belum lengkap</strong>
          <ul>
            {setupMessages.map((message) => (
              <li key={message}>{message}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {!error && checked && status?.has_keys ? (
        <section className="success">Koneksi Binance terbaca. Bot masih mode monitor, belum mengeksekusi order.</section>
      ) : null}

      <section className="grid">
        <article className="metric">
          <span>Mode</span>
          <strong>{status?.mode || "Belum dicek"}</strong>
        </article>
        <article className="metric">
          <span>Symbol</span>
          <strong>{status?.symbol || "-"}</strong>
        </article>
        <article className="metric">
          <span>Harga</span>
          <strong>{status?.price?.price || "-"}</strong>
        </article>
        <article className="metric">
          <span>API Key</span>
          <strong>{status?.has_keys ? "Terpasang" : "Belum diisi"}</strong>
        </article>
        <article className="metric">
          <span>Key Type</span>
          <strong>{status?.key_type?.toUpperCase() || "-"}</strong>
        </article>
      </section>

      <section className="split">
        <article className="panel">
          <h2>Koneksi</h2>
          <dl>
            <div>
              <dt>Backend</dt>
              <dd>Next.js proxy ke Python backend</dd>
            </div>
            <div>
              <dt>Binance URL</dt>
              <dd>{status?.base_url || "-"}</dd>
            </div>
            <div>
              <dt>Server Time</dt>
              <dd>{serverTime}</dd>
            </div>
            <div>
              <dt>Private Key RSA</dt>
              <dd>{status?.private_key_configured ? "Terdeteksi lokal" : "Belum terdeteksi"}</dd>
            </div>
          </dl>
        </article>

        <article className="panel">
          <h2>Saldo Binance</h2>
          {status?.balances?.length ? (
            <div className="table">
              {status.balances.map((balance) => (
                <div className="row" key={balance.asset}>
                  <strong>{balance.asset}</strong>
                  <span>Free {balance.free}</span>
                  <span>Locked {balance.locked}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="empty">Belum ada saldo terbaca. Isi kredensial Binance lalu cek ulang.</p>
          )}
        </article>
      </section>
    </main>
  );
}

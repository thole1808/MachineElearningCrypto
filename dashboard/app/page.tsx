"use client";

import { useMemo, useState } from "react";

type Balance = {
  asset: string;
  free: string;
  locked: string;
};

type BinanceStatus = {
  mode: string;
  symbol: string;
  base_url: string;
  has_keys: boolean;
  server_time?: { serverTime: number };
  price?: { symbol: string; price: string };
  balances?: Balance[];
};

type ApiResponse = {
  ok: boolean;
  binance?: BinanceStatus;
  error?: string;
};

const botApiUrl =
  process.env.NEXT_PUBLIC_BOT_API_URL?.replace(/\/$/, "") || "http://127.0.0.1:8765";

export default function Home() {
  const [status, setStatus] = useState<BinanceStatus | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

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
      const response = await fetch(`${botApiUrl}/api/binance/status`);
      const body = (await response.json()) as ApiResponse;
      if (!response.ok || !body.ok || !body.binance) {
        throw new Error(body.error || "Backend belum bisa membaca status Binance.");
      }
      setStatus(body.binance);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Terjadi error tidak dikenal.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="page">
      <section className="hero">
        <div>
          <p className="eyebrow">Local Binance Testnet Monitor</p>
          <h1>Machine Elearning Crypto</h1>
          <p className="subcopy">
            Pantau koneksi bot lokal, harga simbol, dan saldo testnet dari dashboard Next.js.
          </p>
        </div>
        <button className="primary" disabled={loading} onClick={checkBinance}>
          {loading ? "Mengecek..." : "Cek Binance"}
        </button>
      </section>

      {error ? <section className="alert">{error}</section> : null}

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
      </section>

      <section className="split">
        <article className="panel">
          <h2>Koneksi</h2>
          <dl>
            <div>
              <dt>Backend</dt>
              <dd>{botApiUrl}</dd>
            </div>
            <div>
              <dt>Binance URL</dt>
              <dd>{status?.base_url || "-"}</dd>
            </div>
            <div>
              <dt>Server Time</dt>
              <dd>{serverTime}</dd>
            </div>
          </dl>
        </article>

        <article className="panel">
          <h2>Saldo Testnet</h2>
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
            <p className="empty">Belum ada saldo terbaca. Isi API key testnet lalu cek ulang.</p>
          )}
        </article>
      </section>
    </main>
  );
}

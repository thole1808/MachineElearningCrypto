"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

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

type Candle = {
  open_time: number;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  close_time: number;
};

type KlinesResponse = {
  ok: boolean;
  market?: {
    symbol: string;
    interval: string;
    candles: Candle[];
  };
  error?: string;
};

type MarketSymbol = {
  symbol: string;
  base_asset: string;
  quote_asset: string;
  status: string;
};

type SymbolsResponse = {
  ok: boolean;
  market?: {
    mode: string;
    count: number;
    symbols: MarketSymbol[];
  };
  error?: string;
};

type AutoSignal = {
  symbol: string;
  interval: string;
  signal: string | null;
  reason: string;
  close: string;
  ema_fast: string;
  ema_slow: string;
  rsi: string;
};

type AutoSignalResponse = {
  ok: boolean;
  signal?: AutoSignal;
  error?: string;
};

const intervals = ["1m", "5m", "15m", "1h"] as const;
const quoteFilters = ["USDT", "USDC", "BTC", "ETH", "BNB"] as const;

function formatPrice(value?: string) {
  if (!value) return "-";
  return Number(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatNumber(value?: string) {
  if (!value) return "-";
  return Number(value).toLocaleString("en-US", {
    maximumFractionDigits: 6,
  });
}

function formatClock(value?: number) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("id-ID", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function CandleChart({ candles }: { candles: Candle[] }) {
  const width = 980;
  const height = 360;
  const padding = { top: 18, right: 82, bottom: 28, left: 14 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const values = candles.flatMap((candle) => [Number(candle.high), Number(candle.low)]);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const xStep = plotWidth / Math.max(candles.length, 1);
  const bodyWidth = Math.max(3, Math.min(10, xStep * 0.62));

  const y = (price: number) => padding.top + ((max - price) / range) * plotHeight;
  const lastClose = Number(candles.at(-1)?.close || 0);
  const priceLines = [max, max - range * 0.25, max - range * 0.5, max - range * 0.75, min];

  if (!candles.length) {
    return <div className="chart-empty">Candle belum tersedia.</div>;
  }

  return (
    <svg className="chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Candlestick chart">
      <rect x="0" y="0" width={width} height={height} rx="8" />
      {priceLines.map((line) => (
        <g key={line}>
          <line x1={padding.left} x2={width - padding.right + 18} y1={y(line)} y2={y(line)} className="gridline" />
          <text x={width - padding.right + 28} y={y(line) + 4} className="axis-label">
            {formatPrice(String(line))}
          </text>
        </g>
      ))}
      {candles.map((candle, index) => {
        const open = Number(candle.open);
        const close = Number(candle.close);
        const high = Number(candle.high);
        const low = Number(candle.low);
        const x = padding.left + index * xStep + xStep / 2;
        const bodyTop = Math.min(y(open), y(close));
        const bodyHeight = Math.max(1, Math.abs(y(open) - y(close)));
        const isUp = close >= open;
        const isLast = index === candles.length - 1;

        return (
          <g key={`${candle.open_time}-${index}`} className={isUp ? "candle up" : "candle down"}>
            <line x1={x} x2={x} y1={y(high)} y2={y(low)} />
            <rect
              x={x - bodyWidth / 2}
              y={bodyTop}
              width={bodyWidth}
              height={bodyHeight}
              rx="1.5"
              className={isLast ? "running" : ""}
            />
          </g>
        );
      })}
      <line x1={padding.left} x2={width - padding.right + 18} y1={y(lastClose)} y2={y(lastClose)} className="last-price" />
      <text x={width - padding.right + 28} y={y(lastClose) + 4} className="last-label">
        {formatPrice(String(lastClose))}
      </text>
    </svg>
  );
}

export default function Home() {
  const [status, setStatus] = useState<BinanceStatus | null>(null);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [symbols, setSymbols] = useState<MarketSymbol[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState("BTCUSDT");
  const [search, setSearch] = useState("");
  const [quoteFilter, setQuoteFilter] = useState<(typeof quoteFilters)[number]>("USDT");
  const [interval, setIntervalValue] = useState<(typeof intervals)[number]>("1m");
  const [error, setError] = useState("");
  const [chartError, setChartError] = useState("");
  const [symbolsError, setSymbolsError] = useState("");
  const [signalError, setSignalError] = useState("");
  const [loading, setLoading] = useState(false);
  const [chartLoading, setChartLoading] = useState(false);
  const [checked, setChecked] = useState(false);
  const [autoSignal, setAutoSignal] = useState<AutoSignal | null>(null);

  const serverTime = useMemo(() => {
    const value = status?.server_time?.serverTime;
    if (!value) return "-";
    return new Intl.DateTimeFormat("id-ID", {
      dateStyle: "medium",
      timeStyle: "medium",
    }).format(new Date(value));
  }, [status]);

  const checkBinance = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`/api/binance/status?symbol=${selectedSymbol}`);
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
  }, [selectedSymbol]);

  const loadCandles = useCallback(async (nextInterval = interval) => {
    setChartLoading(true);
    setChartError("");
    try {
      const query = new URLSearchParams({
        symbol: selectedSymbol,
        interval: nextInterval,
        limit: "120",
      });
      const response = await fetch(`/api/binance/klines?${query.toString()}`);
      const body = (await response.json()) as KlinesResponse;
      if (!response.ok || !body.ok || !body.market) {
        throw new Error(body.error || "Backend belum bisa membaca candle Binance.");
      }
      setCandles(body.market.candles);
    } catch (caught) {
      setChartError(caught instanceof Error ? caught.message : "Terjadi error saat membaca candle.");
    } finally {
      setChartLoading(false);
    }
  }, [interval, selectedSymbol]);

  const loadSymbols = useCallback(async () => {
    setSymbolsError("");
    try {
      const response = await fetch("/api/binance/symbols");
      const body = (await response.json()) as SymbolsResponse;
      if (!response.ok || !body.ok || !body.market) {
        throw new Error(body.error || "Backend belum bisa membaca daftar pair Binance.");
      }
      setSymbols(body.market.symbols);
    } catch (caught) {
      setSymbolsError(caught instanceof Error ? caught.message : "Terjadi error saat membaca daftar pair.");
    }
  }, []);

  const loadAutoSignal = useCallback(async () => {
    setSignalError("");
    try {
      const response = await fetch(`/api/auto-signal?symbol=${selectedSymbol}`);
      const body = (await response.json()) as AutoSignalResponse;
      if (!response.ok || !body.ok || !body.signal) {
        throw new Error(body.error || "Backend belum bisa membaca signal Binance.");
      }
      setAutoSignal(body.signal);
    } catch (caught) {
      setSignalError(caught instanceof Error ? caught.message : "Terjadi error saat membaca signal.");
    }
  }, [selectedSymbol]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      checkBinance();
      loadCandles();
      loadSymbols();
      loadAutoSignal();
    }, 0);

    return () => window.clearTimeout(timer);
  }, [checkBinance, loadCandles, loadSymbols, loadAutoSignal]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      checkBinance();
      loadCandles(interval);
      loadAutoSignal();
    }, 5000);

    return () => window.clearInterval(timer);
  }, [checkBinance, interval, loadCandles, loadAutoSignal]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setCandles([]);
      checkBinance();
      loadCandles(interval);
      loadAutoSignal();
    }, 0);

    return () => window.clearTimeout(timer);
  }, [checkBinance, interval, loadCandles, selectedSymbol, loadAutoSignal]);

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

  const lastCandle = candles.at(-1);
  const candleDirection =
    lastCandle && Number(lastCandle.close) >= Number(lastCandle.open) ? "up" : "down";
  const visibleSymbols = useMemo(() => {
    const needle = search.trim().toUpperCase();
    return symbols
      .filter((item) => item.quote_asset === quoteFilter)
      .filter((item) => {
        if (!needle) return true;
        return item.symbol.includes(needle) || item.base_asset.includes(needle);
      })
      .slice(0, 80);
  }, [quoteFilter, search, symbols]);

  return (
    <main className="page">
      <section className="hero">
        <div>
          <p className="eyebrow">Local Binance Testnet Monitor</p>
          <h1>Machine Elearning Crypto</h1>
          <p className="subcopy">
            Pantau koneksi bot lokal, pilih pair Binance, baca candle berjalan, dan siapkan tools trading sendiri.
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

      <section className="market-panel">
        <header className="panel-head">
          <div>
            <h2>Market Scanner</h2>
            <p>Pilih pair Spot Binance. Chart dan harga otomatis mengikuti symbol pilihan.</p>
          </div>
          <input
            className="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Cari BTC, ETH, SOL..."
          />
        </header>
        {symbolsError ? <div className="alert compact">{symbolsError}</div> : null}
        <div className="toolbar left">
          {quoteFilters.map((quote) => (
            <button
              className={quote === quoteFilter ? "chip active" : "chip"}
              key={quote}
              type="button"
              onClick={() => setQuoteFilter(quote)}
            >
              {quote}
            </button>
          ))}
        </div>
        <div className="symbol-list">
          {visibleSymbols.map((item) => (
            <button
              className={item.symbol === selectedSymbol ? "symbol-button active" : "symbol-button"}
              key={item.symbol}
              type="button"
              onClick={() => setSelectedSymbol(item.symbol)}
            >
              <strong>{item.symbol}</strong>
              <span>
                {item.base_asset}/{item.quote_asset}
              </span>
            </button>
          ))}
        </div>
      </section>

      <section className="grid">
        <article className="metric">
          <span>Mode</span>
          <strong>{status?.mode || "Belum dicek"}</strong>
        </article>
        <article className="metric">
          <span>Symbol</span>
          <strong>{status?.symbol || selectedSymbol}</strong>
        </article>
        <article className="metric">
          <span>Harga</span>
          <strong className="price-value">{formatPrice(status?.price?.price)}</strong>
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

      <section className="chart-panel">
        <header className="panel-head">
          <div>
            <h2>Live Candle Chart</h2>
            <p>{status?.symbol || selectedSymbol} berjalan, refresh otomatis tiap 5 detik.</p>
          </div>
          <div className="toolbar">
            {intervals.map((item) => (
              <button
                className={item === interval ? "chip active" : "chip"}
                key={item}
                type="button"
                onClick={() => {
                  setIntervalValue(item);
                  loadCandles(item);
                }}
              >
                {item}
              </button>
            ))}
            <button className="chip" disabled={chartLoading} type="button" onClick={() => loadCandles()}>
              {chartLoading ? "Loading" : "Refresh"}
            </button>
          </div>
        </header>

        {chartError ? <div className="alert compact">{chartError}</div> : null}
        <CandleChart candles={candles} />

        <div className="candle-stats">
          <div>
            <span>Running Candle</span>
            <strong className={candleDirection}>{lastCandle ? candleDirection.toUpperCase() : "-"}</strong>
          </div>
          <div>
            <span>Open</span>
            <strong>{formatPrice(lastCandle?.open)}</strong>
          </div>
          <div>
            <span>High</span>
            <strong>{formatPrice(lastCandle?.high)}</strong>
          </div>
          <div>
            <span>Low</span>
            <strong>{formatPrice(lastCandle?.low)}</strong>
          </div>
          <div>
            <span>Close</span>
            <strong>{formatPrice(lastCandle?.close)}</strong>
          </div>
          <div>
            <span>Volume</span>
            <strong>{formatNumber(lastCandle?.volume)}</strong>
          </div>
          <div>
            <span>Update</span>
            <strong>{formatClock(lastCandle?.close_time)}</strong>
          </div>
        </div>
      </section>

      <section className="panel" style={{ marginTop: "24px", marginBottom: "24px" }}>
        <header className="panel-head">
          <div>
            <h2>Auto Signal Scanner (Real-time)</h2>
            <p>Sinyal scalping berjalan dari Python backend. Otomatis refresh tiap 5 detik.</p>
          </div>
        </header>
        {signalError ? <div className="alert compact">{signalError}</div> : null}
        <div className="candle-stats" style={{ marginTop: "16px" }}>
          <div>
            <span>Sinyal</span>
            <strong className={autoSignal?.signal === "BUY" ? "up" : autoSignal?.signal === "SELL" ? "down" : ""}>
              {autoSignal?.signal || "NO SIGNAL"}
            </strong>
          </div>
          <div>
            <span>Alasan</span>
            <strong>{autoSignal?.reason || "-"}</strong>
          </div>
          <div>
            <span>RSI</span>
            <strong>{autoSignal?.rsi || "-"}</strong>
          </div>
          <div>
            <span>EMA Fast</span>
            <strong>{formatPrice(autoSignal?.ema_fast)}</strong>
          </div>
          <div>
            <span>EMA Slow</span>
            <strong>{formatPrice(autoSignal?.ema_slow)}</strong>
          </div>
          <div>
            <span>Close</span>
            <strong>{formatPrice(autoSignal?.close)}</strong>
          </div>
        </div>
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
            <p className="empty">Tidak ada saldo spot non-zero yang terbaca.</p>
          )}
        </article>
      </section>
    </main>
  );
}

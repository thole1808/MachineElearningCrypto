"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type Balance = {
  asset: string;
  walletBalance?: string;
  availableBalance?: string;
  unrealizedProfit?: string;
  marginBalance?: string;
  maxWithdrawAmount?: string;
  walletUsdt?: string;
  availableUsdt?: string;
  marginUsdt?: string;
  unrealizedUsdt?: string;
  walletIdr?: string;
  availableIdr?: string;
  marginIdr?: string;
  unrealizedIdr?: string;
  free?: string;
  locked?: string;
};

type BalanceSummary = {
  wallet_usdt: string;
  available_usdt: string;
  margin_usdt: string;
  unrealized_usdt: string;
  usdt_idr_rate?: string | null;
  wallet_idr?: string;
  available_idr?: string;
  margin_idr?: string;
  unrealized_idr?: string;
};

type OpenPosition = {
  symbol: string;
  positionAmt: string;
  entryPrice: string;
  markPrice?: string;
  unRealizedProfit?: string;
  unrealizedProfit?: string;
  liquidationPrice?: string;
  leverage: string;
  marginType?: string;
  pnlPercent?: string;
  roePercent?: string;
  takeProfitPrice?: string;
  stopLossPrice?: string;
  positionSide?: string;
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
  balance_summary?: BalanceSummary;
  open_positions?: OpenPosition[];
};

type ApiResponse = {
  ok: boolean;
  binance?: BinanceStatus;
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

const defaultSymbol = "SOLUSDT";
const realtimeRefreshMs = 2000;
type BalanceCurrency = "USDT" | "IDR";

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

function formatSignedUsd(value?: string) {
  if (!value) return "-";
  const number = Number(value);
  const rounded = Math.round(number);
  return `${rounded > 0 ? "+" : ""}${rounded.toLocaleString("en-US", {
    maximumFractionDigits: 0,
  })} USDT`;
}

function formatPercent(value?: string) {
  if (!value) return "-";
  const number = Number(value);
  return `${number >= 0 ? "+" : ""}${number.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}%`;
}

function formatUsd(value?: string) {
  if (!value) return "-";
  return `${Math.round(Number(value)).toLocaleString("en-US", {
    maximumFractionDigits: 0,
  })} USDT`;
}

function formatIdr(value?: string) {
  if (!value) return "-";
  return Math.round(Number(value)).toLocaleString("id-ID", {
    style: "currency",
    currency: "IDR",
    maximumFractionDigits: 0,
  });
}

function roundedNumber(value?: string) {
  if (!value) return 0;
  return Math.round(Number(value));
}

function toneClass(value?: string) {
  const rounded = roundedNumber(value);
  if (rounded > 0) return "up";
  if (rounded < 0) return "down";
  return "";
}

export default function Home() {
  const [status, setStatus] = useState<BinanceStatus | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState(defaultSymbol);
  const [error, setError] = useState("");
  const [signalError, setSignalError] = useState("");
  const [loading, setLoading] = useState(false);
  const [checked, setChecked] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const [balanceCurrency, setBalanceCurrency] = useState<BalanceCurrency>("USDT");
  const [autoSignal, setAutoSignal] = useState<AutoSignal | null>(null);

  const serverTime = useMemo(() => {
    const value = status?.server_time?.serverTime;
    if (!value) return "-";
    return new Intl.DateTimeFormat("id-ID", {
      dateStyle: "medium",
      timeStyle: "medium",
    }).format(new Date(value));
  }, [status]);

  const lastUpdated = useMemo(() => {
    if (!lastUpdatedAt) return "-";
    return new Intl.DateTimeFormat("id-ID", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).format(new Date(lastUpdatedAt));
  }, [lastUpdatedAt]);

  const checkBinance = useCallback(async (manual = false) => {
    if (manual) setLoading(true);
    if (manual) setError("");
    try {
      const response = await fetch(`/api/binance/status?symbol=${selectedSymbol}`);
      const body = (await response.json()) as ApiResponse;
      if (!response.ok || !body.ok || !body.binance) {
        throw new Error(body.error || "Backend belum bisa membaca status Binance.");
      }
      setStatus(body.binance);
      setError("");
      setLastUpdatedAt(Date.now());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Terjadi error tidak dikenal.");
    } finally {
      setChecked(true);
      if (manual) setLoading(false);
    }
  }, [selectedSymbol]);

  const loadAutoSignal = useCallback(async () => {
    try {
      const response = await fetch(`/api/auto-signal?symbol=${selectedSymbol}`);
      const body = (await response.json()) as AutoSignalResponse;
      if (!response.ok || !body.ok || !body.signal) {
        throw new Error(body.error || "Backend belum bisa membaca signal Binance.");
      }
      setAutoSignal(body.signal);
      setSignalError("");
    } catch (caught) {
      setSignalError(caught instanceof Error ? caught.message : "Terjadi error saat membaca signal.");
    }
  }, [selectedSymbol]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      checkBinance();
      loadAutoSignal();
    }, realtimeRefreshMs);

    return () => window.clearInterval(timer);
  }, [checkBinance, loadAutoSignal]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      checkBinance();
      loadAutoSignal();
    }, 0);

    return () => window.clearTimeout(timer);
  }, [checkBinance, loadAutoSignal]);

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

  const openPositions = status?.open_positions || [];
  const futuresBalances = status?.balances || [];
  const balanceSummary = status?.balance_summary;
  const totalUnrealizedPnl = openPositions.reduce(
    (total, position) => total + Number(position.unRealizedProfit ?? position.unrealizedProfit ?? 0),
    0,
  );
  const showIdr = balanceCurrency === "IDR";
  const formatBalance = useCallback((usdtValue?: string, idrValue?: string) => {
    if (showIdr) return formatIdr(idrValue);
    return formatUsd(usdtValue);
  }, [showIdr]);
  const formatSignedBalance = useCallback((usdtValue?: string, idrValue?: string) => {
    if (showIdr) return formatIdr(idrValue);
    return formatSignedUsd(usdtValue);
  }, [showIdr]);
  return (
    <main className="terminal">
      <header className="topbar">
        <div>
          <p className="eyebrow">Futures Trading Console</p>
          <h1>Machine Elearning Crypto</h1>
        </div>
        <div className="topbar-actions">
          <span className={status?.has_keys ? "status-pill live" : "status-pill danger"}>
            {status?.has_keys ? "Binance Live" : "Disconnected"}
          </span>
          <span className="status-pill">Update {lastUpdated}</span>
          <button className="primary" disabled={loading} onClick={() => checkBinance(true)}>
            {loading ? "Syncing" : "Refresh"}
          </button>
        </div>
      </header>

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

      <section className="summary-grid">
        <article className="metric hero-metric">
          <span>Wallet ({balanceCurrency})</span>
          <strong>{formatBalance(balanceSummary?.wallet_usdt, balanceSummary?.wallet_idr)}</strong>
        </article>
        <article className="metric">
          <span>Available</span>
          <strong>{formatBalance(balanceSummary?.available_usdt, balanceSummary?.available_idr)}</strong>
        </article>
        <article className="metric">
          <span>Floating PnL</span>
          <strong className={totalUnrealizedPnl >= 0 ? "up" : "down"}>{formatSignedUsd(String(totalUnrealizedPnl))}</strong>
        </article>
        <article className="metric">
          <span>Active Positions</span>
          <strong>{openPositions.length}</strong>
        </article>
        <article className="metric">
          <span>{status?.symbol || selectedSymbol}</span>
          <strong className="price-value">{formatPrice(status?.price?.price)}</strong>
        </article>
      </section>

      <section className="workspace">
        <section className="panel positions-panel">
          <header className="panel-head">
            <div>
              <h2>Open Positions</h2>
              <p>Entry, mark, target TP/SL, ROE, dan liquidation realtime.</p>
            </div>
          </header>
          {openPositions.length ? (
            <div className="positions-table">
              <div className="positions-row positions-head">
                <span>Pair</span>
                <span>Side</span>
                <span>Qty</span>
                <span>Entry</span>
                <span>Mark</span>
                <span>PnL</span>
                <span>ROE</span>
                <span>TP</span>
                <span>SL</span>
                <span>Liq.</span>
              </div>
              {openPositions.map((position) => {
                const amount = Number(position.positionAmt);
                const pnlValue = position.unRealizedProfit ?? position.unrealizedProfit ?? "0";
                const pnl = Number(pnlValue);
                return (
                  <div className="positions-row" key={position.symbol}>
                    <strong>{position.symbol}</strong>
                    <span className={amount >= 0 ? "up" : "down"}>{position.positionSide && position.positionSide !== "BOTH" ? position.positionSide : amount >= 0 ? "LONG" : "SHORT"}</span>
                    <span>{formatNumber(position.positionAmt)}</span>
                    <span>{formatPrice(position.entryPrice)}</span>
                    <span>{formatPrice(position.markPrice)}</span>
                    <span className={pnl >= 0 ? "up" : "down"}>{formatSignedUsd(pnlValue)}</span>
                    <span className={pnl >= 0 ? "up" : "down"}>{formatPercent(position.roePercent ?? position.pnlPercent)}</span>
                    <span>{formatPrice(position.takeProfitPrice)}</span>
                    <span>{formatPrice(position.stopLossPrice)}</span>
                    <span>{formatPrice(position.liquidationPrice)}</span>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="empty">Belum ada posisi aktif.</p>
          )}
        </section>

        <section className="panel account-panel">
          <header className="panel-head balance-headline">
            <div>
              <h2>Account</h2>
              <p>USDT/IDR {formatIdr(balanceSummary?.usdt_idr_rate || undefined)}</p>
            </div>
            <div className="segmented" aria-label="Pilih mata uang saldo">
              {(["USDT", "IDR"] as const).map((currency) => (
                <button
                  className={balanceCurrency === currency ? "active" : ""}
                  key={currency}
                  type="button"
                  onClick={() => setBalanceCurrency(currency)}
                >
                  {currency}
                </button>
              ))}
            </div>
          </header>
          {futuresBalances.length ? (
            <div className="balance-table">
              <div className="balance-row balance-head">
                <span>Asset</span>
                <span>Wallet</span>
                <span>Avail.</span>
                <span>U-PnL</span>
              </div>
              {futuresBalances.map((balance) => {
                const unrealizedValue = balance.unrealizedUsdt || balance.unrealizedProfit || "0";
                return (
                  <div className="balance-row" key={balance.asset}>
                    <strong>{balance.asset}</strong>
                    <span>{formatBalance(balance.walletUsdt ?? balance.walletBalance ?? balance.free, balance.walletIdr)}</span>
                    <span>{formatBalance(balance.availableUsdt ?? balance.availableBalance ?? balance.free, balance.availableIdr)}</span>
                    <span className={toneClass(showIdr ? balance.unrealizedIdr : unrealizedValue)}>{formatSignedBalance(unrealizedValue, balance.unrealizedIdr)}</span>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="empty">Saldo Futures belum terbaca.</p>
          )}
        </section>

        <section className="panel signal-panel">
          <header className="panel-head">
            <div>
              <h2>Signal</h2>
              <p>Pair aktif refresh tiap {realtimeRefreshMs / 1000} detik.</p>
            </div>
            <strong className={autoSignal?.signal === "BUY" ? "signal-badge buy" : autoSignal?.signal === "SELL" ? "signal-badge sell" : "signal-badge"}>
              {autoSignal?.signal || "NO SIGNAL"}
            </strong>
          </header>
          {signalError ? <div className="alert compact">{signalError}</div> : null}
          <div className="signal-grid">
            <div>
              <span>Pair</span>
              <strong>{autoSignal?.symbol || selectedSymbol}</strong>
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
            <div className="signal-reason">
              <span>Reason</span>
              <strong>{autoSignal?.reason || "-"}</strong>
            </div>
          </div>
        </section>
      </section>
    </main>
  );
}

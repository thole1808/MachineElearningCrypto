"use client";

import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";

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
  score_buy?: number;
  score_sell?: number;
  trend_interval?: string;
  trend_rsi?: string;
  atr?: string;
};

type AutoSignalResponse = {
  ok: boolean;
  signal?: AutoSignal;
  error?: string;
};

const defaultSymbol = "XAUUSDT";
const realtimeRefreshMs = 2000;
type BalanceCurrency = "USDT" | "IDR";

const subscribeHydration = () => () => undefined;
const clientSnapshot = () => true;
const serverSnapshot = () => false;

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

function aiDirection(signal?: AutoSignal | null) {
  const buyScore = Number(signal?.score_buy ?? 0);
  const sellScore = Number(signal?.score_sell ?? 0);
  if (buyScore > sellScore) return "naik";
  if (sellScore > buyScore) return "turun";
  return "netral";
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
  const mounted = useSyncExternalStore(subscribeHydration, clientSnapshot, serverSnapshot);
  const [status, setStatus] = useState<BinanceStatus | null>(null);
  const [selectedSymbol, setSelectedSymbol] = useState(defaultSymbol);
  const [error, setError] = useState("");
  const [signalError, setSignalError] = useState("");
  const [loading, setLoading] = useState(false);
  const [checked, setChecked] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);
  const [balanceCurrency, setBalanceCurrency] = useState<BalanceCurrency>("USDT");
  const [autoSignal, setAutoSignal] = useState<AutoSignal | null>(null);
  const [hideBalance, setHideBalance] = useState(false);

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
    if (hideBalance) return "***";
    if (showIdr) return formatIdr(idrValue);
    return formatUsd(usdtValue);
  }, [showIdr, hideBalance]);
  const idrRate = Number(balanceSummary?.usdt_idr_rate || 0);
  const aiScoreMax = Number(process.env.NEXT_PUBLIC_SIGNAL_SCORE_MAX || 6);
  const activeScore = Math.max(Number(autoSignal?.score_buy ?? 0), Number(autoSignal?.score_sell ?? 0));
  const aiConfidence = Math.max(0, Math.min(100, Math.round((activeScore / aiScoreMax) * 100)));
  const aiScoreBars = `${"█".repeat(Math.round(aiConfidence / 10)).padEnd(10, "░")}`;
  const marketDirection = aiDirection(autoSignal);
  const closePrice = Number(autoSignal?.close || status?.price?.price || 0);
  const tpPips = 35;
  const slPips = 25;
  const pipSize = 0.01;
  const tpDistance = tpPips * pipSize;
  const slDistance = slPips * pipSize;
  const targetPrice = autoSignal?.signal === "SELL" ? closePrice - tpDistance : closePrice + tpDistance;
  const stopPrice = autoSignal?.signal === "SELL" ? closePrice + slDistance : closePrice - slDistance;
  const aiAdvice = autoSignal?.signal
    ? `Sinyal ${autoSignal.signal} terdeteksi, tapi eksekusi live tetap mengikuti AUTO_TRADE_ENABLED dan limit risiko.`
    : "Sinyal belum cukup kuat untuk entry. Tunggu score memenuhi threshold dan hindari entry manual.";

  const formatAnySignedUsd = useCallback((value?: string) => {
    if (!value) return "-";
    if (hideBalance) return "***";
    if (showIdr && idrRate) {
      return formatIdr(String(Number(value) * idrRate));
    }
    return formatSignedUsd(value);
  }, [showIdr, idrRate, hideBalance]);

  if (!mounted) {
    return (
      <main className="terminal">
        <header className="topbar">
          <div>
            <p className="eyebrow">Futures Trading Console</p>
            <h1>Machine Elearning Crypto</h1>
          </div>
          <div className="topbar-actions">
            <span className="status-pill">Loading</span>
          </div>
        </header>
      </main>
    );
  }

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
        <article className="metric glass hero-metric">
          <span>Wallet ({balanceCurrency})</span>
          <strong>{formatBalance(balanceSummary?.wallet_usdt, balanceSummary?.wallet_idr)}</strong>
        </article>
        <article className="metric glass">
          <span>Available</span>
          <strong>{formatBalance(balanceSummary?.available_usdt, balanceSummary?.available_idr)}</strong>
        </article>
        <article className="metric glass">
          <span>Floating PnL</span>
          <strong className={totalUnrealizedPnl >= 0 ? "up" : "down"}>{formatAnySignedUsd(String(totalUnrealizedPnl))}</strong>
        </article>
        <article className="metric glass">
          <span>Active Positions</span>
          <strong>{openPositions.length}</strong>
        </article>
        <article className="metric glass">
          <span>{status?.symbol || selectedSymbol}</span>
          <strong className="price-value">{formatPrice(status?.price?.price)}</strong>
        </article>
      </section>

      <section className="workspace">
        <section className="panel glass positions-panel">
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
                    <span className={pnl >= 0 ? "up" : "down"}>{formatAnySignedUsd(pnlValue)}</span>
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

        <section className="panel glass account-panel">
          <header className="panel-head balance-headline">
            <div>
              <h2 style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                Account
                <button
                  type="button"
                  onClick={() => setHideBalance(!hideBalance)}
                  style={{ background: "transparent", border: "none", color: "var(--muted)", padding: 0 }}
                  title="Toggle Balance Visibility"
                >
                  {hideBalance ? (
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>
                  ) : (
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                  )}
                </button>
              </h2>
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
                    <span className={toneClass(showIdr ? balance.unrealizedIdr : unrealizedValue)}>{formatAnySignedUsd(unrealizedValue)}</span>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="empty">Saldo Futures belum terbaca.</p>
          )}
        </section>

        <section className="panel glass signal-panel">
          <header className="panel-head">
            <div>
              <h2>Signal</h2>
              <p>Pair aktif refresh tiap {realtimeRefreshMs / 1000} detik.</p>
            </div>
            <div className="signal-actions">
              <strong className={autoSignal?.signal === "BUY" ? "signal-badge buy" : autoSignal?.signal === "SELL" ? "signal-badge sell" : "signal-badge"}>
                {autoSignal?.signal || "NO SIGNAL"}
              </strong>
            </div>
          </header>
          {signalError ? <div className="alert compact">{signalError}</div> : null}
          <div className="ai-report">
            <div className="ai-report-head">
              <div>
                <span>Update Pasar</span>
                <strong>{autoSignal?.symbol || selectedSymbol}</strong>
              </div>
              <div>
                <span>Harga Sekarang</span>
                <strong>{formatPrice(autoSignal?.close || status?.price?.price)}</strong>
              </div>
            </div>

            <div className="ai-report-body">
              <section>
                <span>Situasi Saat Ini</span>
                <p>
                  Harga {autoSignal?.symbol || selectedSymbol} terbaca dalam bias {marketDirection}. Confidence AI {aiConfidence}% dengan score {activeScore}/{aiScoreMax}.
                </p>
              </section>
              <section>
                <span>Target & Proteksi</span>
                <div className="ai-levels">
                  <div><small>Target TP</small><strong>{closePrice ? formatPrice(String(targetPrice)) : "-"}</strong></div>
                  <div><small>Stop</small><strong>{closePrice ? formatPrice(String(stopPrice)) : "-"}</strong></div>
                  <div><small>TP/SL</small><strong>{tpPips}/{slPips} pips</strong></div>
                </div>
              </section>
              <section>
                <span>Saran AI</span>
                <p>{aiAdvice}</p>
              </section>
            </div>

            <div className="ai-score">
              <span className="score-bars">{aiScoreBars}</span>
              <strong>{activeScore}/{aiScoreMax}</strong>
              <span>{autoSignal?.trend_interval || "5m"} trend RSI {autoSignal?.trend_rsi || "-"}</span>
            </div>
          </div>
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

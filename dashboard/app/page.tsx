"use client";

import { useCallback, useEffect, useMemo, useState, useSyncExternalStore, useRef } from "react";

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

type OpenOrder = {
  symbol?: string;
  side?: string;
  type?: string;
  price?: string;
  origQty?: string;
  executedQty?: string;
  status?: string;
  clientOrderId?: string;
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
  open_orders?: OpenOrder[];
  account_error?: string;
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
  rsi_regime?: {
    enabled?: boolean;
    overbought?: string;
    oversold?: string;
    long_block_above?: string;
    short_block_below?: string;
  };
  atr?: string;
  stale?: boolean;
};

type SignalDiagnostic = {
  symbol: string;
  trigger_ok: boolean;
  trigger_reason: string;
  can_enter: boolean;
  can_enter_reason: string;
  auto_trade_enabled: boolean;
  auto_scalping_enabled: boolean;
};

type MultiSignalItem = {
  ok: boolean;
  signal?: AutoSignal;
  diagnostic?: SignalDiagnostic;
  symbol?: string;
  error?: string;
};

type MultiSignalResponse = {
  ok: boolean;
  signals?: MultiSignalItem[];
  error?: string;
};

type TradingControl = {
  auto_trade_enabled: boolean;
  auto_scalping: boolean;
  dry_run: boolean;
};

type TradingControlResponse = {
  ok: boolean;
  control?: TradingControl;
  error?: string;
};

type ScalpingSymbolsResponse = {
  ok: boolean;
  symbols?: string[];
  error?: string;
};

const fallbackSignalSymbols = (
  process.env.NEXT_PUBLIC_SIGNAL_SYMBOLS ||
  "BTCUSDT"
)
  .split(",")
  .map((symbol) => symbol.trim().toUpperCase())
  .filter(Boolean);
const defaultSymbol = fallbackSignalSymbols[0] || "BTCUSDT";
const positionRefreshMs = 10000;
const signalRefreshMs = 60000;
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
  return `${number > 0 ? "+" : ""}${number.toLocaleString("en-US", {
    minimumFractionDigits: 4,
    maximumFractionDigits: 4,
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

function signalScore(signal?: AutoSignal | null) {
  return Math.max(Number(signal?.score_buy ?? 0), Number(signal?.score_sell ?? 0));
}

function signalSummary(signal?: AutoSignal | null, diagnostic?: SignalDiagnostic) {
  if (!signal) return "Belum ada data signal dari backend.";
  if (signal.signal) {
    if (!diagnostic?.trigger_ok) return diagnostic?.trigger_reason || signal.reason;
    if (!diagnostic?.can_enter) return diagnostic?.can_enter_reason || signal.reason;
    return signal.reason;
  }
  if (signal.stale) return signal.reason;
  return signal.reason.startsWith("NO SIGNAL:")
    ? signal.reason.replace("NO SIGNAL:", "Belum entry:")
    : signal.reason;
}

function rsiTone(value?: string) {
  const rsi = Number(value ?? 0);
  if (!Number.isFinite(rsi)) return "neutral";
  if (rsi >= 70) return "hot";
  if (rsi <= 30) return "cold";
  if (rsi >= 60) return "firm";
  if (rsi <= 40) return "soft";
  return "neutral";
}

function rsiLabel(value?: string) {
  const rsi = Number(value ?? 0);
  if (!Number.isFinite(rsi)) return "RSI -";
  if (rsi >= 70) return "Overbought";
  if (rsi <= 30) return "Oversold";
  if (rsi >= 60) return "Bullish";
  if (rsi <= 40) return "Bearish";
  return "Neutral";
}

function rsiWidth(value?: string) {
  const rsi = Number(value ?? 0);
  if (!Number.isFinite(rsi)) return 0;
  return Math.max(0, Math.min(100, rsi));
}

function formatUsd(value?: string) {
  if (!value) return "-";
  const number = Number(value);
  return `${number.toLocaleString("en-US", {
    minimumFractionDigits: Math.abs(number) > 0 && Math.abs(number) < 10 ? 4 : 2,
    maximumFractionDigits: Math.abs(number) > 0 && Math.abs(number) < 10 ? 4 : 2,
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

function TradingViewChart({ symbol, timeframe = "5", layoutId = "" }: { symbol: string; timeframe?: string; layoutId?: string }) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.innerHTML = "";
    }

    const containerId = `tradingview_${Math.random().toString(36).substring(7)}`;
    if (containerRef.current) {
      containerRef.current.id = containerId;
    }

    const scriptId = "tradingview-widget-script";
    let script = document.getElementById(scriptId) as HTMLScriptElement;

    const initWidget = () => {
      if (typeof window !== "undefined" && (window as any).TradingView) {
        let formattedSymbol = symbol.toUpperCase();
        if (!formattedSymbol.includes(":")) {
          if (formattedSymbol.endsWith("USDT")) {
            formattedSymbol = `BINANCE:${formattedSymbol}.P`;
          } else {
            formattedSymbol = `BINANCE:${formattedSymbol}`;
          }
        }

        const widgetConfig: any = {
          width: "100%",
          height: 580,
          symbol: formattedSymbol,
          interval: timeframe,
          timezone: "Asia/Jakarta",
          theme: "dark",
          style: "1",
          locale: "en",
          enable_publishing: false,
          hide_side_toolbar: false,
          allow_symbol_change: true,
          container_id: containerId,
          studies: [
            "RSI@tv-basicstudies",
            "MASimple@tv-basicstudies"
          ],
          show_popup_button: true,
          popup_width: "1000",
          popup_height: "650",
          overrides: {
            "paneProperties.background": "#161a1e",
            "paneProperties.backgroundType": "solid",
            "paneProperties.vertGridProperties.color": "rgba(43, 49, 57, 0.4)",
            "paneProperties.horzGridProperties.color": "rgba(43, 49, 57, 0.4)",
            "symbolWatermarkProperties.transparency": 90,
            "scalesProperties.textColor": "#848e9c",
            "mainSeriesProperties.candleStyle.upColor": "#0ecb81",
            "mainSeriesProperties.candleStyle.downColor": "#f6465d",
            "mainSeriesProperties.candleStyle.drawWick": true,
            "mainSeriesProperties.candleStyle.drawBorder": true,
            "mainSeriesProperties.candleStyle.borderColor": "#2b3139",
            "mainSeriesProperties.candleStyle.borderUpColor": "#0ecb81",
            "mainSeriesProperties.candleStyle.borderDownColor": "#f6465d",
            "mainSeriesProperties.candleStyle.wickUpColor": "#0ecb81",
            "mainSeriesProperties.candleStyle.wickDownColor": "#f6465d",
          }
        };

        if (layoutId && layoutId.trim() !== "") {
          widgetConfig.chart = layoutId.trim();
        }

        new (window as any).TradingView.widget(widgetConfig);
      }
    };

    if (!script) {
      script = document.createElement("script");
      script.id = scriptId;
      script.src = "https://s3.tradingview.com/tv.js";
      script.type = "text/javascript";
      script.async = true;
      script.onload = initWidget;
      document.head.appendChild(script);
    } else {
      if ((window as any).TradingView) {
        initWidget();
      } else {
        script.addEventListener("load", initWidget);
      }
    }

    return () => {
      if (script) {
        script.removeEventListener("load", initWidget);
      }
    };
  }, [symbol, timeframe, layoutId]);

  return (
    <div className="tradingview-chart-container">
      <div ref={containerRef} className="tradingview-chart-frame" />
    </div>
  );
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
  const [autoSignals, setAutoSignals] = useState<AutoSignal[]>([]);
  const [signalDiagnostics, setSignalDiagnostics] = useState<Record<string, SignalDiagnostic>>({});
  const [signalSymbols, setSignalSymbols] = useState<string[]>(fallbackSignalSymbols);
  const [hideBalance, setHideBalance] = useState(false);
  const [tradeLoadingSymbol, setTradeLoadingSymbol] = useState("");
  const [tradeMessage, setTradeMessage] = useState("");
  const [tradingControl, setTradingControl] = useState<TradingControl | null>(null);
  const [controlLoading, setControlLoading] = useState(false);
  const [controlError, setControlError] = useState("");
  const [tpLoadingSymbol, setTpLoadingSymbol] = useState("");
  const [positionMessage, setPositionMessage] = useState("");
  const [chartTimeframe, setChartTimeframe] = useState("5");
  const [activeChartSymbol, setActiveChartSymbol] = useState<string | null>(null);
  const [layoutId, setLayoutId] = useState(() => {
    if (typeof window === "undefined") return "W4YOe0Vx";
    return localStorage.getItem("tv_layout_id") || "W4YOe0Vx";
  });

  const handleSaveLayoutId = (val: string) => {
    setLayoutId(val);
    localStorage.setItem("tv_layout_id", val);
  };

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
      const openPos = body.binance.open_positions || [];
      if (openPos.length > 0) {
        setActiveChartSymbol((prev) => prev || openPos[0].symbol);
      }
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
      const response = await fetch(`/api/auto-signal?symbols=${signalSymbols.join(",")}`);
      const body = (await response.json()) as MultiSignalResponse;
      if (!response.ok || !body.ok || !body.signals) {
        throw new Error(body.error || "Backend belum bisa membaca signal Binance.");
      }
      const failedSignals = body.signals
        .filter((item) => !item.ok || item.error)
        .map((item) => `${item.symbol || "PAIR"}: ${item.error || "gagal membaca signal"}`);
      const diagnosticMap: Record<string, SignalDiagnostic> = {};
      for (const item of body.signals) {
        if (item.signal?.symbol && item.diagnostic) {
          diagnosticMap[item.signal.symbol] = item.diagnostic;
        }
      }
      const signals = body.signals
        .map((item) => item.signal)
        .filter((item): item is AutoSignal => Boolean(item))
        .sort((left, right) => signalScore(right) - signalScore(left));
      setAutoSignals(signals);
      setSignalDiagnostics(diagnosticMap);
      setAutoSignal(signals.find((item) => item.symbol === selectedSymbol) || signals[0] || null);
      setSignalError(
        failedSignals.length
          ? failedSignals.slice(0, 3).join(" | ")
          : signals.length
            ? ""
            : "Backend tidak mengirim data signal untuk pair yang discan. Cek log backend atau restart ./start-web.sh.",
      );
    } catch (caught) {
      setSignalError(caught instanceof Error ? caught.message : "Terjadi error saat membaca signal.");
    }
  }, [selectedSymbol, signalSymbols]);

  const loadScalpingSymbols = useCallback(async () => {
    try {
      const response = await fetch("/api/scalping/symbols");
      const body = (await response.json()) as ScalpingSymbolsResponse;
      if (!response.ok || !body.ok || !body.symbols?.length) {
        throw new Error(body.error || "Backend belum bisa membaca daftar scalping.");
      }
      setSignalSymbols(body.symbols);
      setSelectedSymbol((prev) => (body.symbols?.includes(prev) ? prev : body.symbols?.[0] || prev));
      setActiveChartSymbol((prev) => prev || body.symbols?.[0] || null);
    } catch {
      setSignalSymbols(fallbackSignalSymbols);
    }
  }, []);

  const loadTradingControl = useCallback(async () => {
    try {
      const response = await fetch("/api/trading-control");
      const body = (await response.json()) as TradingControlResponse;
      if (!response.ok || !body.ok || !body.control) {
        throw new Error(body.error || "Backend belum bisa membaca kontrol trading.");
      }
      setTradingControl(body.control);
      setControlError("");
    } catch (caught) {
      setControlError(caught instanceof Error ? caught.message : "Terjadi error saat membaca kontrol trading.");
    }
  }, []);

  const toggleTrading = useCallback(async () => {
    const nextEnabled = !tradingControl?.auto_trade_enabled;
    setControlLoading(true);
    setControlError("");
    try {
      const response = await fetch("/api/trading-control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ auto_trade_enabled: nextEnabled }),
      });
      const body = (await response.json()) as TradingControlResponse;
      if (!response.ok || !body.ok || !body.control) {
        throw new Error(body.error || "Gagal mengubah kontrol trading.");
      }
      setTradingControl(body.control);
      setTradeMessage(nextEnabled ? "Trading enabled. Bot boleh entry lagi." : "Trading disabled. Entry baru dipause.");
    } catch (caught) {
      setControlError(caught instanceof Error ? caught.message : "Terjadi error saat mengubah kontrol trading.");
    } finally {
      setControlLoading(false);
    }
  }, [tradingControl]);

  const takeProfitNow = useCallback(async (position: OpenPosition) => {
    const pnlValue = position.unRealizedProfit ?? position.unrealizedProfit ?? "0";
    if (Number(pnlValue) <= 0) return;
    const confirmed = window.confirm(`Close profit ${position.symbol} sekarang?`);
    if (!confirmed) return;
    setTpLoadingSymbol(position.symbol);
    setPositionMessage("");
    try {
      const response = await fetch("/api/position/take-profit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: position.symbol }),
      });
      const body = await response.json();
      if (!response.ok || !body.ok) {
        throw new Error(body.error || "Gagal close profit.");
      }
      setPositionMessage(`${position.symbol} closed profit: ${body.pnl_usdt} USDT.`);
      checkBinance();
    } catch (caught) {
      setPositionMessage(caught instanceof Error ? caught.message : "Terjadi error saat close profit.");
    } finally {
      setTpLoadingSymbol("");
    }
  }, [checkBinance]);

  const executeSignal = useCallback(async (signal: AutoSignal) => {
    if (!signal.signal) return;
    const confirmed = window.confirm(`Kirim order ${signal.signal} untuk ${signal.symbol}?`);
    if (!confirmed) return;
    setTradeLoadingSymbol(signal.symbol);
    setTradeMessage("");
    try {
      const response = await fetch("/api/auto-signal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: signal.symbol,
          usdt: process.env.NEXT_PUBLIC_ORDER_USDT || "5",
          leverage: process.env.NEXT_PUBLIC_DEFAULT_LEVERAGE || "5",
        }),
      });
      const body = await response.json();
      if (!response.ok || !body.ok) {
        throw new Error(body.error || body.result?.reason || "Order gagal dikirim.");
      }
      const result = body.result;
      setTradeMessage(result?.accepted ? `${signal.symbol} ${signal.signal} terkirim.` : `${signal.symbol}: ${result?.reason || "Order tidak diterima."}`);
      checkBinance();
    } catch (caught) {
      setTradeMessage(caught instanceof Error ? caught.message : "Terjadi error saat kirim order.");
    } finally {
      setTradeLoadingSymbol("");
    }
  }, [checkBinance]);

  const executeManualTrade = useCallback(async (symbol: string, side: "BUY" | "SELL") => {
    const confirmed = window.confirm(`Buka ${side} manual untuk ${symbol}?`);
    if (!confirmed) return;
    setTradeLoadingSymbol(`${symbol}:${side}`);
    setTradeMessage("");
    try {
      const response = await fetch("/api/auto-signal", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol,
          side,
          usdt: process.env.NEXT_PUBLIC_ORDER_USDT || "5",
          leverage: process.env.NEXT_PUBLIC_DEFAULT_LEVERAGE || "5",
          entry_order_type: "MARKET",
        }),
      });
      const body = await response.json();
      if (!response.ok || !body.ok) {
        throw new Error(body.error || body.result?.reason || `${side} manual gagal dikirim.`);
      }
      const result = body.result;
      setTradeMessage(result?.accepted ? `${symbol} ${side} manual terkirim.` : `${symbol}: ${result?.reason || "Order manual tidak diterima."}`);
      checkBinance();
    } catch (caught) {
      setTradeMessage(caught instanceof Error ? caught.message : "Terjadi error saat kirim order manual.");
    } finally {
      setTradeLoadingSymbol("");
    }
  }, [checkBinance]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      checkBinance();
      loadTradingControl();
    }, positionRefreshMs);

    return () => window.clearInterval(timer);
  }, [checkBinance, loadTradingControl]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      loadScalpingSymbols();
      loadAutoSignal();
    }, signalRefreshMs);

    return () => window.clearInterval(timer);
  }, [loadAutoSignal, loadScalpingSymbols]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      checkBinance();
      loadScalpingSymbols();
      loadAutoSignal();
      loadTradingControl();
    }, 0);

    return () => window.clearTimeout(timer);
  }, [checkBinance, loadAutoSignal, loadScalpingSymbols, loadTradingControl]);

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
  const chartSymbol = useMemo(() => {
    if (openPositions.length > 0) {
      if (activeChartSymbol && openPositions.some(p => p.symbol === activeChartSymbol)) {
        return activeChartSymbol;
      }
      return openPositions[0].symbol;
    }
    if (activeChartSymbol) {
      return activeChartSymbol;
    }
    return selectedSymbol;
  }, [activeChartSymbol, openPositions, selectedSymbol]);
  const futuresBalances = status?.balances || [];
  const openOrders = status?.open_orders || [];
  const visibleOpenOrders = openOrders.filter((order) => {
    const symbolMatch = (order.symbol || "").toUpperCase() === chartSymbol.toUpperCase();
    const isWorking = ["NEW", "PARTIALLY_FILLED"].includes((order.status || "").toUpperCase());
    return symbolMatch && isWorking;
  });
  const visibleFuturesBalances = futuresBalances.filter((balance) => {
    const wallet = Number(balance.walletUsdt ?? balance.walletBalance ?? 0);
    const margin = Number(balance.marginUsdt ?? balance.marginBalance ?? 0);
    const unrealized = Number(balance.unrealizedUsdt ?? balance.unrealizedProfit ?? 0);
    return Math.abs(wallet) > 0 || Math.abs(margin) > 0 || Math.abs(unrealized) > 0;
  });
  const balanceSummary = status?.balance_summary;
  const accountError = status?.account_error;
  const positionUnrealizedPnl = openPositions.reduce(
    (total, position) => total + Number(position.unRealizedProfit ?? position.unrealizedProfit ?? 0),
    0,
  );
  const totalUnrealizedPnl = Number(balanceSummary?.unrealized_usdt ?? positionUnrealizedPnl);
  const showIdr = balanceCurrency === "IDR";
  const formatBalance = useCallback((usdtValue?: string, idrValue?: string) => {
    if (hideBalance) return "***";
    if (showIdr) return formatIdr(idrValue);
    return formatUsd(usdtValue);
  }, [showIdr, hideBalance]);
  const idrRate = Number(balanceSummary?.usdt_idr_rate || 0);
  const aiScoreMax = Number(process.env.NEXT_PUBLIC_SIGNAL_SCORE_MAX || 6);
  const strongSignalThreshold = Number(process.env.NEXT_PUBLIC_STRONG_SIGNAL_THRESHOLD || aiScoreMax);
  const activeSignalCards = autoSignals.filter((signal) => Boolean(signal.signal));
  const watchSignals = autoSignals.slice(0, 8);

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
          <button
            className={tradingControl?.auto_trade_enabled ? "trade-toggle on" : "trade-toggle off"}
            disabled={controlLoading}
            type="button"
            onClick={toggleTrading}
            title="Pause atau aktifkan entry baru dari bot"
          >
            <span>{tradingControl?.auto_trade_enabled ? "Trading ON" : "Trading OFF"}</span>
            <strong>{controlLoading ? "..." : tradingControl?.auto_trade_enabled ? "Enabled" : "Disabled"}</strong>
          </button>
          <span className="status-pill">Update {lastUpdated}</span>
          <button className="primary" disabled={loading} onClick={() => checkBinance(true)}>
            {loading ? "Syncing" : "Refresh"}
          </button>
        </div>
      </header>

      {error ? <section className="alert">{error}</section> : null}
      {controlError ? <section className="alert">{controlError}</section> : null}
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

      <div className="terminal-layout">
        <aside className="terminal-sidebar">
          {/* Signal Panel (Auto Signals & Scan) */}
          <section className="panel glass signal-panel">
            <header className="panel-head">
              <div>
                <h2>Signal</h2>
                <p>Scan {signalSymbols.length} pair, refresh tiap {signalRefreshMs / 1000} detik.</p>
              </div>
              <div className="signal-actions">
                <strong className={activeSignalCards.length ? "signal-badge buy" : "signal-badge"}>
                  {activeSignalCards.length ? `${activeSignalCards.length} SIGNAL` : "NO SIGNAL"}
                </strong>
              </div>
            </header>
            {signalError ? <div className="alert compact">{signalError}</div> : null}
            {tradeMessage ? <div className="alert compact">{tradeMessage}</div> : null}
            <div className="multi-signal-grid">
              {activeSignalCards.length ? (
                activeSignalCards.map((signal) => {
                  const score = signalScore(signal);
                  const confidence = Math.max(0, Math.min(100, Math.round((score / aiScoreMax) * 100)));
                  const isTrading = tradeLoadingSymbol === signal.symbol;
                  const isManualBuyLoading = tradeLoadingSymbol === `${signal.symbol}:BUY`;
                  const isManualSellLoading = tradeLoadingSymbol === `${signal.symbol}:SELL`;
                  const isStrongSignal = score >= strongSignalThreshold;
                  const rsiStatus = rsiLabel(signal.rsi);
                  const rsiState = rsiTone(signal.rsi);
                  const diagnostic = signalDiagnostics[signal.symbol];
                  return (
                    <article className={`signal-card ${signal.signal === "BUY" ? "buy" : "sell"}`} key={signal.symbol}>
                      <div className="signal-card-head">
                        <strong>{signal.symbol}</strong>
                        <span>{signal.signal}</span>
                      </div>
                      <div className="signal-card-price">{formatPrice(signal.close)}</div>
                      <div className="signal-card-meta">
                        <span>Score {score}/{aiScoreMax}</span>
                        <span>AI {confidence}%</span>
                        <span>RSI {signal.rsi}</span>
                      </div>
                      <div className={`rsi-meter ${rsiState}`}>
                        <div className="rsi-meter-head">
                          <strong>{rsiStatus}</strong>
                          <span>{signal.interval} RSI {signal.rsi}</span>
                        </div>
                        <div className="rsi-track">
                          <i style={{ width: `${rsiWidth(signal.rsi)}%` }} />
                        </div>
                        <div className="rsi-scale">
                          <span>30 OS</span>
                          <span>{signal.trend_interval || "Trend"} {signal.trend_rsi ? `RSI ${signal.trend_rsi}` : "RSI -"}</span>
                          <span>70 OB</span>
                        </div>
                        {signal.rsi_regime?.enabled ? (
                          <div className="rsi-thresholds">
                            <span>Block L {signal.rsi_regime.long_block_above || "72"}</span>
                            <span>Block S {signal.rsi_regime.short_block_below || "28"}</span>
                          </div>
                        ) : null}
                      </div>
                      <p>{signalSummary(signal, diagnostic)}</p>
                      <button
                        className={`signal-trade-button ${signal.signal === "BUY" ? "buy" : "sell"}`}
                        disabled={isTrading || !isStrongSignal}
                        type="button"
                        onClick={() => executeSignal(signal)}
                      >
                        {isTrading ? "Sending" : isStrongSignal ? `Entry ${signal.signal}` : `Wait Strong ${score}/${strongSignalThreshold}`}
                      </button>
                      <div className="manual-trade-row">
                        <button
                          className="signal-trade-button buy secondary-trade-button"
                          disabled={isManualBuyLoading}
                          type="button"
                          onClick={() => executeManualTrade(signal.symbol, "BUY")}
                        >
                          {isManualBuyLoading ? "Sending" : "Manual BUY"}
                        </button>
                        <button
                          className="signal-trade-button sell secondary-trade-button"
                          disabled={isManualSellLoading}
                          type="button"
                          onClick={() => executeManualTrade(signal.symbol, "SELL")}
                        >
                          {isManualSellLoading ? "Sending" : "Manual SELL"}
                        </button>
                      </div>
                    </article>
                  );
                })
              ) : (
                <div className="scan-strip">
                  {watchSignals.length ? (
                    watchSignals.map((signal) => {
                      const rsiStatus = rsiLabel(signal.rsi);
                      const rsiState = rsiTone(signal.rsi);
                      const diagnostic = signalDiagnostics[signal.symbol];
                      return (
                        <article
                          className={`scan-rsi-card ${signal.symbol === autoSignal?.symbol ? "active" : ""}`}
                          key={signal.symbol}
                        >
                          <button
                            className="scan-rsi-select"
                            type="button"
                            onClick={() => {
                              setAutoSignal(signal);
                              setSelectedSymbol(signal.symbol);
                              setActiveChartSymbol(signal.symbol);
                            }}
                          >
                          <div className="scan-rsi-head">
                            <strong>{signal.symbol}</strong>
                            <span>{signalScore(signal)}/{aiScoreMax}</span>
                          </div>
                          <div className={`rsi-meter compact ${rsiState}`}>
                            <div className="rsi-meter-head">
                              <strong>{rsiStatus}</strong>
                              <span>{signal.interval} RSI {signal.rsi}</span>
                            </div>
                            <div className="rsi-track">
                              <i style={{ width: `${rsiWidth(signal.rsi)}%` }} />
                            </div>
                            <div className="rsi-scale">
                              <span>30</span>
                              <span>{signal.trend_rsi ? `Trend ${signal.trend_rsi}` : "Trend -"}</span>
                              <span>70</span>
                            </div>
                          </div>
                          <p className="scan-rsi-reason">{signalSummary(signal, diagnostic)}</p>
                          </button>
                          <div className="manual-trade-row compact-manual-row">
                            <button
                              className="signal-trade-button buy secondary-trade-button"
                              disabled={tradeLoadingSymbol === `${signal.symbol}:BUY`}
                              type="button"
                              onClick={() => executeManualTrade(signal.symbol, "BUY")}
                            >
                              {tradeLoadingSymbol === `${signal.symbol}:BUY` ? "Sending" : "Manual BUY"}
                            </button>
                            <button
                              className="signal-trade-button sell secondary-trade-button"
                              disabled={tradeLoadingSymbol === `${signal.symbol}:SELL`}
                              type="button"
                              onClick={() => executeManualTrade(signal.symbol, "SELL")}
                            >
                              {tradeLoadingSymbol === `${signal.symbol}:SELL` ? "Sending" : "Manual SELL"}
                            </button>
                          </div>
                        </article>
                      );
                    })
                  ) : (
                    <div className="empty signal-empty">Belum ada data RSI dari backend.</div>
                  )}
                </div>
              )}
            </div>
          </section>

          {/* Account Panel */}
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
            {accountError ? (
              <div className="alert compact">Account private belum terbaca: {accountError}</div>
            ) : null}
            {visibleFuturesBalances.length ? (
              <div className="balance-table">
                <div className="balance-row balance-head">
                  <span>Asset</span>
                  <span>Wallet</span>
                  <span>Avail.</span>
                  <span>U-PnL</span>
                </div>
                {visibleFuturesBalances.map((balance) => {
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
              <p className="empty">{accountError ? "Cek API key, private key RSA, permission Futures, atau IP restriction." : "Saldo Futures belum terbaca."}</p>
            )}
          </section>
        </aside>

        <main className="terminal-main">
          {/* Live Chart & Candlesticks Panel */}
          <section className="panel glass chart-panel">
            <header className="panel-head chart-header">
              <div>
                <h2 className="chart-title">
                  Live Chart & Candlesticks
                  <div className="chart-symbol-badge chart-symbol-badge-futures">
                    <span className="chart-symbol-pulse chart-symbol-pulse-futures"></span>
                    <span className="chart-symbol-label">BINANCE FUTURES</span>
                    <span className="chart-symbol-divider">|</span>
                    <span>{chartSymbol}</span>
                  </div>
                </h2>
                <p>
                  {openPositions.length > 0
                    ? "Menampilkan grafik entry yang sedang berjalan secara live."
                    : "Pilih koin di panel scan untuk menampilkan grafiknya."}
                </p>
              </div>
              <div className="chart-controls">
                <div className="chart-input-group">
                  <span className="chart-control-label">TV CHART ID:</span>
                  <input
                    className="chart-layout-input"
                    type="text"
                    placeholder="e.g. g8KjHs12"
                    value={layoutId}
                    onChange={(e) => handleSaveLayoutId(e.target.value)}
                  />
                </div>
                
                <div className="chart-input-group">
                  <span className="chart-control-label">TIMEFRAME:</span>
                  <div className="timeframe-selector" aria-label="Pilih timeframe chart">
                    {[
                      { label: "1m", value: "1" },
                      { label: "3m", value: "3" },
                      { label: "5m", value: "5" },
                      { label: "15m", value: "15" },
                      { label: "30m", value: "30" },
                      { label: "1H", value: "60" },
                      { label: "4H", value: "240" },
                      { label: "1D", value: "D" },
                    ].map((tf) => (
                      <button
                        className={chartTimeframe === tf.value ? "active" : ""}
                        key={tf.value}
                        type="button"
                        onClick={() => setChartTimeframe(tf.value)}
                      >
                        {tf.label}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </header>
            <div className="chart-wrapper">
              {checked ? (
                <TradingViewChart symbol={chartSymbol} timeframe={chartTimeframe} layoutId={layoutId} />
              ) : (
                <div className="chart-loading">
                  <div className="chart-symbol-pulse chart-loading-pulse"></div>
                  <span className="chart-loading-text">LOADING MARKET DATA...</span>
                </div>
              )}
            </div>
            <div className="chart-tip">
              <span className="chart-tip-icon">Tip</span>
              <span>
                <strong>PRO-TIP UNTUK KODE SMC:</strong> Karena standard widget melarang pencarian indikator komunitas secara langsung, silakan buka <strong>tradingview.com</strong> di tab baru, buat chart dengan indikator <strong>&quot;LuxAlgo® - Price Action Concepts™&quot;</strong>, lalu <strong>Save Chart Layout</strong> tersebut dan pastikan opsi <strong>Sharing</strong> aktif. Copy kode layout ID dari URL-nya (contoh: <code>g8KjHs12</code> dari <code>https://www.tradingview.com/chart/g8KjHs12/</code>) dan paste di kolom <strong>&quot;TV CHART ID&quot;</strong> di atas. Chart kustom Anda dengan SMC LuxAlgo akan tampil otomatis!
              </span>
            </div>
          </section>

          {/* Open Positions Panel */}
          <section className="panel glass positions-panel">
            <header className="panel-head">
              <div>
                <h2>Open Positions</h2>
                <p>Entry, mark, target TP/SL, ROE, dan liquidation refresh tiap {positionRefreshMs / 1000} detik.</p>
              </div>
            </header>
            {visibleOpenOrders.length ? (
              <div className="pending-orders-banner">
                <strong>Pending Limit Order</strong>
                <div className="pending-orders-grid">
                  {visibleOpenOrders.map((order) => (
                    <div className="pending-order-card" key={`${order.clientOrderId || "order"}-${order.price || "0"}`}>
                      <span>{order.side} {order.type}</span>
                      <strong>{formatPrice(order.price)}</strong>
                      <span>Qty {formatNumber(order.origQty)}</span>
                      <span>Status {order.status || "-"}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
            {positionMessage ? <div className="alert compact">{positionMessage}</div> : null}
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
                  <span>Action</span>
                </div>
                {openPositions.map((position) => {
                  const amount = Number(position.positionAmt);
                  const pnlValue = position.unRealizedProfit ?? position.unrealizedProfit ?? "0";
                  const pnl = Number(pnlValue);
                  const isTpLoading = tpLoadingSymbol === position.symbol;
                  const isCurrentChart = position.symbol === chartSymbol;
                  return (
                    <div 
                      className={`positions-row ${isCurrentChart ? "active-chart-row" : ""}`} 
                      key={position.symbol}
                      onClick={() => setActiveChartSymbol(position.symbol)}
                      style={{ cursor: "pointer" }}
                      title={`Klik untuk menampilkan grafik live ${position.symbol}`}
                    >
                      <strong>
                        <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                          {isCurrentChart && <span className="chart-symbol-pulse" style={{ width: "6px", height: "6px" }}></span>}
                          {position.symbol}
                        </span>
                      </strong>
                      <span className={amount >= 0 ? "up" : "down"}>{position.positionSide && position.positionSide !== "BOTH" ? position.positionSide : amount >= 0 ? "LONG" : "SHORT"}</span>
                      <span>{formatNumber(position.positionAmt)}</span>
                      <span>{formatPrice(position.entryPrice)}</span>
                      <span>{formatPrice(position.markPrice)}</span>
                      <span className={pnl >= 0 ? "up" : "down"}>{formatAnySignedUsd(pnlValue)}</span>
                      <span className={pnl >= 0 ? "up" : "down"}>{formatPercent(position.roePercent ?? position.pnlPercent)}</span>
                      <span>{formatPrice(position.takeProfitPrice)}</span>
                      <span>{formatPrice(position.stopLossPrice)}</span>
                      <span>{formatPrice(position.liquidationPrice)}</span>
                      <button
                        className="tp-now-button"
                        disabled={pnl <= 0 || isTpLoading}
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          takeProfitNow(position);
                        }}
                      >
                        {isTpLoading ? "Closing" : pnl > 0 ? "TP Now" : "Wait Profit"}
                      </button>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="empty">Belum ada posisi aktif.</p>
            )}
          </section>
        </main>
      </div>
    </main>
  );
}

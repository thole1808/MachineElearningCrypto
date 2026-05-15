from __future__ import annotations

import json
import os
import time
from decimal import Decimal

from app import (
    BinanceClient,
    can_auto_enter,
    effective_order_usdt,
    env_bool,
    generate_auto_signal,
    load_dotenv,
    load_trade_memory,
    notify_signal_once,
    record_trade_pnl,
    record_auto_entry,
    score_trigger_ok,
    send_telegram,
    trade_pnl_summary,
)


def log(message: str) -> None:
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def close_positions_if_needed(symbol: str) -> None:
    client = BinanceClient(symbol=symbol)
    positions = client.open_positions()
    if not positions:
        return

    force_close_loss = env_bool("FORCE_CLOSE_LOSS", "true")
    profit_pips = Decimal(os.getenv("FORCE_CLOSE_PROFIT_PIPS", os.getenv("TAKE_PROFIT_PIPS", "35")))
    loss_pips = Decimal(os.getenv("FORCE_CLOSE_LOSS_PIPS", os.getenv("STOP_LOSS_PIPS", "25")))
    pip_size = Decimal(os.getenv("GOLD_PIP_SIZE", "0.01"))

    for position in positions:
        position_symbol = str(position.get("symbol", "")).upper()
        amount = Decimal(str(position.get("positionAmt", "0")))
        if position_symbol != symbol or amount == 0:
            continue

        entry = Decimal(str(position.get("entryPrice", "0")))
        mark = Decimal(str(position.get("markPrice", "0")))
        if entry <= 0 or mark <= 0:
            continue

        if amount > 0:
            pips = (mark - entry) / pip_size
            side_label = "LONG"
        else:
            pips = (entry - mark) / pip_size
            side_label = "SHORT"
        pnl_usdt = Decimal(str(position.get("unRealizedProfit", position.get("unrealizedProfit", "0"))))

        should_profit_close = profit_pips > 0 and pips >= profit_pips
        should_loss_close = force_close_loss and loss_pips > 0 and pips <= -loss_pips
        if not should_profit_close and not should_loss_close:
            continue

        reason = "PROFIT" if should_profit_close else "LOSS"
        result = client.close_position_amount(amount)
        status = client.status()
        idr_rate = Decimal(str(status.get("balance_summary", {}).get("usdt_idr_rate", "0")))
        record_trade_pnl(symbol, pnl_usdt)
        summary = trade_pnl_summary(symbol, idr_rate)
        message = (
            f"<b>GOLD BOT FORCE CLOSE {reason}</b>\n"
            f"{position_symbol} {side_label}\n"
            f"Pips: {pips}\n"
            f"PnL: {pnl_usdt:+.4f} USDT / Rp {pnl_usdt * idr_rate:+,.0f}\n\n"
            f"{summary}\n\n"
            f"Result: {json.dumps(result, ensure_ascii=False)}"
        )
        log(message.replace("<b>", "").replace("</b>", ""))
        send_telegram(message)


def maybe_enter(symbol: str) -> None:
    signal = generate_auto_signal(symbol)
    log(
        f"[SCAN] {symbol} signal={signal.get('signal')} "
        f"rsi={signal.get('rsi')} reason={signal.get('reason')}"
    )

    if not signal.get("signal"):
        return
    if signal.get("signal") == "SELL" and not env_bool("ALLOW_SHORT", "true"):
        log("[SKIP] SELL signal valid tapi ALLOW_SHORT=false")
        return

    ok, reason = score_trigger_ok(signal)
    if not ok:
        log(f"[SKIP] {reason}")
        return

    notify_signal_once(signal)

    can_enter, enter_reason = can_auto_enter(symbol)
    if not can_enter:
        log(f"[SKIP] {enter_reason}")
        return

    client = BinanceClient(symbol=symbol)
    if env_bool("PREVENT_DOUBLE_POSITION", "true") and client.has_open_position():
        log(f"[SKIP] {symbol} masih punya posisi terbuka")
        return
    if client.has_reached_max_open_positions():
        log("[SKIP] MAX_OPEN_POSITIONS tercapai")
        return

    payload = {
        "symbol": symbol,
        "side": signal["signal"],
        "usdt": str(effective_order_usdt(symbol, Decimal(os.getenv("ORDER_USDT", "5")))),
        "leverage": os.getenv("DEFAULT_LEVERAGE", "3"),
    }

    if not env_bool("AUTO_TRADE_ENABLED", "false"):
        log(f"[PAPER] Trigger valid tapi AUTO_TRADE_ENABLED=false payload={payload}")
        return

    result = client.handle_webhook_signal(payload)
    log("[AUTO RESULT] " + json.dumps(result, ensure_ascii=False))
    send_telegram(
        f"<b>GOLD BOT AUTO RESULT</b>\n"
        f"{payload['symbol']} {payload['side']}\n"
        f"Accepted: {result.get('accepted')}\n"
        f"Reason: {result.get('reason', '-')}\n"
        f"Qty: {result.get('quantity', '-')}"
    )
    if result.get("accepted"):
        record_auto_entry(symbol)


def main() -> None:
    load_dotenv()
    load_trade_memory()
    symbol = os.getenv("TRADE_SYMBOL", "XAUUSDT").strip().upper()
    interval = int(os.getenv("GOLD_BOT_INTERVAL", os.getenv("SCALPING_INTERVAL", "20")))
    log(f"GOLD SCORE BOT START symbol={symbol} interval={interval}s")
    log(f"AUTO_TRADE_ENABLED={os.getenv('AUTO_TRADE_ENABLED', 'false')}")
    try:
        status = BinanceClient(symbol=symbol).status()
        idr_rate = Decimal(str(status.get("balance_summary", {}).get("usdt_idr_rate", "0")))
    except Exception:
        idr_rate = Decimal("0")
    send_telegram(
        f"<b>GOLD SCORE BOT START</b>\n"
        f"Symbol: {symbol}\n"
        f"Interval: {interval}s\n\n"
        f"{trade_pnl_summary(symbol, idr_rate)}"
    )

    while True:
        try:
            close_positions_if_needed(symbol)
            maybe_enter(symbol)
        except Exception as error:
            log(f"[ERROR] {error}")
            send_telegram(f"<b>GOLD BOT ERROR</b>\n{error}")
        time.sleep(interval)


if __name__ == "__main__":
    main()

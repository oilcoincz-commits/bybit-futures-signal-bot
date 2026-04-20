import os
import asyncio
import logging
import time
from datetime import datetime
from typing import Optional
import requests
import pandas as pd
import numpy as np
from telegram import Bot, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

PAIRS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "BNB-USDT", "XRP-USDT",
    "TAO-USDT", "HYPE-USDT", "ZEC-USDT", "ARB-USDT", "OP-USDT",
    "AVAX-USDT", "DOGE-USDT",
]

TIMEFRAMES = ["5min", "15min"]
TF_LABELS = {"5min": "5m", "15min": "15m"}

KUCOIN_BASE_URL = "https://api.kucoin.com"

SIGNAL_HISTORY: dict[str, dict] = {}

SCAN_INTERVAL_SECONDS = 300


def fetch_klines(symbol: str, interval: str, limit: int = 100) -> Optional[pd.DataFrame]:
    url = f"{KUCOIN_BASE_URL}/api/v1/market/candles"
    now = int(time.time())
    seconds_per_candle = int(interval.replace("min", "")) * 60
    start_at = now - (limit + 5) * seconds_per_candle

    params = {
        "type": interval,
        "symbol": symbol,
        "startAt": start_at,
        "endAt": now,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "200000":
            logger.error("KuCoin API error for %s %s: %s", symbol, interval, data.get("msg"))
            return None
        rows = data.get("data", [])
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["timestamp", "open", "close", "high", "low", "volume", "turnover"])
        df = df.iloc[::-1].reset_index(drop=True)
        for col in ["open", "close", "high", "low", "volume"]:
            df[col] = df[col].astype(float)
        df["timestamp"] = pd.to_numeric(df["timestamp"])
        return df
    except Exception as exc:
        logger.error("Failed to fetch klines for %s %s: %s", symbol, interval, exc)
        return None


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    close = df["close"]
    df = df.copy()
    df["ma7"] = close.rolling(window=7).mean()
    df["ma14"] = close.rolling(window=14).mean()
    df["ma28"] = close.rolling(window=28).mean()

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def determine_signal(df: pd.DataFrame) -> Optional[str]:
    if len(df) < 30:
        return None
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    ma7 = latest["ma7"]
    ma14 = latest["ma14"]
    ma28 = latest["ma28"]
    rsi = latest["rsi"]
    prev_ma7 = prev["ma7"]
    prev_ma14 = prev["ma14"]

    if any(pd.isna(v) for v in [ma7, ma14, ma28, rsi, prev_ma7, prev_ma14]):
        return None

    bullish_cross = prev_ma7 <= prev_ma14 and ma7 > ma14
    ma_uptrend = ma7 > ma14 > ma28
    rsi_buy = 40 < rsi < 70

    if (bullish_cross or ma_uptrend) and rsi_buy:
        return "BUY"

    bearish_cross = prev_ma7 >= prev_ma14 and ma7 < ma14
    ma_downtrend = ma7 < ma14 < ma28
    rsi_sell = 30 < rsi < 60

    if (bearish_cross or ma_downtrend) and rsi_sell:
        return "SELL"

    return None


def get_indicator_snapshot(symbol: str, interval: str) -> Optional[dict]:
    df = fetch_klines(symbol, interval)
    if df is None:
        return None
    df = calculate_indicators(df)
    latest = df.iloc[-1]
    if any(pd.isna(latest[c]) for c in ["ma7", "ma14", "ma28", "rsi"]):
        return None
    signal = determine_signal(df)
    return {
        "close": round(latest["close"], 6),
        "rsi": round(latest["rsi"], 1),
        "ma7": round(latest["ma7"], 6),
        "ma14": round(latest["ma14"], 6),
        "ma28": round(latest["ma28"], 6),
        "signal": signal,
    }


def calculate_levels(df: pd.DataFrame, signal: str) -> dict:
    latest = df.iloc[-1]
    recent = df.tail(14)
    entry = latest["close"]

    if signal == "BUY":
        sl = round(recent["low"].min() * 0.995, 6)
        risk = entry - sl
        tp1 = round(entry + risk * 1.5, 6)
        tp2 = round(entry + risk * 3.0, 6)
    else:
        sl = round(recent["high"].max() * 1.005, 6)
        risk = sl - entry
        tp1 = round(entry - risk * 1.5, 6)
        tp2 = round(entry - risk * 3.0, 6)

    return {
        "entry": round(entry, 6),
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rsi": round(latest["rsi"], 2),
        "ma7": round(latest["ma7"], 6),
        "ma14": round(latest["ma14"], 6),
        "ma28": round(latest["ma28"], 6),
    }


def format_price(value: float) -> str:
    if value >= 1000:
        return f"{value:,.2f}"
    elif value >= 1:
        return f"{value:.4f}"
    else:
        return f"{value:.6f}"


def format_confirmed_signal_message(symbol: str, signal: str, levels: dict) -> str:
    direction = "🟢 BUY LONG" if signal == "BUY" else "🔴 SELL SHORT"
    coin = symbol.replace("-USDT", "")
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    return (
        f"<b>{direction} — {coin}/USDT Perp ✅ 5m+15m</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Entry:</b>  <code>{format_price(levels['entry'])}</code>\n"
        f"🛑 <b>Stop Loss:</b>  <code>{format_price(levels['sl'])}</code>\n"
        f"🎯 <b>TP1 (1.5R):</b>  <code>{format_price(levels['tp1'])}</code>\n"
        f"🎯 <b>TP2 (3.0R):</b>  <code>{format_price(levels['tp2'])}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>RSI(14):</b>  {levels['rsi']}\n"
        f"📈 <b>MA7:</b>  <code>{format_price(levels['ma7'])}</code>\n"
        f"📈 <b>MA14:</b>  <code>{format_price(levels['ma14'])}</code>\n"
        f"📈 <b>MA28:</b>  <code>{format_price(levels['ma28'])}</code>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏰ {now}\n"
        f"⚠️ <i>Educational purposes only. Not financial advice.</i>"
    )


async def send_message(bot: Bot, chat_id: str | int, text: str) -> None:
    try:
        await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
        logger.info("Telegram message sent.")
    except Exception as exc:
        logger.error("Failed to send Telegram message: %s", exc)


def get_signal_and_df(symbol: str, interval: str) -> tuple[Optional[str], Optional[pd.DataFrame]]:
    df = fetch_klines(symbol, interval)
    if df is None:
        return None, None
    df = calculate_indicators(df)
    signal = determine_signal(df)
    return signal, df


async def scan_pair_and_alert(bot: Bot, symbol: str, force: bool = False) -> bool:
    sig_5m, df_5m = get_signal_and_df(symbol, "5min")
    await asyncio.sleep(0.3)
    sig_15m, df_15m = get_signal_and_df(symbol, "15min")

    if sig_5m is None or sig_15m is None:
        if sig_5m != sig_15m:
            logger.debug("%s: one timeframe has no signal — skipping", symbol)
        return False

    if sig_5m != sig_15m:
        logger.info("%s: conflict (5m=%s, 15m=%s) — skipped", symbol, sig_5m, sig_15m)
        return False

    agreed_signal = sig_5m
    latest_close = round(df_5m.iloc[-1]["close"], 6)
    last = SIGNAL_HISTORY.get(symbol, {})

    if not force and last.get("signal") == agreed_signal and last.get("close") == latest_close:
        return False

    SIGNAL_HISTORY[symbol] = {"signal": agreed_signal, "close": latest_close}

    levels = calculate_levels(df_5m, agreed_signal)
    message = format_confirmed_signal_message(symbol, agreed_signal, levels)
    logger.info("Confirmed signal: %s %s (5m+15m agree)", agreed_signal, symbol)
    await send_message(bot, TELEGRAM_CHAT_ID, message)
    return True


async def scan_all(bot: Bot, force: bool = False) -> int:
    logger.info("Scanning %d pairs (5m+15m confirmation required)...", len(PAIRS))
    sent = 0
    for symbol in PAIRS:
        try:
            fired = await scan_pair_and_alert(bot, symbol, force=force)
            if fired:
                sent += 1
        except Exception as exc:
            logger.error("Error scanning %s: %s", symbol, exc)
        await asyncio.sleep(0.2)
    logger.info("Scan complete — %d confirmed signal(s) sent.", sent)
    return sent


async def background_scan(context: ContextTypes.DEFAULT_TYPE) -> None:
    await scan_all(context.bot)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    coin_list = " ".join(p.replace("-USDT", "") for p in PAIRS)
    msg = (
        "🤖 <b>Bybit Futures Signal Bot</b>\n\n"
        f"📋 <b>Pairs:</b> {coin_list}\n\n"
        "⏱ <b>Timeframes:</b> 5m and 15m\n"
        "📐 <b>Indicators:</b> MA7, MA14, MA28, RSI(14)\n\n"
        "🔁 Scanning every <b>5 minutes</b> automatically.\n\n"
        "<b>Commands:</b>\n"
        "/status — live snapshot of all 12 pairs\n"
        "/scan — force an immediate scan and send new signals\n"
        "/help — show this message\n\n"
        "⚠️ <i>Educational purposes only. Not financial advice.</i>"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    await update.message.reply_text(
        f"🔍 <b>Forcing full scan now...</b>\n<i>{now}</i>",
        parse_mode=ParseMode.HTML,
    )

    SIGNAL_HISTORY.clear()
    found = await scan_all(context.bot, force=True)

    if found == 0:
        await update.message.reply_text(
            "✅ <b>Scan complete.</b> No confirmed signals right now.\n"
            "<i>A signal requires both 5m and 15m to agree.</i>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            f"✅ <b>Scan complete.</b> Sent <b>{found}</b> confirmed signal(s) (5m+15m agreement).",
            parse_mode=ParseMode.HTML,
        )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    await update.message.reply_text(
        f"⏳ <b>Fetching live data for all pairs...</b>\n<i>{now}</i>",
        parse_mode=ParseMode.HTML,
    )

    signal_icon = {"BUY": "🟢", "SELL": "🔴", None: "⚪"}

    lines_5m: list[str] = []
    lines_15m: list[str] = []

    for symbol in PAIRS:
        coin = symbol.replace("-USDT", "")
        for interval, lines in [("5min", lines_5m), ("15min", lines_15m)]:
            snap = get_indicator_snapshot(symbol, interval)
            if snap is None:
                lines.append(f"  {coin}: ❌ data error")
                continue
            sig = snap["signal"]
            icon = signal_icon.get(sig, "⚪")
            sig_label = sig if sig else "—"
            lines.append(
                f"  {icon} <b>{coin}</b>  "
                f"<code>{format_price(snap['close'])}</code>  "
                f"RSI {snap['rsi']}  "
                f"→ <b>{sig_label}</b>"
            )
        await asyncio.sleep(0.2)

    msg = (
        f"📊 <b>Live Status — {now}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<b>5m Timeframe</b>\n"
        + "\n".join(lines_5m)
        + f"\n━━━━━━━━━━━━━━━━━━\n"
        f"<b>15m Timeframe</b>\n"
        + "\n".join(lines_15m)
        + f"\n━━━━━━━━━━━━━━━━━━\n"
        "🟢 BUY  🔴 SELL  ⚪ Neutral\n"
        "⚠️ <i>Educational purposes only. Not financial advice.</i>"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.HTML)


async def post_init(application: Application) -> None:
    bot = application.bot
    me = await bot.get_me()
    logger.info("Bot started: @%s", me.username)

    coin_list = " ".join(p.replace("-USDT", "") for p in PAIRS)
    startup_msg = (
        "🤖 <b>Bybit Futures Signal Bot Started</b>\n\n"
        f"📋 <b>Pairs:</b> {coin_list}\n\n"
        "⏱ <b>Timeframes:</b> 5m and 15m\n"
        "📐 <b>Indicators:</b> MA7, MA14, MA28, RSI(14)\n\n"
        "🔁 Scanning every <b>5 minutes</b> automatically.\n\n"
        "<b>Commands:</b>\n"
        "/status — live snapshot of all 12 pairs\n"
        "/scan — force an immediate scan and send new signals\n"
        "/help — show this message\n\n"
        "⚠️ <i>Educational purposes only. Not financial advice.</i>"
    )
    await send_message(bot, TELEGRAM_CHAT_ID, startup_msg)

    application.job_queue.run_repeating(
        background_scan,
        interval=SCAN_INTERVAL_SECONDS,
        first=5,
        name="scan_job",
    )


def main() -> None:
    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("scan", cmd_scan))

    logger.info("Starting bot with polling...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

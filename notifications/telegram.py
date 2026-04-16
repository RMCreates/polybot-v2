from __future__ import annotations
import httpx
from config.settings import settings
from observability.logger import get_logger

log = get_logger(__name__)


async def send_alert(text: str) -> None:
    """
    Send a Telegram message to the configured chat.
    Silently does nothing if TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID are not set.

    Setup:
      1. Message @BotFather on Telegram → /newbot → copy token
      2. Message @userinfobot → copy your chat id
      3. Add to .env: TELEGRAM_BOT_TOKEN=... and TELEGRAM_CHAT_ID=...
      IMPORTANT: Send at least one message to your bot first, or Telegram
      will block outgoing messages (bots can't initiate conversations).
    """
    token = settings.telegram_bot_token.get_secret_value()
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        return  # gracefully disabled if not configured

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            )
            if resp.status_code != 200:
                log.warning("telegram_send_failed", extra={
                    "status": resp.status_code,
                    "body": resp.text[:200],
                })
    except Exception as e:
        log.warning("telegram_exception", extra={"error": str(e)})


def trade_placed_message(trade_side: str, question: str, size_usd: float, entry_price: float, edge: float, confidence: float) -> str:
    return (
        f"📊 <b>NEW PAPER TRADE</b>\n"
        f"{trade_side} — <i>{question[:80]}</i>\n"
        f"Size: ${size_usd:.2f} | Entry: {entry_price:.3f} | "
        f"Edge: {edge:+.1%} | Conf: {confidence:.0%}"
    )


def trade_resolved_message(outcome: str, question: str, side: str, pnl_usd: float, entry_price: float, exit_price: float) -> str:
    emoji = "✅" if pnl_usd > 0 else "❌" if pnl_usd < 0 else "➖"
    return (
        f"{emoji} <b>TRADE RESOLVED — {outcome} ${pnl_usd:+.2f}</b>\n"
        f"{side} on <i>{question[:80]}</i>\n"
        f"Entry {entry_price:.3f} → Exit {exit_price:.3f}"
    )

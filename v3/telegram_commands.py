from __future__ import annotations

import html
from typing import Any, Dict, List

import requests

from .storage import get_store
from .settings import settings


def _send(chat_id: str, text: str) -> None:
    if not settings.telegram_bot_token:
        return
    response = requests.post(
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=30,
    )
    response.raise_for_status()


def _top_lines(signals: List[Dict[str, Any]], limit: int = 10) -> str:
    rows = []
    for signal in signals[:limit]:
        icon = "🟢" if signal.get("direction") == "bullish" else "🔴"
        rows.append(f"{icon} <b>{html.escape(str(signal.get('symbol')))}</b> {signal.get('grade')}/{signal.get('score')} — {html.escape(str(signal.get('signal_type')))} [{signal.get('status')}]")
    return "\n".join(rows) or "No signals available."


def handle_update(update: Dict[str, Any]) -> Dict[str, Any]:
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    text = str(message.get("text") or "").strip()
    if not chat_id or not text.startswith("/"):
        return {"handled": False}
    if settings.telegram_chat_id and chat_id != str(settings.telegram_chat_id):
        return {"handled": False, "reason": "unauthorized_chat"}
    command = text.split()[0].split("@")[0].lower()
    store = get_store()
    signals = store.list_signals(limit=200)
    if command in {"/top", "/signals"}:
        reply = "<b>Top Ichimoku V3 signals</b>\n" + _top_lines(signals)
    elif command == "/active":
        active = [row for row in signals if row.get("status") in {"active", "confirmed", "entry_zone"}]
        reply = "<b>Active setups</b>\n" + _top_lines(active)
    elif command == "/performance":
        runs = store.list_backtests(limit=5)
        reply = "<b>Recent backtests</b>\n" + "\n".join(f"{html.escape(str(run.get('symbol')))}: {run.get('summary', {}).get('signals', 0)} signals" for run in runs) if runs else "No backtests stored yet."
    elif command == "/paper":
        state = store.load_paper_state()
        reply = f"<b>Paper portfolio</b>\nEquity: {float(state.get('equity', 0)):,.2f}\nOpen positions: {len(state.get('positions', {}))}\nClosed trades: {len(state.get('closed_trades', []))}"
    elif command == "/status":
        regimes = store.list_regimes(limit=2)
        runs = store.list_scanner_runs(limit=4)
        queue = store.delivery_queue_health()
        run_lines = "\n".join(
            f"{html.escape(str(run.get('market')))} {html.escape(str(run.get('mode')))}: {html.escape(str(run.get('status')))} — {html.escape(str(run.get('completed_at') or run.get('started_at')))}"
            for run in runs
        ) or "No V3.1 run records yet."
        reply = (
            f"<b>Ichimoku V3 status</b>\nSignals stored: {len(signals)}"
            f"\nSupabase: {'enabled' if settings.supabase_enabled else 'local fallback'}"
            f"\nQueue: {int(queue.get('pending', 0))} pending, {int(queue.get('in_progress', 0))} processing, {int(queue.get('failed', 0))} failed"
            f"\nRegimes: " + ", ".join(f"{html.escape(str(r.get('market')))}={html.escape(str(r.get('regime')))}" for r in regimes)
            + f"\n\n<b>Recent runs</b>\n{run_lines}"
        )
    elif command == "/help":
        reply = "<b>Commands</b>\n/status\n/top\n/active\n/performance\n/paper"
    else:
        reply = "Unknown command. Use /help."
    _send(chat_id, reply)
    return {"handled": True, "command": command}

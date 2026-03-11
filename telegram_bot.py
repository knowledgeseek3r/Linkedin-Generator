"""
telegram_bot.py
Telegram Bot daemon — runs permanently on the VPS.

Commands:
  /run      — Start the LinkedIn pipeline (generates post + sends Telegram notification)
  /status   — Show last log entries from today's pipeline log
  /pending  — List posts waiting for image selection
  /help     — Command overview

Callback handler:
  Handles inline button presses (1/2/3) from telegram_notifier.py notifications.
  Looks up the pending post, posts to LinkedIn with the selected image.

Run:
  python telegram_bot.py

On VPS (systemd daemon):
  See deployment instructions in CONTEXT.md
"""

import os
import sys
import json
import asyncio
from datetime import date

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_CHAT_ID_STR = os.getenv("TELEGRAM_CHAT_ID", "")
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
PENDING_FILE = os.path.join(PROJECT_DIR, ".tmp", "pending_posts.json")


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

def _is_authorized(update: Update) -> bool:
    if not ALLOWED_CHAT_ID_STR:
        return False
    try:
        return update.effective_chat.id == int(ALLOWED_CHAT_ID_STR)
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Pending posts helpers
# ---------------------------------------------------------------------------

def _load_pending() -> dict:
    if os.path.exists(PENDING_FILE):
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_pending(data: dict) -> None:
    os.makedirs(os.path.dirname(PENDING_FILE), exist_ok=True)
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    keyword = " ".join(context.args).strip() if context.args else None
    if keyword:
        await update.message.reply_text(f"▶️ Pipeline wird gestartet für Keyword: *{keyword}*...", parse_mode="Markdown")
        cmd = [sys.executable, "main.py", "--keyword", keyword]
    else:
        await update.message.reply_text("▶️ Pipeline wird gestartet...")
        cmd = [sys.executable, "main.py"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=PROJECT_DIR,
        )
        stdout, _ = await proc.communicate()
        output = stdout.decode("utf-8", errors="replace").strip()
        lines = output.splitlines()
        summary = "\n".join(lines[-25:])  # last 25 lines
        status = "✅ Fertig" if proc.returncode == 0 else "❌ Fehler (returncode {})".format(proc.returncode)
        # Telegram message limit: 4096 chars
        msg = f"{status}\n\n```\n{summary[-3500:]}\n```"
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ Fehler beim Starten der Pipeline: {e}")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    log_path = os.path.join(PROJECT_DIR, ".tmp", f"pipeline_{date.today()}.log")
    if not os.path.exists(log_path):
        await update.message.reply_text("Kein Log für heute gefunden.")
        return
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    summary = "".join(lines[-20:])
    await update.message.reply_text(
        f"```\n{summary[-3500:]}\n```", parse_mode="Markdown"
    )


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    pending = _load_pending()
    if not pending:
        await update.message.reply_text("Keine ausstehenden Posts.")
        return
    lines = [f"• {v['post_title']}" for v in pending.values()]
    await update.message.reply_text(
        f"{len(pending)} ausstehende(r) Post(s):\n" + "\n".join(lines)
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        return
    await update.message.reply_text(
        "*LinkedIn Bot — Befehle:*\n\n"
        "/run — Pipeline starten (nächstes Keyword laut Rotation)\n"
        "/run `<keyword>` — Pipeline mit spezifischem Keyword starten (z.B. `/run AI Agents`)\n"
        "/status — Letzten Log-Eintrag anzeigen\n"
        "/pending — Ausstehende Posts auflisten\n"
        "/help — Diese Übersicht",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Callback handler — inline button 1️⃣ / 2️⃣ / 3️⃣
# ---------------------------------------------------------------------------

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    if query.message.chat.id != int(ALLOWED_CHAT_ID_STR or "0"):
        return
    await query.answer()

    # callback_data format: "post:{tracking_id}:{image_index}"
    parts = query.data.split(":")
    if len(parts) != 3 or parts[0] != "post":
        await query.edit_message_text("❌ Unbekanntes Callback-Format.")
        return

    _, tracking_id, idx_str = parts
    idx = int(idx_str)

    pending = _load_pending()
    entry = pending.get(tracking_id)
    if not entry:
        await query.edit_message_text("❌ Post nicht gefunden oder bereits verarbeitet.")
        return

    image_urls = entry.get("image_urls", [])
    selected_image = image_urls[idx] if image_urls and idx < len(image_urls) else None
    img_label = f"Bild {idx + 1}" if selected_image else "ohne Bild"

    await query.edit_message_text(f"⏳ Wird auf LinkedIn gepostet ({img_label})...")

    try:
        # Import here to avoid circular imports and keep startup fast
        import linkedin_poster
        from config_loader import load_config

        config = load_config(os.path.join(PROJECT_DIR, "config.yaml"))
        linkedin_poster.post_to_linkedin(entry["post_body"], selected_image, config)

        # Remove from pending
        del pending[tracking_id]
        _save_pending(pending)

        await query.edit_message_text(
            f"✅ Auf LinkedIn gepostet!\n\n"
            f"*{entry['post_title']}*\n"
            f"{img_label} verwendet.",
            parse_mode="Markdown",
        )
        logger.success(f"LinkedIn post published via Telegram: '{entry['post_title']}' ({img_label})")

    except PermissionError:
        await query.edit_message_text(
            "❌ LinkedIn Token abgelaufen (401).\n"
            "Neuen Token unter developer.linkedin.com generieren und in .env eintragen."
        )
    except Exception as e:
        logger.error(f"Error posting to LinkedIn via Telegram callback: {e}")
        await query.edit_message_text(f"❌ Fehler beim Posten auf LinkedIn:\n{e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set in .env", file=sys.stderr)
        sys.exit(1)
    if not ALLOWED_CHAT_ID_STR:
        print("ERROR: TELEGRAM_CHAT_ID not set in .env", file=sys.stderr)
        sys.exit(1)

    logger.info(f"Starting Telegram bot | authorized chat_id: {ALLOWED_CHAT_ID_STR}")

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(handle_callback, pattern=r"^post:"))

    print("Bot läuft — warte auf Nachrichten...")
    app.run_polling()

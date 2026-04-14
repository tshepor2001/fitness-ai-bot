"""Telegram bot entry point — multi-user with /connect onboarding."""

import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from fitness_ai_bot import config
from fitness_ai_bot.agent import ask
from fitness_ai_bot.credential_store import CredentialStore
from fitness_ai_bot.mcp_client import MCPPool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

store = CredentialStore()
pool: MCPPool  # initialised in post_init

# ── auth ─────────────────────────────────────────────────────────────

_allowed: set[int] | None = None
if config.ALLOWED_USER_IDS:
    _allowed = {int(uid.strip()) for uid in config.ALLOWED_USER_IDS.split(",")}


def _is_authorised(update: Update) -> bool:
    if _allowed is None:
        return True
    return update.effective_user is not None and update.effective_user.id in _allowed


# ── /connect conversation states ─────────────────────────────────────

GARMIN_EMAIL, GARMIN_PASS, TP_EMAIL, TP_PASS = range(4)


async def cmd_connect(update: Update, context) -> int:
    if not _is_authorised(update):
        await update.message.reply_text("Sorry, you're not authorised.")
        return ConversationHandler.END
    await update.message.reply_text(
        "Let's connect your fitness accounts.\n\n"
        "Send your **Garmin Connect email**:",
        parse_mode="Markdown",
    )
    return GARMIN_EMAIL


async def recv_garmin_email(update: Update, context) -> int:
    context.user_data["garmin_email"] = update.message.text.strip()
    await _delete_msg(update)
    await update.message.reply_text("Garmin **password**:", parse_mode="Markdown")
    return GARMIN_PASS


async def recv_garmin_pass(update: Update, context) -> int:
    context.user_data["garmin_password"] = update.message.text.strip()
    await _delete_msg(update)
    await update.message.reply_text("TrainingPeaks **email**:", parse_mode="Markdown")
    return TP_EMAIL


async def recv_tp_email(update: Update, context) -> int:
    context.user_data["tp_username"] = update.message.text.strip()
    await _delete_msg(update)
    await update.message.reply_text("TrainingPeaks **password**:", parse_mode="Markdown")
    return TP_PASS


async def recv_tp_pass(update: Update, context) -> int:
    context.user_data["tp_password"] = update.message.text.strip()
    await _delete_msg(update)

    uid = update.effective_user.id
    creds = {
        "garmin_email": context.user_data.pop("garmin_email"),
        "garmin_password": context.user_data.pop("garmin_password"),
        "tp_username": context.user_data.pop("tp_username"),
        "tp_password": context.user_data.pop("tp_password"),
    }

    await store.save(uid, creds)
    # evict any stale session so next query spawns fresh
    await pool.evict_user(uid)

    await update.message.reply_text(
        "✅ Connected! Your credentials are encrypted and stored.\n\n"
        "Try asking: _How was my run yesterday?_",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def connect_cancel(update: Update, context) -> int:
    context.user_data.clear()
    await update.message.reply_text("Setup cancelled.")
    return ConversationHandler.END


async def _delete_msg(update: Update) -> None:
    """Delete the user's message containing sensitive data."""
    try:
        await update.message.delete()
    except Exception:
        pass  # may lack delete permission in groups


# ── /disconnect ──────────────────────────────────────────────────────

async def cmd_disconnect(update: Update, context) -> None:
    uid = update.effective_user.id
    await pool.evict_user(uid)
    deleted = await store.delete(uid)
    if deleted:
        await update.message.reply_text("🗑️ Your credentials and sessions have been removed.")
    else:
        await update.message.reply_text("You don't have any stored credentials.")


# ── /start ───────────────────────────────────────────────────────────

async def cmd_start(update: Update, context) -> None:
    await update.message.reply_text(
        "Hey! I'm your fitness AI assistant 🏃‍♂️\n\n"
        "Use /connect to link your Garmin account (TrainingPeaks is optional).\n"
        "Then just ask me anything about your training data!\n\n"
        "Commands:\n"
        "/connect — link your accounts\n"
        "/disconnect — remove your data\n"
        "/start — show this message"
    )


# ── message handler ──────────────────────────────────────────────────

async def handle_message(update: Update, context) -> None:
    if not _is_authorised(update):
        await update.message.reply_text("Sorry, you're not authorised to use this bot.")
        return

    uid = update.effective_user.id
    session = await pool.get_session(uid)

    if session is None:
        await update.message.reply_text(
            "You haven't connected your accounts yet.\n"
            "Use /connect to get started."
        )
        return

    question = update.message.text
    logger.info("Question from %s: %s", uid, question[:120])
    await update.message.chat.send_action("typing")

    try:
        answer = await ask(question, session)
    except Exception:
        logger.exception("Agent error for user %d", uid)
        answer = "Something went wrong processing your request. Please try again."

    for i in range(0, len(answer), 4096):
        await update.message.reply_text(answer[i : i + 4096])


# ── lifecycle ────────────────────────────────────────────────────────

async def post_init(app: Application) -> None:
    global pool
    await store.open()
    pool = MCPPool(store)
    await pool.start()
    logger.info("Credential store + MCP pool ready.")


async def post_shutdown(app: Application) -> None:
    await pool.stop()
    await store.close()


def main() -> None:
    app = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    connect_conv = ConversationHandler(
        entry_points=[CommandHandler("connect", cmd_connect)],
        states={
            GARMIN_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_garmin_email)],
            GARMIN_PASS:  [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_garmin_pass)],
            TP_ASK:       [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_tp_ask)],
            TP_EMAIL:     [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_tp_email)],
            TP_PASS:      [MessageHandler(filters.TEXT & ~filters.COMMAND, recv_tp_pass)],
        },
        fallbacks=[CommandHandler("cancel", connect_cancel)],
    )

    app.add_handler(connect_conv)
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("disconnect", cmd_disconnect))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot is starting (polling mode)…")
    app.run_polling()


if __name__ == "__main__":
    main()

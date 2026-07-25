"""
AIVideo1GeneratorBot
A Telegram bot that generates short AI videos from text prompts using Replicate.

Commands:
  /start      - welcome message
  /help       - usage instructions
  /generate   - generate a video from a text prompt, e.g. /generate a cat surfing a wave
  /model      - show which model is currently configured

Environment variables (set these in Railway, not in code):
  TELEGRAM_BOT_TOKEN   - token from @BotFather
  REPLICATE_API_TOKEN  - API token from replicate.com/account/api-tokens
  REPLICATE_MODEL       - e.g. "anotherjesse/zeroscope-v2-xl" or any owner/model
                          on Replicate that accepts a text prompt and returns video.
                          You can swap models anytime without changing code.
"""

import asyncio
import logging
import os

import replicate
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
REPLICATE_API_TOKEN = os.environ.get("REPLICATE_API_TOKEN")
REPLICATE_MODEL = os.environ.get("REPLICATE_MODEL", "anotherjesse/zeroscope-v2-xl")

# In-memory per-user lock so one user can't queue multiple generations at once.
# For multi-instance / production scale, replace with a real job queue (e.g. Redis + RQ).
user_locks: dict[int, bool] = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Welcome to AIVideo1GeneratorBot!\n\n"
        "Send me a text prompt with /generate and I'll create a short AI video for you.\n\n"
        "Example:\n/generate a golden retriever running through a field at sunset\n\n"
        "Type /help for more info."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎬 *How to use*\n\n"
        "/generate <description> — generate a video from text\n"
        "/model — show the current video model\n\n"
        "Generation typically takes 30s–3min depending on the model and prompt.\n"
        "Please wait for the current job to finish before starting a new one.",
        parse_mode="Markdown",
    )


async def show_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(f"Current model: `{REPLICATE_MODEL}`", parse_mode="Markdown")


def _run_replicate(prompt: str) -> str:
    """Blocking call to Replicate — run this inside an executor thread."""
    output = replicate.run(REPLICATE_MODEL, input={"prompt": prompt})
    # Replicate outputs vary by model: sometimes a single URL string,
    # sometimes a list of URLs (frames or file outputs). Normalize to one URL.
    if isinstance(output, list):
        return output[-1]
    return output


async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    prompt = " ".join(context.args) if context.args else None

    if not prompt:
        await update.message.reply_text(
            "Please include a description.\nExample: /generate a spaceship landing on mars"
        )
        return

    if user_locks.get(user_id):
        await update.message.reply_text("⏳ You already have a video generating — please wait for it to finish.")
        return

    if not REPLICATE_API_TOKEN:
        await update.message.reply_text(
            "⚠️ The bot isn't fully configured yet (missing REPLICATE_API_TOKEN). "
            "Ask the bot owner to set it in Railway's environment variables."
        )
        return

    user_locks[user_id] = True
    status_msg = await update.message.reply_text("🎬 Generating your video... this can take a minute or two.")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_VIDEO)

    try:
        loop = asyncio.get_running_loop()
        video_url = await loop.run_in_executor(None, _run_replicate, prompt)

        if not video_url:
            await status_msg.edit_text("❌ Generation finished but returned no video. Try a different prompt.")
            return

        await status_msg.edit_text("✅ Done! Uploading your video...")
        await update.message.reply_video(video=video_url, caption=f"🎥 {prompt}")

    except Exception as exc:  # noqa: BLE001
        logger.exception("Video generation failed")
        await status_msg.edit_text(f"❌ Something went wrong generating that video:\n`{exc}`", parse_mode="Markdown")
    finally:
        user_locks[user_id] = False


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("I didn't understand that. Try /help to see available commands.")


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")

    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("model", show_model))
    application.add_handler(CommandHandler("generate", generate))
    application.add_handler(MessageHandler(filters.COMMAND, unknown))

    logger.info("Bot starting with model=%s", REPLICATE_MODEL)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

import functools
import logging
from telegram import Update
from telegram.constants import ParseMode


def escape_md(text: str) -> str:
    if not text:
        return ""
    chars = r"_*[]()~`>#+-=|{}.!"
    for ch in chars:
        text = text.replace(ch, f"\\{ch}")
    return text


def format_env_vars(vars_dict: dict):
    if not vars_dict:
        return "No environment variables set\\."
    lines = []
    for k, v in vars_dict.items():
        masked = v[:2] + "****" if len(v) > 4 else "****"
        lines.append(f"{escape_md(k)} = `{escape_md(masked)}`")
    return "\n".join(lines)


def safe_handler(func):
    @functools.wraps(func)
    async def wrapper(update: Update, context):
        try:
            from database import get_user

            user = await get_user(update.effective_user.id)
            if user and user.get("is_banned"):
                text = "🚫 You are banned from using this bot\\."
                if update.effective_message:
                    await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
                elif update.callback_query:
                    await update.callback_query.answer(text, show_alert=True)
                return
            return await func(update, context)
        except Exception as e:
            logging.exception(f"Error in {func.__name__}: {e}")
            try:
                msg = f"⚠️ Error: `{escape_md(str(e))}`"
                if update.effective_message:
                    await update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
                elif update.callback_query and update.callback_query.message:
                    await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
            except Exception:
                pass

    return wrapper

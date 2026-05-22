from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from database import create_user
from utils.formatters import escape_md, safe_handler


@safe_handler
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    try:
        await create_user(user.id, user.username or user.first_name)
    except Exception:
        pass
    text = (
        f"🚀 *Welcome to DeployBot*, {escape_md(user.first_name)}\\!\n\n"
        "Deploy your apps directly from Telegram\\.\n"
        "• Upload \\.zip files\n"
        "• Connect GitHub repos\n"
        "• Auto\\-detect project type\n"
        "• Manage env vars, logs, backups\n\n"
        "Choose an option below:"
    )
    keyboard = [
        [
            InlineKeyboardButton("📁 My Projects", callback_data="menu_projects"),
            InlineKeyboardButton("📤 Upload", callback_data="menu_upload"),
        ],
        [
            InlineKeyboardButton("🐙 GitHub", callback_data="menu_github"),
            InlineKeyboardButton("❓ Help", callback_data="menu_help"),
        ],
    ]
    await update.message.reply_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
    )


@safe_handler
async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "*📖 Help \\- Command Reference*\n\n"
        "*Deploy*\n"
        "/upload \\- Upload a \\.zip project\n"
        "/smartdeploy \\- Auto detect \\& deploy\n"
        "/template \\- Deploy from a template\n\n"
        "*GitHub*\n"
        "/connect \\- Link GitHub account\n"
        "/disconnect \\- Unlink GitHub\n"
        "/repos \\- List your repositories\n"
        "/github \\<<url\\> \\[branch\\] \\- Deploy from GitHub\n\n"
        "*Manage*\n"
        "/projects \\- List your projects\n"
        "/status \\- Live stats for all projects\n"
        "/logs \\<<name\\> \\- View container logs\n"
        "/stop \\<<name\\> \\- Stop a project\n"
        "/update \\<<name\\> \\- Re\\-clone \\& rebuild\n\n"
        "*Advanced*\n"
        "/env \\<<name\\> \\- Manage env vars\n"
        "/envfile \\<<name\\> \\- Upload \\.env file\n"
        "/pip \\<<name\\> \\<<packages\\> \\- Install packages\n"
        "/exec \\<<name\\> \\<<cmd\\> \\- Run command\n"
        "/replace \\<<name\\> \\- Replace file\n"
        "/files \\<<name\\> \\[path\\] \\- Browse files\n"
        "/backup \\<<name\\> \\- Download backup"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


@safe_handler
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "menu_projects":
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Use /projects to view your projects\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    elif data == "menu_upload":
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Use /upload to start uploading a project\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    elif data == "menu_github":
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Use /connect to link GitHub or /repos to list repos\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    elif data == "menu_help":
        await help_handler(update, context)


def get_handlers():
    return [
        CommandHandler("start", start_handler),
        CommandHandler("help", help_handler),
        CallbackQueryHandler(menu_callback, pattern=r"^menu_"),
    ]

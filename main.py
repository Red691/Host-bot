import logging
import os
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)
from config import Config
from database import init_db
from handlers.start import get_handlers as start_handlers
from handlers.projects import projects_handler, project_menu_callback
from handlers.github import (
    github_handler,
    repos_handler,
    connect_handler,
    connect_token_received,
    disconnect_handler,
    repos_callback,
)
from handlers.deploy import (
    upload_handler,
    upload_zip_received,
    smartdeploy_handler,
    template_handler,
    template_callback,
)
from handlers.tools import (
    logs_handler,
    stop_handler,
    status_handler,
    pip_handler,
    update_handler,
    exec_handler,
    env_handler,
    envfile_handler,
    backup_handler,
    files_handler,
    env_add_callback,
    env_key_received,
    env_value_received,
    envfile_received,
    env_delete_menu_callback,
    env_delete_callback,
    files_callback_handler,
    replace_handler,
    replace_path_received,
    replace_file_received,
    WAITING_FOR_ENV_KEY,
    WAITING_FOR_ENV_VALUE,
    WAITING_FOR_ENVFILE,
    WAITING_FOR_REPLACE_PATH,
    WAITING_FOR_REPLACE_FILE,
)

logging.basicConfig(level=getattr(logging, Config.LOG_LEVEL))
logger = logging.getLogger(__name__)


async def error_handler(update: Update, context):
    logger.error(f"Exception while handling an update: {context.error}")
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "An unexpected error occurred\\. Please try again\\.", parse_mode="MarkdownV2"
        )


async def post_init(app):
    await init_db()
    for d in [Config.UPLOAD_DIR, Config.PROJECTS_DIR, Config.BACKUP_DIR]:
        os.makedirs(d, exist_ok=True)


def main():
    app = ApplicationBuilder().token(Config.BOT_TOKEN).post_init(post_init).build()

    # Command handlers
    for handler in start_handlers():
        app.add_handler(handler)
    app.add_handler(CommandHandler("projects", projects_handler))
    app.add_handler(CommandHandler("github", github_handler))
    app.add_handler(CommandHandler("repos", repos_handler))
    app.add_handler(CommandHandler("disconnect", disconnect_handler))
    app.add_handler(CommandHandler("logs", logs_handler))
    app.add_handler(CommandHandler("stop", stop_handler))
    app.add_handler(CommandHandler("status", status_handler))
    app.add_handler(CommandHandler("pip", pip_handler))
    app.add_handler(CommandHandler("update", update_handler))
    app.add_handler(CommandHandler("exec", exec_handler))
    app.add_handler(CommandHandler("env", env_handler))
    app.add_handler(CommandHandler("envfile", envfile_handler))
    app.add_handler(CommandHandler("backup", backup_handler))
    app.add_handler(CommandHandler("files", files_handler))
    app.add_handler(CommandHandler("smartdeploy", smartdeploy_handler))
    app.add_handler(CommandHandler("template", template_handler))

    # Conversation handlers
    upload_conv = ConversationHandler(
        entry_points=[CommandHandler("upload", upload_handler)],
        states={0: [MessageHandler(filters.Document.ZIP, upload_zip_received)]},
        fallbacks=[MessageHandler(filters.TEXT, lambda u, c: u.message.reply_text("Please send a .zip file."))],
    )
    app.add_handler(upload_conv)

    smartdeploy_conv = ConversationHandler(
        entry_points=[CommandHandler("smartdeploy", smartdeploy_handler)],
        states={0: [MessageHandler(filters.Document.ZIP, upload_zip_received)]},
        fallbacks=[],
    )
    app.add_handler(smartdeploy_conv)

    connect_conv = ConversationHandler(
        entry_points=[CommandHandler("connect", connect_handler)],
        states={0: [MessageHandler(filters.TEXT & ~filters.COMMAND, connect_token_received)]},
        fallbacks=[],
    )
    app.add_handler(connect_conv)

    replace_conv = ConversationHandler(
        entry_points=[CommandHandler("replace", replace_handler)],
        states={
            WAITING_FOR_REPLACE_PATH: [MessageHandler(filters.TEXT & ~filters.COMMAND, replace_path_received)],
            WAITING_FOR_REPLACE_FILE: [MessageHandler(filters.Document.ALL, replace_file_received)],
        },
        fallbacks=[],
    )
    app.add_handler(replace_conv)

    env_add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(env_add_callback, pattern=r"^ead:")],
        states={
            WAITING_FOR_ENV_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, env_key_received)],
            WAITING_FOR_ENV_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, env_value_received)],
        },
        fallbacks=[],
    )
    app.add_handler(env_add_conv)

    envfile_conv = ConversationHandler(
        entry_points=[CommandHandler("envfile", envfile_handler)],
        states={0: [MessageHandler(filters.Document.ALL, envfile_received)]},
        fallbacks=[],
    )
    app.add_handler(envfile_conv)

    # Callback handlers
    app.add_handler(CallbackQueryHandler(project_menu_callback, pattern=r"^(sel|log|stop|run|del|bak|fil|env|sta|upd):"))
    app.add_handler(CallbackQueryHandler(repos_callback, pattern=r"^repo_(p|d):"))
    app.add_handler(CallbackQueryHandler(template_callback, pattern=r"^tpl:"))
    app.add_handler(CallbackQueryHandler(files_callback_handler, pattern=r"^(fil|fup|fdr|fdo):"))
    app.add_handler(CallbackQueryHandler(env_delete_menu_callback, pattern=r"^edl:"))
    app.add_handler(CallbackQueryHandler(env_delete_callback, pattern=r"^evd:"))

    app.add_error_handler(error_handler)
    app.run_polling()


if __name__ == "__main__":
    main()

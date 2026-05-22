import io
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from database import get_user_projects, get_project, delete_project, update_project_status
from docker_manager import DockerManager
from utils.formatters import escape_md, safe_handler
from handlers.tools import _send_logs, _stop_project, _do_update, _list_files, _show_env_menu, _reply_or_edit

docker = DockerManager()


@safe_handler
async def projects_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    projects = await get_user_projects(user_id)
    if not projects:
        await update.message.reply_text(
            "You have no projects\\. Use /upload or /github to deploy\\.", parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    keyboard = []
    for p in projects:
        status = "🟢" if p["status"] == "running" else "🔴"
        keyboard.append([InlineKeyboardButton(f"{status} {p['name']}", callback_data=f"sel:{p['project_id']}")])
    await update.message.reply_text(
        "*📁 Your Projects*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
    )


@safe_handler
async def project_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id

    if data.startswith("sel:"):
        pid = data.split(":")[1]
        project = await get_project(pid)
        if not project or project["user_id"] != user_id:
            await query.edit_message_text("Project not found or access denied\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        status = "🟢 Running" if project["status"] == "running" else "🔴 Stopped"
        text = (
            f"*{escape_md(project['name'])}*\n"
            f"Status: {status}\n"
            f"Port: `{project['port']}`\n"
            f"ID: `{project['project_id'][:8]}`"
        )
        keyboard = [
            [
                InlineKeyboardButton("📋 Logs", callback_data=f"log:{pid}"),
                InlineKeyboardButton("⏹ Stop", callback_data=f"stop:{pid}"),
                InlineKeyboardButton("▶️ Start", callback_data=f"run:{pid}"),
            ],
            [
                InlineKeyboardButton("🗑 Delete", callback_data=f"del:{pid}"),
                InlineKeyboardButton("💾 Backup", callback_data=f"bak:{pid}"),
                InlineKeyboardButton("📁 Files", callback_data=f"fil:{pid}"),
            ],
            [
                InlineKeyboardButton("⚙️ Env", callback_data=f"env:{pid}"),
                InlineKeyboardButton("🔄 Update", callback_data=f"upd:{pid}"),
                InlineKeyboardButton("📊 Status", callback_data=f"sta:{pid}"),
            ],
        ]
        await query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )

    elif data.startswith("log:"):
        pid = data.split(":")[1]
        await _send_logs(update, context, pid)

    elif data.startswith("stop:"):
        pid = data.split(":")[1]
        await _stop_project(update, context, pid)

    elif data.startswith("run:"):
        pid = data.split(":")[1]
        project = await get_project(pid)
        if not project:
            return

        async def _start_container():
            try:
                c = docker.client.containers.get(project["container_id"])
                c.start()
                return True
            except Exception:
                return False

        ok = await _start_container()
        if ok:
            await update_project_status(pid, "running")
            text = f"▶️ *{escape_md(project['name'])}* started\\."
        else:
            text = f"Failed to start *{escape_md(project['name'])}*\\. Container may have been removed\\. Use /update to rebuild\\."
        await _reply_or_edit(update, context, text)

    elif data.startswith("del:"):
        pid = data.split(":")[1]
        project = await get_project(pid)
        if not project:
            return
        await docker.stop_container(project["container_id"])
        await docker.remove_container(project["container_id"])
        await delete_project(pid)
        await query.edit_message_text(f"🗑 *{escape_md(project['name'])}* deleted\\.", parse_mode=ParseMode.MARKDOWN_V2)

    elif data.startswith("bak:"):
        pid = data.split(":")[1]
        project = await get_project(pid)
        if not project:
            return
        await query.message.reply_text(
            f"Creating backup for *{escape_md(project['name'])}*\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2
        )
        zip_data = await docker.backup_container(project["container_id"], project["name"])
        if zip_data:
            bio = io.BytesIO(zip_data)
            bio.name = f"{project['name']}_backup.zip"
            size_kb = len(zip_data) / 1024
            await query.message.reply_document(
                bio,
                caption=f"Backup of *{escape_md(project['name'])}* \\({size_kb:.1f} KB\\)",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        else:
            await query.edit_message_text("Backup failed\\.", parse_mode=ParseMode.MARKDOWN_V2)

    elif data.startswith("fil:"):
        pid = data.split(":")[1]
        context.user_data.setdefault("file_paths", {})[pid] = "/app"
        await _list_files(update, context, pid, "/app")

    elif data.startswith("env:"):
        pid = data.split(":")[1]
        await _show_env_menu(update, context, pid)

    elif data.startswith("sta:"):
        pid = data.split(":")[1]
        project = await get_project(pid)
        if not project:
            return
        stats = await docker.get_container_stats(project["container_id"])
        text = (
            f"📊 *{escape_md(project['name'])}*\n"
            f"Status: {'🟢 Running' if project['status']=='running' else '🔴 Stopped'}\n"
            f"CPU: {stats['cpu_percent']}% | RAM: {stats['ram_mb']}MB / {stats['ram_limit_mb']}MB\n"
            f"Uptime: {escape_md(stats['uptime'])}"
        )
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN_V2)

    elif data.startswith("upd:"):
        pid = data.split(":")[1]
        project = await get_project(pid)
        if project:
            await _do_update(update, context, project)

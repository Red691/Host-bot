import io
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, CallbackQueryHandler
from database import (
    get_user_projects,
    get_project,
    get_env_vars,
    set_env_vars,
    delete_env_var,
    delete_project,
    update_project_status,
    update_project_container_id,
    check_rate_limit,
    update_deploy_count,
)
from docker_manager import DockerManager
from utils.formatters import escape_md, format_env_vars, safe_handler

docker = DockerManager()

WAITING_FOR_ENV_KEY, WAITING_FOR_ENV_VALUE = range(2)
WAITING_FOR_ENVFILE = range(1)
WAITING_FOR_REPLACE_PATH, WAITING_FOR_REPLACE_FILE = range(2)


# ─── Helpers ───

async def _find_project_by_name(user_id: int, name: str):
    projects = await get_user_projects(user_id)
    name_lower = name.lower()
    for p in projects:
        if p["name"].lower() == name_lower:
            return p
    return None


async def _send_project_selector(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str):
    user_id = update.effective_user.id
    projects = await get_user_projects(user_id)
    if not projects:
        text = "You have no projects\\. Create one with /upload or /github\\."
        if update.callback_query:
            await update.callback_query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
        return None
    keyboard = []
    for p in projects:
        status = "🟢" if p["status"] == "running" else "🔴"
        keyboard.append(
            [InlineKeyboardButton(f"{status} {p['name']}", callback_data=f"{action}:{p['project_id']}")]
        )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            "Select a project:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )
    else:
        await update.message.reply_text(
            "Select a project:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
        )
    return None


async def _reply_or_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None):
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup
        )
    elif update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup)
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text=text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=reply_markup
        )


async def _send_logs(update: Update, context: ContextTypes.DEFAULT_TYPE, project_id: str):
    project = await get_project(project_id)
    if not project:
        await _reply_or_edit(update, context, "Project not found\\.")
        return
    logs_text = await docker.get_container_logs(project["container_id"], tail=50)
    if len(logs_text) > 4000:
        logs_text = logs_text[-4000:]
    logs_text = logs_text.replace("```", "``")
    text = f"*📋 Logs for {escape_md(project['name'])}*\n```\n{logs_text}\n```"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data=f"log:{project_id}")]])
    await _reply_or_edit(update, context, text, keyboard)


async def _stop_project(update: Update, context: ContextTypes.DEFAULT_TYPE, project_id: str):
    project = await get_project(project_id)
    if not project:
        text = "Project not found\\."
    else:
        success = await docker.stop_container(project["container_id"])
        if success:
            await update_project_status(project_id, "stopped")
            text = f"⏹ *{escape_md(project['name'])}* stopped\\."
        else:
            text = f"Failed to stop *{escape_md(project['name'])}*\\. Container may already be stopped\\."
    await _reply_or_edit(update, context, text)


async def _do_update(update: Update, context: ContextTypes.DEFAULT_TYPE, project: dict):
    await _reply_or_edit(update, context, f"🔄 Updating *{escape_md(project['name'])}*\\.\\.\\.")
    await docker.stop_container(project["container_id"])
    await docker.remove_container(project["container_id"])

    from github_client import clone_repo
    from security import scan_files
    from utils.smart_deploy import detect_project_type, generate_dockerfile
    import tempfile, shutil
    from config import Config
    from database import get_github_token

    temp_dir = tempfile.mkdtemp(dir=Config.UPLOAD_DIR)
    try:
        token = await get_github_token(project["user_id"])
        ok = await clone_repo(project["github_repo"], token, project.get("github_branch", "main"), temp_dir)
        if not ok:
            await _reply_or_edit(update, context, "Failed to re\\-clone repository\\.")
            return
        safe, reason = scan_files(temp_dir)
        if not safe:
            await _reply_or_edit(update, context, f"Security scan failed: {escape_md(reason)}")
            return
        df_path = os.path.join(temp_dir, "Dockerfile")
        if not os.path.exists(df_path):
            ptype = detect_project_type(temp_dir)
            df = generate_dockerfile(ptype, temp_dir)
            with open(df_path, "w") as f:
                f.write(df)
        await _reply_or_edit(update, context, "🔨 Building image\\.\\.\\.")
        image = await docker.build_image(project["project_id"], temp_dir)
        env_vars = await get_env_vars(project["project_id"]) or {}
        await _reply_or_edit(update, context, "🚀 Starting container\\.\\.\\.")
        container = await docker.run_container(
            project["project_id"], f"deploybot/{project['project_id']}", env_vars, project["port"]
        )
        await update_project_status(project["project_id"], "running")
        await update_project_container_id(project["project_id"], container.id)
        await _reply_or_edit(update, context, f"✅ *{escape_md(project['name'])}* updated successfully\\.")
    except Exception as e:
        await _reply_or_edit(update, context, f"Update error: `{escape_md(str(e))}`")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def _list_files(update: Update, context: ContextTypes.DEFAULT_TYPE, project_id: str, path: str):
    project = await get_project(project_id)
    if not project:
        text = "Project not found\\."
        keyboard = None
    else:
        files = await docker.get_container_files(project["container_id"], path)
        text = f"*📁 {escape_md(project['name'])}:* `{escape_md(path)}`\n"
        if not files:
            text += "_Empty directory_"
        keyboard = []
        for f in files:
            fname = f["name"]
            if f["type"] == "folder":
                keyboard.append(
                    [InlineKeyboardButton(f"📁 {fname}", callback_data=f"fdr:{project_id}:{fname}")]
                )
            else:
                keyboard.append(
                    [InlineKeyboardButton(f"📄 {fname} ({f['size']})", callback_data=f"fdo:{project_id}:{fname}")]
                )
        if path != "/app":
            keyboard.append([InlineKeyboardButton("⬆️ Parent Dir", callback_data=f"fup:{project_id}")])
    await _reply_or_edit(update, context, text, InlineKeyboardMarkup(keyboard) if keyboard else None)


async def _show_env_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, project_id: str):
    project = await get_project(project_id)
    vars_dict = await get_env_vars(project_id) or {}
    text = f"*⚙️ Environment Variables for {escape_md(project['name'])}*\n\n{format_env_vars(vars_dict)}"
    keyboard = [
        [
            InlineKeyboardButton("➕ Add Variable", callback_data=f"ead:{project_id}"),
            InlineKeyboardButton("🗑 Delete", callback_data=f"edl:{project_id}"),
        ]
    ]
    await _reply_or_edit(update, context, text, InlineKeyboardMarkup(keyboard))


# ─── Command Handlers ───

@safe_handler
async def logs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if context.args:
        project = await _find_project_by_name(user_id, context.args[0])
        if not project:
            await update.message.reply_text("Project not found\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        await _send_logs(update, context, project["project_id"])
    else:
        await _send_project_selector(update, context, "log")


@safe_handler
async def stop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if context.args:
        project = await _find_project_by_name(user_id, context.args[0])
        if not project:
            await update.message.reply_text("Project not found\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        await _stop_project(update, context, project["project_id"])
    else:
        await _send_project_selector(update, context, "stop")


@safe_handler
async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    projects = await get_user_projects(user_id)
    if not projects:
        await update.message.reply_text("No projects found\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    lines = ["*📊 Project Status*\n"]
    for p in projects:
        stats = await docker.get_container_stats(p["container_id"])
        status_icon = "🟢" if p["status"] == "running" else "🔴"
        lines.append(
            f"{status_icon} *{escape_md(p['name'])}*\n"
            f"CPU: {stats['cpu_percent']}% | RAM: {stats['ram_mb']}MB / {stats['ram_limit_mb']}MB\n"
            f"Uptime: {escape_md(stats['uptime'])}\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)


@safe_handler
async def pip_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /pip \\<<project_name\\> \\<<packages\\>", parse_mode=ParseMode.MARKDOWN_V2)
        return
    name = context.args[0]
    packages = " ".join(context.args[1:])
    project = await _find_project_by_name(user_id, name)
    if not project:
        await update.message.reply_text("Project not found\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    await update.message.reply_text(
        f"Installing `{escape_md(packages)}` in *{escape_md(name)}*\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2
    )
    result = await docker.install_pip_packages(project["container_id"], packages)
    output = result["output"]
    if len(output) > 4000:
        output = output[:4000] + "\n...truncated"
    output = output.replace("```", "``")
    text = f"*📦 pip install result*\nExit code: {result['exit_code']}\n```\n{output}\n```"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


@safe_handler
async def update_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await _send_project_selector(update, context, "upd")
        return
    name = context.args[0]
    project = await _find_project_by_name(user_id, name)
    if not project:
        await update.message.reply_text("Project not found\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    if not project.get("github_repo"):
        await update.message.reply_text("This project was not deployed from GitHub\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    await _do_update(update, context, project)


@safe_handler
async def exec_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /exec \\<<project_name\\> \\<<command\\>", parse_mode=ParseMode.MARKDOWN_V2)
        return
    name = context.args[0]
    command = " ".join(context.args[1:])
    project = await _find_project_by_name(user_id, name)
    if not project:
        await update.message.reply_text("Project not found\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    result = await docker.exec_in_container(project["container_id"], command)
    output = result["output"]
    if len(output) > 4000:
        output = output[:4000] + "\n...truncated"
    output = output.replace("```", "``")
    text = f"*⚙️ Exec result*\nExit code: {result['exit_code']}\n```\n{output}\n```"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


@safe_handler
async def env_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if context.args:
        project = await _find_project_by_name(user_id, context.args[0])
        if not project:
            await update.message.reply_text("Project not found\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        await _show_env_menu(update, context, project["project_id"])
    else:
        await _send_project_selector(update, context, "env")


@safe_handler
async def envfile_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text(
            "Usage: /envfile \\<<project_name\\> then upload a \\.env file\\.", parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    name = context.args[0]
    project = await _find_project_by_name(user_id, name)
    if not project:
        await update.message.reply_text("Project not found\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    context.user_data["envfile_project_id"] = project["project_id"]
    await update.message.reply_text("Send me the \\.env file\\.", parse_mode=ParseMode.MARKDOWN_V2)
    return WAITING_FOR_ENVFILE


async def envfile_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        await update.message.reply_text("Please send a file\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return WAITING_FOR_ENVFILE
    pid = context.user_data.get("envfile_project_id")
    if not pid:
        await update.message.reply_text("Session expired\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return -1
    file = await update.message.document.get_file()
    content = await file.download_as_bytearray()
    text = content.decode("utf-8", errors="replace")
    vars_dict = {}
    count = 0
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            vars_dict[k] = v
            count += 1
    await set_env_vars(pid, vars_dict)
    project = await get_project(pid)
    if project:
        all_vars = await get_env_vars(pid) or {}
        env_content = "\n".join([f"{k}={v}" for k, v in all_vars.items()])
        try:
            await docker.copy_file_to_container(project["container_id"], env_content.encode(), "/app/.env")
        except Exception:
            pass
    await update.message.reply_text(f"Imported {count} variables\\.", parse_mode=ParseMode.MARKDOWN_V2)
    return -1


@safe_handler
async def backup_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if context.args:
        project = await _find_project_by_name(user_id, context.args[0])
        if not project:
            await update.message.reply_text("Project not found\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        await update.message.reply_text(
            f"Creating backup for *{escape_md(project['name'])}*\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2
        )
        zip_data = await docker.backup_container(project["container_id"], project["name"])
        if not zip_data:
            await update.message.reply_text("Backup failed\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        bio = io.BytesIO(zip_data)
        bio.name = f"{project['name']}_backup.zip"
        size_kb = len(zip_data) / 1024
        await update.message.reply_document(
            bio,
            caption=f"Backup of *{escape_md(project['name'])}* \\({size_kb:.1f} KB\\)",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    else:
        await _send_project_selector(update, context, "bak")


@safe_handler
async def files_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args or []
    if not args:
        await _send_project_selector(update, context, "fil")
        return
    name = args[0]
    path = args[1] if len(args) > 1 else "/app"
    project = await _find_project_by_name(user_id, name)
    if not project:
        await update.message.reply_text("Project not found\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    context.user_data.setdefault("file_paths", {})[project["project_id"]] = path
    await _list_files(update, context, project["project_id"], path)


# ─── Callback Handlers ───

@safe_handler
async def files_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    if data.startswith("fil:"):
        pid = data.split(":")[1]
        context.user_data.setdefault("file_paths", {})[pid] = "/app"
        await _list_files(update, context, pid, "/app")
    elif data.startswith("fup:"):
        pid = data.split(":")[1]
        current = context.user_data.get("file_paths", {}).get(pid, "/app")
        parent = os.path.dirname(current.rstrip("/"))
        if not parent or parent == current:
            parent = "/app"
        context.user_data["file_paths"][pid] = parent
        await _list_files(update, context, pid, parent)
    elif data.startswith("fdr:"):
        _, pid, fname = data.split(":", 2)
        current = context.user_data.get("file_paths", {}).get(pid, "/app")
        new_path = os.path.join(current, fname)
        context.user_data["file_paths"][pid] = new_path
        await _list_files(update, context, pid, new_path)
    elif data.startswith("fdo:"):
        _, pid, fname = data.split(":", 2)
        current = context.user_data.get("file_paths", {}).get(pid, "/app")
        file_path = os.path.join(current, fname)
        project = await get_project(pid)
        if not project or project["user_id"] != user_id:
            await query.edit_message_text("Access denied\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        file_bytes = await docker.download_file_from_container(project["container_id"], file_path)
        if file_bytes:
            bio = io.BytesIO(file_bytes)
            bio.name = fname
            await query.message.reply_document(
                bio, caption=f"`{escape_md(file_path)}`", parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            await query.edit_message_text("Failed to download file\\.", parse_mode=ParseMode.MARKDOWN_V2)


@safe_handler
async def env_add_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pid = query.data.split(":")[1]
    context.user_data["env_project_id"] = pid
    await query.edit_message_text("Send me the variable *KEY*:", parse_mode=ParseMode.MARKDOWN_V2)
    return WAITING_FOR_ENV_KEY


async def env_key_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["env_key"] = update.message.text.strip()
    await update.message.reply_text("Now send me the *VALUE*:", parse_mode=ParseMode.MARKDOWN_V2)
    return WAITING_FOR_ENV_VALUE


async def env_value_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data.get("env_key")
    pid = context.user_data.get("env_project_id")
    value = update.message.text.strip()
    if not key or not pid:
        await update.message.reply_text("Session expired\\. Start again\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return -1
    vars_dict = await get_env_vars(pid) or {}
    vars_dict[key] = value
    await set_env_vars(pid, vars_dict)
    project = await get_project(pid)
    if project:
        all_vars = await get_env_vars(pid) or {}
        env_content = "\n".join([f"{k}={v}" for k, v in all_vars.items()])
        try:
            await docker.copy_file_to_container(project["container_id"], env_content.encode(), "/app/.env")
        except Exception:
            pass
    await update.message.reply_text(f"Variable `{escape_md(key)}` saved\\.", parse_mode=ParseMode.MARKDOWN_V2)
    return -1


@safe_handler
async def env_delete_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pid = query.data.split(":")[1]
    vars_dict = await get_env_vars(pid) or {}
    keyboard = []
    for k in vars_dict.keys():
        keyboard.append([InlineKeyboardButton(f"🗑 {k[:30]}", callback_data=f"evd:{pid}:{k}")])
    if not keyboard:
        keyboard.append([InlineKeyboardButton("Back", callback_data=f"env:{pid}")])
    await query.edit_message_text(
        "Select variable to delete:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
    )


@safe_handler
async def env_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":", 2)
    pid = parts[1]
    key = parts[2]
    await delete_env_var(pid, key)
    await query.edit_message_text(f"Variable `{escape_md(key)}` deleted\\.", parse_mode=ParseMode.MARKDOWN_V2)


# ─── Replace Conversation ───

@safe_handler
async def replace_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usage: /replace \\<<project_name\\>", parse_mode=ParseMode.MARKDOWN_V2)
        return -1
    name = context.args[0]
    project = await _find_project_by_name(user_id, name)
    if not project:
        await update.message.reply_text("Project not found\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return -1
    context.user_data["replace_project_id"] = project["project_id"]
    await update.message.reply_text(
        "Send me the *target file path* inside the container \\(e\\.g\\. `/app/main.py`\\):",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return WAITING_FOR_REPLACE_PATH


async def replace_path_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["replace_path"] = update.message.text.strip()
    await update.message.reply_text("Now send me the new file\\.", parse_mode=ParseMode.MARKDOWN_V2)
    return WAITING_FOR_REPLACE_FILE


async def replace_file_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        await update.message.reply_text("Please send a file\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return WAITING_FOR_REPLACE_FILE
    pid = context.user_data.get("replace_project_id")
    path = context.user_data.get("replace_path")
    if not pid or not path:
        await update.message.reply_text("Session expired\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return -1
    file = await update.message.document.get_file()
    content = await file.download_as_bytearray()
    project = await get_project(pid)
    if project:
        await docker.copy_file_to_container(project["container_id"], bytes(content), path)
        await update.message.reply_text(f"File replaced at `{escape_md(path)}`\\.", parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await update.message.reply_text("Project not found\\.", parse_mode=ParseMode.MARKDOWN_V2)
    return -1

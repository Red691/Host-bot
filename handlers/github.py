import os
import shutil
import tempfile
import uuid
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, CommandHandler, ConversationHandler, MessageHandler, filters, CallbackQueryHandler
from database import (
    get_github_token,
    save_github_token,
    delete_github_token,
    count_user_projects,
    create_project,
    update_project_status,
    update_project_github,
    update_deploy_count,
    check_rate_limit,
)
from github_client import validate_token, list_repos, clone_repo
from security import scan_files, encrypt_token
from docker_manager import DockerManager
from utils.smart_deploy import detect_project_type, generate_dockerfile
from utils.formatters import escape_md, safe_handler
from config import Config

docker = DockerManager()
WAITING_FOR_GITHUB_TOKEN = range(1)


async def _deploy_from_github(update: Update, context: ContextTypes.DEFAULT_TYPE, repo_url: str, branch: str):
    user_id = update.effective_user.id
    token = await get_github_token(user_id)
    if await count_user_projects(user_id) >= Config.MAX_PROJECTS_PER_USER:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="Max projects limit reached\\.", parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    if not await check_rate_limit(user_id):
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="Rate limit exceeded\\.", parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    temp_dir = tempfile.mkdtemp(dir=Config.UPLOAD_DIR)
    try:
        ok = await clone_repo(repo_url, token, branch, temp_dir)
        if not ok:
            await context.bot.send_message(
                chat_id=update.effective_chat.id, text="Clone failed\\.", parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        safe, reason = scan_files(temp_dir)
        if not safe:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"Security scan failed: {escape_md(reason)}",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return
        df_path = os.path.join(temp_dir, "Dockerfile")
        if not os.path.exists(df_path):
            ptype = detect_project_type(temp_dir)
            df = generate_dockerfile(ptype, temp_dir)
            with open(df_path, "w") as f:
                f.write(df)
        pid = str(uuid.uuid4())
        image = await docker.build_image(pid, temp_dir)
        port = Config.BASE_PORT + (hash(pid) % (Config.MAX_PORT - Config.BASE_PORT))
        env_vars = {}
        container = await docker.run_container(pid, f"deploybot/{pid}", env_vars, port)
        await create_project(user_id, os.path.basename(repo_url).replace(".git", ""), container.id, port)
        await update_project_github(pid, repo_url, branch)
        await update_project_status(pid, "running")
        await update_deploy_count(user_id)
        text = (
            f"✅ *Deployed from GitHub*\n"
            f"Name: `{escape_md(os.path.basename(repo_url).replace('.git',''))}`\n"
            f"ID: `{pid[:8]}`\n"
            f"Status: 🟢 Running\n"
            f"Port: `{port}`"
        )
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text=text, parse_mode=ParseMode.MARKDOWN_V2
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@safe_handler
async def github_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if len(context.args) < 1:
        await update.message.reply_text(
            "Usage: /github \\<<repo_url\\> \\[branch\\]", parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    repo_url = context.args[0]
    branch = context.args[1] if len(context.args) > 1 else "main"
    token = await get_github_token(user_id)
    if await count_user_projects(user_id) >= Config.MAX_PROJECTS_PER_USER:
        await update.message.reply_text("Max projects limit reached\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    if not await check_rate_limit(user_id):
        await update.message.reply_text("Rate limit exceeded\\. Try again later\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    await update.message.reply_text("🐙 Cloning repository\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)
    temp_dir = tempfile.mkdtemp(dir=Config.UPLOAD_DIR)
    try:
        ok = await clone_repo(repo_url, token, branch, temp_dir)
        if not ok:
            await update.message.reply_text(
                "Failed to clone repository\\. Check URL and token\\.", parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        safe, reason = scan_files(temp_dir)
        if not safe:
            await update.message.reply_text(
                f"🛡 Security scan failed: {escape_md(reason)}", parse_mode=ParseMode.MARKDOWN_V2
            )
            return
        df_path = os.path.join(temp_dir, "Dockerfile")
        if not os.path.exists(df_path):
            ptype = detect_project_type(temp_dir)
            df = generate_dockerfile(ptype, temp_dir)
            with open(df_path, "w") as f:
                f.write(df)
            await update.message.reply_text(
                f"🔍 Detected *{escape_md(ptype)}* project\\. Generated Dockerfile\\.",
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        pid = str(uuid.uuid4())
        await update.message.reply_text("🔨 Building image\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)
        image = await docker.build_image(pid, temp_dir)
        port = Config.BASE_PORT + (hash(pid) % (Config.MAX_PORT - Config.BASE_PORT))
        env_vars = {}
        container = await docker.run_container(pid, f"deploybot/{pid}", env_vars, port)
        await create_project(user_id, os.path.basename(repo_url).replace(".git", ""), container.id, port)
        await update_project_github(pid, repo_url, branch)
        await update_project_status(pid, "running")
        await update_deploy_count(user_id)
        text = (
            f"✅ *Deployed from GitHub*\n"
            f"Name: `{escape_md(os.path.basename(repo_url).replace('.git',''))}`\n"
            f"ID: `{pid[:8]}`\n"
            f"Status: 🟢 Running\n"
            f"Port: `{port}`"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@safe_handler
async def repos_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    token = await get_github_token(user_id)
    if not token:
        await update.message.reply_text(
            "No GitHub token found\\. Use /connect first\\.", parse_mode=ParseMode.MARKDOWN_V2
        )
        return
    page = 1
    if context.args:
        try:
            page = int(context.args[0])
        except ValueError:
            pass
    repos = await list_repos(token, page)
    if not repos:
        await update.message.reply_text("No repositories found or API error\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    keyboard = []
    for i, r in enumerate(repos):
        keyboard.append(
            [InlineKeyboardButton(f"{r['name']} ⭐ {r['stars']}", callback_data=f"repo_d:{page}:{i}")]
        )
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"repo_p:{page-1}"))
    if len(repos) == 10:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"repo_p:{page+1}"))
    if nav:
        keyboard.append(nav)
    await update.message.reply_text(
        f"*🐙 Your Repositories \\(Page {page}\\)*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


@safe_handler
async def repos_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    token = await get_github_token(user_id)
    if not token:
        await query.edit_message_text("Token missing\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    if data.startswith("repo_p:"):
        page = int(data.split(":")[1])
        repos = await list_repos(token, page)
        keyboard = []
        for i, r in enumerate(repos):
            keyboard.append(
                [InlineKeyboardButton(f"{r['name']} ⭐ {r['stars']}", callback_data=f"repo_d:{page}:{i}")]
            )
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"repo_p:{page-1}"))
        if len(repos) == 10:
            nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"repo_p:{page+1}"))
        if nav:
            keyboard.append(nav)
        await query.edit_message_text(
            f"*🐙 Your Repositories \\(Page {page}\\)*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    elif data.startswith("repo_d:"):
        parts = data.split(":")
        page = int(parts[1])
        idx = int(parts[2])
        repos = await list_repos(token, page)
        if idx >= len(repos):
            await query.edit_message_text("Invalid selection\\.", parse_mode=ParseMode.MARKDOWN_V2)
            return
        r = repos[idx]
        await query.edit_message_text(
            f"Deploying *{escape_md(r['name'])}*\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2
        )
        await _deploy_from_github(update, context, r["url"], r["default_branch"])


@safe_handler
async def connect_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send me your GitHub Personal Access Token \\(PAT\\):", parse_mode=ParseMode.MARKDOWN_V2
    )
    return WAITING_FOR_GITHUB_TOKEN


async def connect_token_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    token = update.message.text.strip()
    valid, username = await validate_token(token)
    if not valid:
        await update.message.reply_text(
            f"Invalid token: {escape_md(username)}\\. Please try again\\.", parse_mode=ParseMode.MARKDOWN_V2
        )
        return WAITING_FOR_GITHUB_TOKEN
    user_id = update.effective_user.id
    if Config.ENCRYPT_TOKENS:
        token = encrypt_token(token)
    await save_github_token(user_id, token, username)
    await update.message.reply_text(f"✅ Connected as *{escape_md(username)}*\\.", parse_mode=ParseMode.MARKDOWN_V2)
    return -1


@safe_handler
async def disconnect_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await delete_github_token(user_id)
    await update.message.reply_text("GitHub account disconnected\\.", parse_mode=ParseMode.MARKDOWN_V2)

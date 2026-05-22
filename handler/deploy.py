import os
import shutil
import tempfile
import uuid
import zipfile
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
from database import (
    count_user_projects,
    create_project,
    update_project_status,
    update_deploy_count,
    check_rate_limit,
    get_env_vars,
)
from docker_manager import DockerManager
from security import scan_files
from utils.smart_deploy import detect_project_type, generate_dockerfile
from utils.formatters import escape_md, safe_handler
from config import Config

docker = DockerManager()
WAITING_FOR_ZIP = range(1)


@safe_handler
async def upload_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if await count_user_projects(user_id) >= Config.MAX_PROJECTS_PER_USER:
        await update.message.reply_text("Max projects limit reached\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return -1
    if not await check_rate_limit(user_id):
        await update.message.reply_text("Rate limit exceeded\\. Try again later\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return -1
    await update.message.reply_text("📤 Send me a \\.zip file \\(max 50MB\\)\\.", parse_mode=ParseMode.MARKDOWN_V2)
    context.user_data.pop("smartdeploy", None)
    return WAITING_FOR_ZIP[0]


@safe_handler
async def upload_zip_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    document = update.message.document
    if not document:
        await update.message.reply_text("Please send a file\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return WAITING_FOR_ZIP[0]
    if document.file_size > 50 * 1024 * 1024:
        await update.message.reply_text("File too large\\. Max 50MB\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return WAITING_FOR_ZIP[0]
    if not document.file_name.endswith(".zip"):
        await update.message.reply_text("Only \\.zip files are allowed\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return WAITING_FOR_ZIP[0]

    await update.message.reply_text("📂 Processing archive\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)
    temp_dir = tempfile.mkdtemp(dir=Config.UPLOAD_DIR)
    try:
        file = await document.get_file()
        content = await file.download_as_bytearray()
        zip_path = os.path.join(temp_dir, "upload.zip")
        with open(zip_path, "wb") as f:
            f.write(content)
        extract_dir = os.path.join(temp_dir, "project")
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(extract_dir)
        items = os.listdir(extract_dir)
        if len(items) == 1 and os.path.isdir(os.path.join(extract_dir, items[0])):
            root = os.path.join(extract_dir, items[0])
            for item in os.listdir(root):
                shutil.move(os.path.join(root, item), os.path.join(extract_dir, item))
            os.rmdir(root)

        safe, reason = scan_files(extract_dir)
        if not safe:
            await update.message.reply_text(
                f"🛡 Security scan failed: {escape_md(reason)}", parse_mode=ParseMode.MARKDOWN_V2
            )
            return -1

        df_path = os.path.join(extract_dir, "Dockerfile")
        if not os.path.exists(df_path):
            if context.user_data.get("smartdeploy"):
                ptype = detect_project_type(extract_dir)
                df = generate_dockerfile(ptype, extract_dir)
                with open(df_path, "w") as f:
                    f.write(df)
                await update.message.reply_text(
                    f"🔍 Detected *{escape_md(ptype)}* project\\. Generated Dockerfile\\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            else:
                await update.message.reply_text(
                    "No Dockerfile found\\. Use /smartdeploy to auto\\-generate one\\.",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
                return -1

        pid = str(uuid.uuid4())
        await update.message.reply_text("🔨 Building Docker image\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2)
        image = await docker.build_image(pid, extract_dir)
        port = Config.BASE_PORT + (hash(pid) % (Config.MAX_PORT - Config.BASE_PORT))
        env_vars = await get_env_vars(pid) or {}
        container = await docker.run_container(pid, f"deploybot/{pid}", env_vars, port)
        name = document.file_name.replace(".zip", "")
        await create_project(user_id, name, container.id, port)
        await update_project_status(pid, "running")
        await update_deploy_count(user_id)
        text = (
            f"✅ *Project Deployed*\n"
            f"Name: `{escape_md(name)}`\n"
            f"ID: `{pid[:8]}`\n"
            f"Status: 🟢 Running\n"
            f"Container: `{container.id[:12]}`"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await update.message.reply_text(f"Error: `{escape_md(str(e))}`", parse_mode=ParseMode.MARKDOWN_V2)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        context.user_data.pop("smartdeploy", None)
    return -1


@safe_handler
async def smartdeploy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if await count_user_projects(user_id) >= Config.MAX_PROJECTS_PER_USER:
        await update.message.reply_text("Max projects limit reached\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return -1
    if not await check_rate_limit(user_id):
        await update.message.reply_text("Rate limit exceeded\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return -1
    await update.message.reply_text("📤 Send me a \\.zip file for smart deploy\\.", parse_mode=ParseMode.MARKDOWN_V2)
    context.user_data["smartdeploy"] = True
    return WAITING_FOR_ZIP[0]


@safe_handler
async def template_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🤖 Telegram Bot", callback_data="tpl:telegram")],
        [InlineKeyboardButton("🎮 Discord Bot", callback_data="tpl:discord")],
        [InlineKeyboardButton("🌐 Flask API", callback_data="tpl:flask")],
        [InlineKeyboardButton("⚡ FastAPI", callback_data="tpl:fastapi")],
        [InlineKeyboardButton("📦 Node Express", callback_data="tpl:express")],
        [InlineKeyboardButton("🕷 Scraper", callback_data="tpl:scraper")],
    ]
    await update.message.reply_text(
        "*📋 Choose a template:*", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN_V2
    )


TEMPLATES = {
    "telegram": {
        "type": "python",
        "files": {
            "main.py": "from telegram import Update\nfrom telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes\n\nasync def start(update: Update, context: ContextTypes.DEFAULT_TYPE):\n    await update.message.reply_text('Hello World!')\n\napp = ApplicationBuilder().token('YOUR_BOT_TOKEN').build()\napp.add_handler(CommandHandler('start', start))\napp.run_polling()\n",
            "requirements.txt": "python-telegram-bot==20.7\n",
        },
    },
    "discord": {
        "type": "python",
        "files": {
            "main.py": "import discord\nfrom discord.ext import commands\n\nbot = commands.Bot(command_prefix='!')\n\n@bot.event\nasync def on_ready():\n    print(f'Logged in as {bot.user}')\n\n@bot.command()\nasync def hello(ctx):\n    await ctx.send('Hello!')\n\nbot.run('YOUR_BOT_TOKEN')\n",
            "requirements.txt": "discord.py==2.3.0\n",
        },
    },
    "flask": {
        "type": "python",
        "files": {
            "app.py": "from flask import Flask\napp = Flask(__name__)\n\n@app.route('/')\ndef home():\n    return 'Hello from Flask!'\n\nif __name__ == '__main__':\n    app.run(host='0.0.0.0', port=5000)\n",
            "requirements.txt": "flask==3.0.0\n",
        },
    },
    "fastapi": {
        "type": "python",
        "files": {
            "main.py": "from fastapi import FastAPI\napp = FastAPI()\n\n@app.get('/')\ndef read_root():\n    return {'message': 'Hello from FastAPI!'}\n\nif __name__ == '__main__':\n    import uvicorn\n    uvicorn.run(app, host='0.0.0.0', port=8000)\n",
            "requirements.txt": "fastapi==0.104.0\nuvicorn==0.24.0\n",
        },
    },
    "express": {
        "type": "nodejs",
        "files": {
            "index.js": "const express = require('express');\nconst app = express();\n\napp.get('/', (req, res) => {\n  res.send('Hello from Express!');\n});\n\napp.listen(3000, () => console.log('Server running on port 3000'));\n",
            "package.json": '{"name": "express-app", "version": "1.0.0", "main": "index.js", "scripts": {"start": "node index.js"}, "dependencies": {"express": "^4.18.0"}}',
        },
    },
    "scraper": {
        "type": "python",
        "files": {
            "main.py": "import requests\nfrom bs4 import BeautifulSoup\n\nurl = 'https://example.com'\nresp = requests.get(url)\nprint(resp.status_code)\nsoup = BeautifulSoup(resp.text, 'html.parser')\nprint(soup.title.string)\n",
            "requirements.txt": "requests==2.31.0\nbeautifulsoup4==4.12.0\n",
        },
    },
}


@safe_handler
async def template_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tpl = query.data.split(":")[1]
    info = TEMPLATES.get(tpl)
    if not info:
        await query.edit_message_text("Unknown template\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    user_id = update.effective_user.id
    if await count_user_projects(user_id) >= Config.MAX_PROJECTS_PER_USER:
        await query.edit_message_text("Max projects limit reached\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    if not await check_rate_limit(user_id):
        await query.edit_message_text("Rate limit exceeded\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    await query.edit_message_text(
        f"🚀 Deploying *{escape_md(tpl)}* template\\.\\.\\.", parse_mode=ParseMode.MARKDOWN_V2
    )
    temp_dir = tempfile.mkdtemp(dir=Config.UPLOAD_DIR)
    try:
        for fname, content in info["files"].items():
            with open(os.path.join(temp_dir, fname), "w") as f:
                f.write(content)
        ptype = info["type"]
        df = generate_dockerfile(ptype, temp_dir)
        with open(os.path.join(temp_dir, "Dockerfile"), "w") as f:
            f.write(df)
        pid = str(uuid.uuid4())
        image = await docker.build_image(pid, temp_dir)
        port = Config.BASE_PORT + (hash(pid) % (Config.MAX_PORT - Config.BASE_PORT))
        env_vars = {}
        container = await docker.run_container(pid, f"deploybot/{pid}", env_vars, port)
        await create_project(user_id, tpl, container.id, port)
        await update_project_status(pid, "running")
        await update_deploy_count(user_id)
        text = (
            f"✅ *Template Deployed*\n"
            f"Name: `{escape_md(tpl)}`\n"
            f"ID: `{pid[:8]}`\n"
            f"Status: 🟢 Running\n"
            f"Port: `{port}`"
        )
        await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await query.message.reply_text(f"Error: `{escape_md(str(e))}`", parse_mode=ParseMode.MARKDOWN_V2)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

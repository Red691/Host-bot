import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
    MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
    DB_NAME = os.getenv("DB_NAME", "botdb")
    DOCKER_NETWORK = os.getenv("DOCKER_NETWORK", "bot_network")
    BASE_PORT = int(os.getenv("BASE_PORT", "10000"))
    MAX_PORT = int(os.getenv("MAX_PORT", "20000"))
    MAX_PROJECTS_PER_USER = int(os.getenv("MAX_PROJECTS_PER_USER", "5"))
    MAX_CPU = float(os.getenv("MAX_CPU", "0.5"))

    _raw_ram = os.getenv("MAX_RAM_MB", "512")
    MAX_RAM_MB = _raw_ram if str(_raw_ram).lower()[-1] in ("k", "m", "g") else f"{_raw_ram}m"

    _raw_storage = os.getenv("MAX_STORAGE_MB", "1024")
    MAX_STORAGE_MB = _raw_storage if str(_raw_storage).lower()[-1] in ("k", "m", "g") else f"{_raw_storage}m"

    DEPLOY_RATE_LIMIT = int(os.getenv("DEPLOY_RATE_LIMIT", "3"))
    RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "3600"))
    SECRET_KEY = os.getenv("SECRET_KEY", "")
    ENCRYPT_TOKENS = os.getenv("ENCRYPT_TOKENS", "true").lower() == "true"
    GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
    GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
    UPLOAD_DIR = os.getenv("UPLOAD_DIR", "./uploads")
    PROJECTS_DIR = os.getenv("PROJECTS_DIR", "./projects")
    BACKUP_DIR = os.getenv("BACKUP_DIR", "./backups")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

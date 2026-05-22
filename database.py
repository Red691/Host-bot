import asyncio
import uuid
from datetime import datetime
from motor.motor_asyncio import AsyncIOMotorClient
from config import Config

client = AsyncIOMotorClient(Config.MONGO_URI)
db = client[Config.DB_NAME]


def retry_async(retries=3, delay=1):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            for i in range(retries):
                try:
                    return await func(*args, **kwargs)
                except Exception:
                    if i == retries - 1:
                        raise
                    await asyncio.sleep(delay)
        return wrapper
    return decorator


# ─── Users ───

@retry_async()
async def create_user(user_id: int, username: str):
    await db.users.update_one(
        {"user_id": user_id},
        {"$setOnInsert": {
            "username": username,
            "is_banned": False,
            "created_at": datetime.utcnow(),
            "deploy_count": 0,
            "last_deploy": None
        }},
        upsert=True
    )


@retry_async()
async def get_user(user_id: int):
    return await db.users.find_one({"user_id": user_id})


@retry_async()
async def ban_user(user_id: int):
    await db.users.update_one({"user_id": user_id}, {"$set": {"is_banned": True}})


@retry_async()
async def update_deploy_count(user_id: int):
    await db.users.update_one(
        {"user_id": user_id},
        {"$inc": {"deploy_count": 1}, "$set": {"last_deploy": datetime.utcnow()}}
    )


@retry_async()
async def check_rate_limit(user_id: int) -> bool:
    user = await db.users.find_one({"user_id": user_id})
    if not user:
        return True
    now = datetime.utcnow()
    last = user.get("last_deploy")
    count = user.get("deploy_count", 0)
    if not last or (now - last).total_seconds() > Config.RATE_LIMIT_WINDOW:
        await db.users.update_one(
            {"user_id": user_id},
            {"$set": {"deploy_count": 0, "last_deploy": now}}
        )
        return True
    return count < Config.DEPLOY_RATE_LIMIT


# ─── Projects ───

@retry_async()
async def create_project(user_id: int, name: str, container_id: str, port: int):
    project_id = str(uuid.uuid4())
    doc = {
        "project_id": project_id,
        "user_id": user_id,
        "name": name,
        "container_id": container_id,
        "status": "running",
        "github_repo": "",
        "github_branch": "main",
        "port": port,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    await db.projects.insert_one(doc)
    return project_id


@retry_async()
async def get_project(project_id: str):
    return await db.projects.find_one({"project_id": project_id})


@retry_async()
async def get_user_projects(user_id: int):
    return await db.projects.find({"user_id": user_id}).to_list(length=100)


@retry_async()
async def update_project_status(project_id: str, status: str):
    await db.projects.update_one(
        {"project_id": project_id},
        {"$set": {"status": status, "updated_at": datetime.utcnow()}}
    )


@retry_async()
async def update_project_container_id(project_id: str, container_id: str):
    await db.projects.update_one(
        {"project_id": project_id},
        {"$set": {"container_id": container_id, "updated_at": datetime.utcnow()}}
    )


@retry_async()
async def update_project_github(project_id: str, repo_url: str, branch: str):
    await db.projects.update_one(
        {"project_id": project_id},
        {"$set": {"github_repo": repo_url, "github_branch": branch, "updated_at": datetime.utcnow()}}
    )


@retry_async()
async def delete_project(project_id: str):
    await db.projects.delete_one({"project_id": project_id})


@retry_async()
async def count_user_projects(user_id: int) -> int:
    return await db.projects.count_documents({"user_id": user_id})


# ─── GitHub Tokens ───

@retry_async()
async def save_github_token(user_id: int, encrypted_token: str, github_username: str):
    await db.github_tokens.update_one(
        {"user_id": user_id},
        {"$set": {
            "encrypted_token": encrypted_token,
            "github_username": github_username,
            "connected_at": datetime.utcnow()
        }},
        upsert=True
    )


@retry_async()
async def get_github_token(user_id: int):
    doc = await db.github_tokens.find_one({"user_id": user_id})
    if not doc:
        return None
    token = doc["encrypted_token"]
    if Config.ENCRYPT_TOKENS:
        from security import decrypt_token
        token = decrypt_token(token)
    return token


@retry_async()
async def delete_github_token(user_id: int):
    await db.github_tokens.delete_one({"user_id": user_id})


# ─── Env Vars ───

@retry_async()
async def set_env_vars(project_id: str, vars_dict: dict):
    await db.env_vars.update_one(
        {"project_id": project_id},
        {"$set": {"vars": vars_dict, "updated_at": datetime.utcnow()}},
        upsert=True
    )


@retry_async()
async def get_env_vars(project_id: str):
    doc = await db.env_vars.find_one({"project_id": project_id})
    return doc.get("vars", {}) if doc else {}


@retry_async()
async def delete_env_var(project_id: str, key: str):
    await db.env_vars.update_one(
        {"project_id": project_id},
        {"$unset": {f"vars.{key}": ""}, "$set": {"updated_at": datetime.utcnow()}}
    )


# ─── Logs ───

@retry_async()
async def add_log(project_id: str, message: str, level: str = "INFO"):
    await db.logs.insert_one({
        "project_id": project_id,
        "message": message,
        "level": level,
        "timestamp": datetime.utcnow()
    })


@retry_async()
async def get_logs(project_id: str, limit: int = 50):
    cursor = db.logs.find({"project_id": project_id}).sort("timestamp", -1).limit(limit)
    return await cursor.to_list(length=limit)


# ─── Init ───

async def init_db():
    await db.users.create_index("user_id", unique=True)
    await db.projects.create_index("project_id", unique=True)
    await db.github_tokens.create_index("user_id", unique=True)
    await db.env_vars.create_index("project_id", unique=True)
    await db.logs.create_index([("project_id", 1), ("timestamp", -1)])

import aiohttp
from github import Github


async def validate_token(token: str):
    try:
        g = Github(token)
        user = g.get_user()
        return True, user.login
    except Exception as e:
        return False, str(e)


async def list_repos(token: str, page: int = 1):
    try:
        g = Github(token)
        user = g.get_user()
        repos = user.get_repos().get_page(page - 1)
        return [
            {
                "name": r.full_name,
                "stars": r.stargazers_count,
                "url": r.clone_url,
                "default_branch": r.default_branch,
            }
            for r in repos
        ]
    except Exception:
        return []


async def get_repo_info(token: str, repo_name: str):
    try:
        g = Github(token)
        repo = g.get_repo(repo_name)
        return {
            "name": repo.full_name,
            "stars": repo.stargazers_count,
            "url": repo.clone_url,
            "default_branch": repo.default_branch,
        }
    except Exception:
        return None


async def clone_repo(repo_url: str, token: str, branch: str, dest_path: str):
    parts = (
        repo_url.replace("https://github.com/", "")
        .replace("http://github.com/", "")
        .replace(".git", "")
        .split("/")
    )
    if len(parts) < 2:
        return False
    owner, repo = parts[0], parts[1]
    zip_url = f"https://api.github.com/repos/{owner}/{repo}/zipball/{branch}"
    headers = {"Authorization": f"token {token}", "User-Agent": "DeployBot"}
    if not token:
        headers = {"User-Agent": "DeployBot"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(zip_url, headers=headers, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status != 200:
                    return False
                data = await resp.read()
                import zipfile

                z = zipfile.ZipFile(zipfile.io.BytesIO(data))
                z.extractall(dest_path)
                import os

                items = os.listdir(dest_path)
                if len(items) == 1 and os.path.isdir(os.path.join(dest_path, items[0])):
                    root = os.path.join(dest_path, items[0])
                    for item in os.listdir(root):
                        os.rename(os.path.join(root, item), os.path.join(dest_path, item))
                    os.rmdir(root)
                return True
    except Exception:
        return False

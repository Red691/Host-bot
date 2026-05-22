import asyncio
import io
import os
import tarfile
import zipfile
from datetime import datetime
from docker import DockerClient
from docker.errors import NotFound, APIError
from config import Config


class DockerManager:
    def __init__(self):
        self.client = DockerClient.from_env()
        try:
            self.client.networks.get(Config.DOCKER_NETWORK)
        except NotFound:
            self.client.networks.create(Config.DOCKER_NETWORK, driver="bridge")

    async def _run(self, func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    async def build_image(self, project_id: str, path: str):
        def _build():
            image, logs = self.client.images.build(
                path=path, tag=f"deploybot/{project_id}", rm=True, forcerm=True
            )
            return image
        return await self._run(_build)

    async def run_container(self, project_id: str, image, env_vars: dict, port: int):
        def _run():
            container = self.client.containers.run(
                image,
                detach=True,
                environment=env_vars or {},
                ports={f"{port}/tcp": port} if port else None,
                network=Config.DOCKER_NETWORK,
                mem_limit=Config.MAX_RAM_MB,
                cpu_period=100000,
                cpu_quota=int(Config.MAX_CPU * 100000),
                name=f"deploybot_{project_id}",
                labels={"project_id": project_id},
                restart_policy={"Name": "unless-stopped"},
            )
            return container
        return await self._run(_run)

    async def stop_container(self, container_id: str):
        def _stop():
            try:
                c = self.client.containers.get(container_id)
                c.stop(timeout=10)
                return True
            except NotFound:
                return False
        return await self._run(_stop)

    async def remove_container(self, container_id: str):
        def _rm():
            try:
                c = self.client.containers.get(container_id)
                c.remove(force=True)
                return True
            except NotFound:
                return False
        return await self._run(_rm)

    async def get_container_logs(self, container_id: str, tail: int = 50):
        def _logs():
            try:
                c = self.client.containers.get(container_id)
                return c.logs(tail=tail, timestamps=True).decode("utf-8", errors="replace")
            except NotFound:
                return "Container not found"
        return await self._run(_logs)

    async def get_container_stats(self, container_id: str):
        def _stats():
            try:
                c = self.client.containers.get(container_id)
                stats = c.stats(stream=False)
                cpu_delta = (
                    stats["cpu_stats"]["cpu_usage"]["total_usage"]
                    - stats["precpu_stats"]["cpu_usage"]["total_usage"]
                )
                system_delta = (
                    stats["cpu_stats"]["system_cpu_usage"]
                    - stats["precpu_stats"]["system_cpu_usage"]
                )
                cpu_percent = 0.0
                if system_delta > 0 and cpu_delta > 0:
                    cpu_count = len(stats["cpu_stats"]["cpu_usage"].get("percpu_usage", [1]))
                    cpu_percent = (cpu_delta / system_delta) * cpu_count * 100
                mem_usage = stats["memory_stats"].get("usage", 0)
                mem_limit = stats["memory_stats"].get("limit", 1)
                ram_mb = mem_usage / (1024 * 1024)
                started = c.attrs["State"]["StartedAt"]
                uptime = "N/A"
                if started and started != "0001-01-01T00:00:00Z":
                    started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
                    delta = datetime.now().astimezone() - started_dt
                    uptime = str(delta).split(".")[0]
                return {
                    "cpu_percent": round(cpu_percent, 2),
                    "ram_mb": round(ram_mb, 2),
                    "ram_limit_mb": round(mem_limit / (1024 * 1024), 2),
                    "uptime": uptime,
                }
            except Exception as e:
                return {"cpu_percent": 0, "ram_mb": 0, "ram_limit_mb": 0, "uptime": f"Error: {e}"}
        return await self._run(_stats)

    async def exec_in_container(self, container_id: str, command: str):
        def _exec():
            try:
                c = self.client.containers.get(container_id)
                result = c.exec_run(command, demux=True)
                output = result.output
                if isinstance(output, tuple):
                    stdout, stderr = output
                    text = (stdout or b"").decode("utf-8", errors="replace") + (
                        stderr or b""
                    ).decode("utf-8", errors="replace")
                else:
                    text = output.decode("utf-8", errors="replace") if output else ""
                return {"exit_code": result.exit_code, "output": text}
            except Exception as e:
                return {"exit_code": -1, "output": str(e)}
        return await self._run(_exec)

    async def copy_file_to_container(self, container_id: str, file_bytes: bytes, path: str):
        def _copy():
            c = self.client.containers.get(container_id)
            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode="w") as tar:
                info = tarfile.TarInfo(name=os.path.basename(path))
                info.size = len(file_bytes)
                tar.addfile(info, io.BytesIO(file_bytes))
            tar_stream.seek(0)
            c.put_archive(os.path.dirname(path) or "/", tar_stream)
            return True
        return await self._run(_copy)

    async def get_container_files(self, container_id: str, path: str):
        def _files():
            try:
                c = self.client.containers.get(container_id)
                result = c.exec_run(f"ls -la {path}", demux=True)
                output = result.output
                if isinstance(output, tuple):
                    stdout, _ = output
                    text = (stdout or b"").decode("utf-8", errors="replace")
                else:
                    text = output.decode("utf-8", errors="replace") if output else ""
                lines = text.strip().split("\n")[1:]
                files = []
                for line in lines:
                    parts = line.split(maxsplit=8)
                    if len(parts) >= 9:
                        name = parts[8]
                        ftype = "folder" if parts[0].startswith("d") else "file"
                        files.append({"name": name, "type": ftype, "perms": parts[0], "size": parts[4]})
                return files
            except Exception:
                return []
        return await self._run(_files)

    async def download_file_from_container(self, container_id: str, path: str):
        def _download():
            try:
                c = self.client.containers.get(container_id)
                bits, stat = c.get_archive(path)
                file_obj = io.BytesIO()
                for chunk in bits:
                    file_obj.write(chunk)
                file_obj.seek(0)
                return file_obj.read()
            except Exception:
                return None
        return await self._run(_download)

    async def backup_container(self, container_id: str, project_name: str):
        def _backup():
            try:
                c = self.client.containers.get(container_id)
                bits, stat = c.get_archive("/app")
                tar_bytes = io.BytesIO()
                for chunk in bits:
                    tar_bytes.write(chunk)
                tar_bytes.seek(0)
                zip_bytes = io.BytesIO()
                with zipfile.ZipFile(zip_bytes, "w", zipfile.ZIP_DEFLATED) as zf:
                    with tarfile.open(fileobj=tar_bytes, mode="r") as tar:
                        for member in tar.getmembers():
                            if member.isfile():
                                f = tar.extractfile(member)
                                if f:
                                    zf.writestr(member.name, f.read())
                zip_bytes.seek(0)
                return zip_bytes.read()
            except Exception:
                return None
        return await self._run(_backup)

    async def install_pip_packages(self, container_id: str, packages: str):
        cmd = f"pip install {packages}"
        return await self.exec_in_container(container_id, cmd)

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests


class LocalServer:
    REQUIRED_BUILD_ID = "2026-05-29-spell-scope"
    REQUIRED_PATHS = {
        "/correct-public",
        "/summary-public",
        "/evaluation-public",
        "/title-public",
        "/tone-public",
    }

    def __init__(self, base_url="http://127.0.0.1:8765"):
        self.base_url = base_url.rstrip("/")
        self.process = None
        self.last_error = ""

    def ensure_running(self, timeout=8.0):
        if self._is_running():
            return
        self._stop_incompatible_server()

        server_dir = Path(__file__).resolve().parents[2] / "server"
        log_path = Path(__file__).resolve().parents[2] / ".logs" / "local_server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("a", encoding="utf-8")
        env = os.environ.copy()
        env.setdefault("PYTHONIOENCODING", "utf-8")
        command = self._server_command()
        log_file.write(f"\n{time.strftime('%Y-%m-%d %H:%M:%S')} start {' '.join(command)}\n")
        log_file.flush()
        self.process = subprocess.Popen(
            command,
            cwd=str(server_dir),
            env=env,
            stdout=log_file,
            stderr=log_file,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._is_running():
                self.last_error = ""
                log_file.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} ready\n")
                log_file.flush()
                return
            if self.process.poll() is not None:
                break
            time.sleep(0.15)
        self.last_error = f"로컬 서버를 시작할 수 없습니다. 로그: {log_path}"
        raise RuntimeError(self.last_error)

    def stop(self):
        if self.process is None:
            return
        try:
            self.process.terminate()
        except Exception:
            pass
        self.process = None

    def _is_running(self):
        try:
            response = requests.get(f"{self.base_url}/", timeout=0.5)
            if response.status_code >= 500:
                return False
            data = response.json()
            if data.get("build_id") != self.REQUIRED_BUILD_ID:
                self.last_error = "Local server build is outdated."
                return False
            return self._has_required_paths()
        except Exception:
            return False

    def _has_required_paths(self):
        try:
            response = requests.get(f"{self.base_url}/openapi.json", timeout=0.8)
            if response.status_code >= 400:
                return False
            paths = set((response.json().get("paths") or {}).keys())
            missing = self.REQUIRED_PATHS - paths
            if missing:
                self.last_error = f"Local server is outdated. Missing endpoints: {', '.join(sorted(missing))}"
                return False
            return True
        except Exception as exc:
            self.last_error = f"Local server compatibility check failed: {exc}"
            return False

    def _stop_incompatible_server(self):
        if not self._server_responds():
            return
        if self.process is not None:
            self.stop()
            time.sleep(0.2)
            return
        self._stop_listening_process_on_windows()

    def _server_responds(self):
        try:
            response = requests.get(f"{self.base_url}/", timeout=0.5)
            return response.status_code < 500
        except Exception:
            return False

    def _stop_listening_process_on_windows(self):
        if sys.platform != "win32":
            return
        port = urlparse(self.base_url).port
        if not port:
            return
        try:
            result = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            return
        pids = set()
        marker = f":{port}"
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            if marker in parts[1] and parts[3].upper() == "LISTENING":
                pids.add(parts[-1])
        for pid in pids:
            try:
                subprocess.run(
                    ["taskkill", "/PID", pid, "/F"],
                    capture_output=True,
                    timeout=3,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except Exception:
                pass
        if pids:
            time.sleep(0.4)

    def _server_command(self):
        scripts_uvicorn = Path(sys.executable).resolve().parent / "Scripts" / "uvicorn.exe"
        if scripts_uvicorn.exists():
            return [str(scripts_uvicorn), "main:app", "--host", "127.0.0.1", "--port", "8765"]

        uvicorn_path = shutil.which("uvicorn")
        if uvicorn_path:
            return [uvicorn_path, "main:app", "--host", "127.0.0.1", "--port", "8765"]

        return [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8765"]

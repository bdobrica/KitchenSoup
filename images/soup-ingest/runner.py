"""Private, bounded transport for the public Soup document CLI. No Soup imports."""

import json
import os
import re
import resource
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory

MAX_INPUT = 16 * 1024**2
MAX_OUTPUT = 32 * 1024**2
MAX_LOG = 256 * 1024
SOUP_VERSION = version("soup-cli")
IMAGE_ID = os.environ.get("SOUP_IMAGE_ID", "")
INGESTION_LOCK = threading.Lock()


def limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT, MAX_OUTPUT))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))


def ingest(body, extension):
    with TemporaryDirectory(prefix="soup-ingest-") as directory:
        root = Path(directory)
        (root / ("source" + extension)).write_bytes(body)
        with (root / "stdout").open("wb") as stdout, (root / "stderr").open("wb") as stderr:
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "/runner/runner.py",
                        "ingest-cli",
                        extension,
                    ],
                    cwd=root,
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=65,
                    check=False,
                    env={
                        "PATH": "/usr/local/bin:/usr/bin:/bin",
                        "HOME": directory,
                        "LANG": "C.UTF-8",
                        "NO_COLOR": "1",
                        "TERM": "dumb",
                        "HF_HUB_OFFLINE": "1",
                        "HF_HUB_DISABLE_TELEMETRY": "1",
                    },
                )
                exit_code = result.returncode
            except subprocess.TimeoutExpired:
                exit_code = 124
        logs = {}
        truncated = False
        for name in ("stdout", "stderr"):
            with (root / name).open("rb") as stream:
                data = stream.read(MAX_LOG + 1)
            truncated |= len(data) > MAX_LOG
            logs[name] = data[:MAX_LOG].decode("utf-8", errors="replace")
        output = root / "output.jsonl"
        data = ""
        if exit_code == 0:
            if not output.is_file() or output.stat().st_size > MAX_OUTPUT:
                exit_code = 125
            else:
                try:
                    data = output.read_text(encoding="utf-8")
                except UnicodeError:
                    exit_code = 125
        return {
            "schema": "kitchensoup.soup-ingest-response/v1",
            "soup_version": SOUP_VERSION,
            "image_id": IMAGE_ID,
            "exit_code": exit_code,
            "output": data,
            "stdout": logs["stdout"],
            "stderr": logs["stderr"],
            "logs_truncated": truncated,
        }


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *_args):
        pass  # Source content, CLI output and request details never enter container logs.

    def reply(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/healthz":
            self.reply(404, {})
        else:
            self.reply(
                200 if re.fullmatch(r"sha256:[0-9a-f]{64}", IMAGE_ID) else 503,
                {"soup_version": SOUP_VERSION},
            )

    def do_POST(self):
        extension = self.path.removeprefix("/v1/ingest/")
        if self.path != "/v1/ingest/" + extension or extension not in (
            ".txt",
            ".md",
            ".pdf",
            ".docx",
        ):
            self.reply(404, {})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_INPUT or self.headers.get("Transfer-Encoding"):
            self.reply(413, {})
            return
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", IMAGE_ID):
            self.reply(503, {})
            return
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                self.reply(400, {})
                return
            if not INGESTION_LOCK.acquire(blocking=False):
                self.reply(503, {})
                return
            try:
                self.reply(200, ingest(body, extension))
            finally:
                INGESTION_LOCK.release()
        except (OSError, ValueError):
            self.reply(503, {})


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "ingest-cli":
        extension = sys.argv[2]
        if extension not in (".txt", ".md", ".pdf", ".docx"):
            sys.exit(2)
        # Set limits in a fresh process; preexec_fn is unsafe in a threaded server.
        limits()
        os.execv(
            "/usr/local/bin/soup",
            ["soup", "data", "ingest", "source" + extension, "--output", "output.jsonl"],
        )
    # One parser at a time, while health probes remain responsive.
    ThreadingHTTPServer(("0.0.0.0", 8090), Handler).serve_forever()

"""Integration tests for publicTrafficHandler scripts (bash only)."""

from __future__ import annotations

import http.server
import json
import os
import signal
import socket
import socketserver
import subprocess
import threading
import time
from http.client import HTTPConnection
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_DIR = PROJECT_ROOT / "communicationPlane" / "publicTrafficHandler"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _start_health_server() -> tuple[socketserver.TCPServer, int, threading.Thread]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port, thread


@pytest.mark.integration
def test_start_whatsapp_stack_starts_server_and_tunnel(tmp_path: Path) -> None:
    """Ensure start_whatsapp_stack.sh starts server and tunnel when none run."""
    log_path = tmp_path / "start.log"
    server_stub = tmp_path / "server_stub.sh"
    fake_cloudflared = tmp_path / "cloudflared"

    _write_executable(
        server_stub,
        f"""#!/usr/bin/env bash
set -euo pipefail
echo "server-started" >> "{log_path}"
sleep 10
""",
    )
    _write_executable(
        fake_cloudflared,
        f"""#!/usr/bin/env bash
set -euo pipefail
echo "tunnel-started $@" >> "{log_path}"
sleep 10
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env.get('PATH', '')}"
    env["SERVER_CMD"] = f"bash {server_stub}"
    env["SERVER_PORT"] = "5999"
    env["SERVER_HOST"] = "127.0.0.1"
    env["TUNNEL_TOKEN"] = "token-123"
    env["TUNNEL_PROCESS_MATCH"] = "__no_match__"

    script = SCRIPT_DIR / "start_whatsapp_stack.sh"
    process = subprocess.Popen(
        ["bash", str(script)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        for _ in range(50):
            if log_path.exists():
                contents = log_path.read_text()
                if "server-started" in contents and "tunnel-started" in contents:
                    break
            time.sleep(0.1)
        contents = log_path.read_text() if log_path.exists() else ""
        assert "server-started" in contents
        assert "tunnel-started tunnel run --token token-123" in contents
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)


@pytest.mark.integration
def test_start_whatsapp_stack_skips_when_running(tmp_path: Path) -> None:
    """Ensure start_whatsapp_stack.sh skips starting when server and tunnel run."""
    log_path = tmp_path / "skip.log"
    server_stub = tmp_path / "server_stub.sh"
    fake_cloudflared = tmp_path / "cloudflared"

    _write_executable(
        server_stub,
        f"""#!/usr/bin/env bash
set -euo pipefail
echo "server-started" >> "{log_path}"
exit 0
""",
    )
    _write_executable(
        fake_cloudflared,
        """#!/usr/bin/env bash
set -euo pipefail
sleep 10
""",
    )

    server, port, thread = _start_health_server()

    cloudflared_proc = subprocess.Popen(
        [str(fake_cloudflared), "tunnel", "run", "--token", "dummy"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env.get('PATH', '')}"
    env["SERVER_CMD"] = f"bash {server_stub}"
    env["SERVER_PORT"] = str(port)
    env["SERVER_HOST"] = "127.0.0.1"
    env["TUNNEL_TOKEN"] = "token-123"
    env["TUNNEL_PROCESS_MATCH"] = "cloudflared.*tunnel run"

    script = SCRIPT_DIR / "start_whatsapp_stack.sh"
    try:
        result = subprocess.run(
            ["bash", str(script)],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "Server already running" in result.stdout
        assert "Tunnel already running" in result.stdout
        assert "Nothing to do" in result.stdout
        contents = log_path.read_text() if log_path.exists() else ""
        assert "server-started" not in contents
    finally:
        cloudflared_proc.terminate()
        cloudflared_proc.wait(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.integration
def test_tunnel_daemon_start_stop(tmp_path: Path) -> None:
    """Ensure tunnel_daemon.sh can start and stop a tunnel process."""
    fake_cloudflared = tmp_path / "cloudflared"
    log_path = tmp_path / "tunnel.log"
    pid_path = tmp_path / "tunnel.pid"

    _write_executable(
        fake_cloudflared,
        f"""#!/usr/bin/env bash
set -euo pipefail
echo "$@" >> "{log_path}"
sleep 10
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env.get('PATH', '')}"
    env["TUNNEL_TOKEN"] = "token-123"
    env["PID_FILE"] = str(pid_path)
    env["LOG_FILE"] = str(log_path)

    script = SCRIPT_DIR / "tunnel_daemon.sh"

    start = subprocess.run(
        ["bash", str(script), "start"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Tunnel started" in start.stdout
    assert pid_path.exists()

    status = subprocess.run(
        ["bash", str(script), "status"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Tunnel running" in status.stdout

    stop = subprocess.run(
        ["bash", str(script), "stop"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Stopping tunnel" in stop.stdout
    assert not pid_path.exists()


@pytest.mark.integration
def test_webhook_endpoint_receives_message() -> None:
    """Start the Flask app and confirm the webhook endpoint accepts a message."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    env = os.environ.copy()
    env["SERVER_HOST"] = "127.0.0.1"
    env["SERVER_PORT"] = str(port)
    env["FLASK_DEBUG"] = "0"
    env["SALES_GROUP_ID"] = "expected@g.us"

    server_dir = PROJECT_ROOT / "communicationPlane" / "server"
    process = subprocess.Popen(
        ["python", "app.py"],
        cwd=server_dir,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                conn = HTTPConnection("127.0.0.1", port, timeout=0.5)
                conn.request("GET", "/health")
                resp = conn.getresponse()
                resp.read()
                conn.close()
                if resp.status == 200:
                    break
            except Exception:
                time.sleep(0.1)
        else:
            raise AssertionError("Server did not become healthy in time.")

        payload = {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "chat": {"id": -100999, "type": "group", "title": "Staff"},
                "from": {"id": 111, "first_name": "Tester", "is_bot": False},
                "date": 1700000000,
                "text": "hello",
            },
        }
        body = json.dumps(payload)
        conn = HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request(
            "POST",
            "/telegram/webhook/messages",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        resp_body = resp.read().decode("utf-8")
        conn.close()
        assert resp.status == 200
        assert '"status":"ok"' in resp_body
        assert '"messages":1' in resp_body
    finally:
        process.terminate()
        process.wait(timeout=5)

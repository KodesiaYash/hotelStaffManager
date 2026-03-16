import http.server
import os
import signal
import socketserver
import subprocess
import tempfile
import time
from pathlib import Path
import threading

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_DIR = PROJECT_ROOT / "communicationPlane" / "publicTrafficHandler"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(0o755)


def _start_health_server() -> tuple[socketserver.TCPServer, int, threading.Thread]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - external interface
            if self.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port, thread


@pytest.mark.integration
def test_tunnel_dns_and_run_invokes_cloudflared(tmp_path: Path) -> None:
    """Ensure tunnel_dns_and_run.sh calls cloudflared for DNS and run."""
    log_path = tmp_path / "cloudflared.log"
    fake_cloudflared = tmp_path / "cloudflared"
    _write_executable(
        fake_cloudflared,
        """#!/usr/bin/env bash
set -euo pipefail
echo "$@" >> "{log_path}"
exit 0
""".format(
            log_path=log_path
        ),
    )

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env.get('PATH', '')}"
    env["TUNNEL_TOKEN"] = "token-123"
    env["TUNNEL_NAME"] = "test-tunnel"
    env["TUNNEL_HOSTNAME"] = "example.test"
    env["OVERWRITE_DNS"] = "1"

    script = SCRIPT_DIR / "tunnel_dns_and_run.sh"
    result = subprocess.run(
        [\"bash\", str(script)],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0

    lines = log_path.read_text().splitlines()
    assert any(
        line
        == "tunnel route dns --overwrite-dns test-tunnel example.test"
        for line in lines
    )
    assert any(line == "tunnel run --token token-123" for line in lines)


@pytest.mark.integration
def test_start_whatsapp_stack_starts_server_and_tunnel(tmp_path: Path) -> None:
    """Ensure start_whatsapp_stack.sh starts server and tunnel when none run."""
    log_path = tmp_path / "start.log"
    server_stub = tmp_path / "server_stub.sh"
    tunnel_stub = tmp_path / "tunnel_stub.sh"

    _write_executable(
        server_stub,
        f"""#!/usr/bin/env bash
set -euo pipefail
echo "server-started" >> "{log_path}"
sleep 10
""",
    )
    _write_executable(
        tunnel_stub,
        f"""#!/usr/bin/env bash
set -euo pipefail
echo "tunnel-started" >> "{log_path}"
sleep 10
""",
    )

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    env = os.environ.copy()
    env["SERVER_CMD"] = f"bash {server_stub}"
    env["TUNNEL_CMD"] = str(tunnel_stub)
    env["SERVER_PORT"] = str(port)
    env["SERVER_HOST"] = "127.0.0.1"

    script = SCRIPT_DIR / "start_whatsapp_stack.sh"
    process = subprocess.Popen(
        [\"bash\", str(script)],
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
        assert "tunnel-started" in contents
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)


@pytest.mark.integration
def test_start_whatsapp_stack_skips_when_running(tmp_path: Path) -> None:
    """Ensure start_whatsapp_stack.sh skips starting when server and tunnel run."""
    log_path = tmp_path / "skip.log"
    server_stub = tmp_path / "server_stub.sh"
    tunnel_stub = tmp_path / "tunnel_stub.sh"

    _write_executable(
        server_stub,
        f"""#!/usr/bin/env bash
set -euo pipefail
echo "server-started" >> "{log_path}"
exit 0
""",
    )
    _write_executable(
        tunnel_stub,
        f"""#!/usr/bin/env bash
set -euo pipefail
echo "tunnel-started" >> "{log_path}"
exit 0
""",
    )

    server, port, thread = _start_health_server()

    fake_cloudflared = tmp_path / "cloudflared"
    _write_executable(
        fake_cloudflared,
        """#!/usr/bin/env bash
set -euo pipefail
sleep 10
""",
    )
    cloudflared_proc = subprocess.Popen(
        [str(fake_cloudflared), "tunnel", "run", "--token", "dummy"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    env = os.environ.copy()
    env["SERVER_CMD"] = f"bash {server_stub}"
    env["TUNNEL_CMD"] = str(tunnel_stub)
    env["SERVER_PORT"] = str(port)
    env["SERVER_HOST"] = "127.0.0.1"

    script = SCRIPT_DIR / "start_whatsapp_stack.sh"
    try:
        result = subprocess.run(
            [\"bash\", str(script)],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        assert "Server already running" in result.stdout
        assert "Tunnel already running" in result.stdout
        assert "Nothing to do" in result.stdout
        if log_path.exists():
            contents = log_path.read_text()
        else:
            contents = ""
        assert "server-started" not in contents
        assert "tunnel-started" not in contents
    finally:
        cloudflared_proc.terminate()
        cloudflared_proc.wait(timeout=5)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

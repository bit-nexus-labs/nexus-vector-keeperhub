from __future__ import annotations

"""Nexus Vector localhost control-plane adapter."""

import json
import os
import re
import secrets
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HOST = "127.0.0.1"
PORT = int(os.environ.get("NEXUS_VECTOR_ADAPTER_PORT", "8765"))
ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
RUNNER_PS1 = ROOT / "tools" / "invoke_anna_mark_leo_video_mission.ps1"
RUN_REF = os.environ.get("NEXUS_VECTOR_RUN_REF", "anna-mark-leo-video-20260809-v1")
LOG_ROOT = os.environ.get("NEXUS_VECTOR_OPERATOR_LOG_ROOT", "").strip()
COMMAND_TIMEOUT_SECONDS = 90
MAX_BODY_BYTES = 16_384
RUN_REF_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
ALLOWED_EFFECTS = {"anna", "mark", "leo"}
ALLOWED_ACTIONS = {"simulate", "broadcast", "provider-bind", "verify"}
STATIC_FILES = {
    "/api-client.js": ("api-client.js", "application/javascript; charset=utf-8"),
    "/hackathon-demo.html": ("hackathon-demo.html", "text/html; charset=utf-8"),
}
ADAPTER_TOKEN = secrets.token_urlsafe(32)
ALLOWED_ORIGIN = f"http://{HOST}:{PORT}"
_locks = {effect: threading.Lock() for effect in (*ALLOWED_EFFECTS, "*")}


def _validate_run_ref(value: str) -> str:
    if not RUN_REF_RE.fullmatch(value):
        raise ValueError("invalid_run_ref")
    return value


def _runner_command(action: str, effect: str | None, approval: str | None) -> list[str]:
    if action not in {"prepare", "status", *ALLOWED_ACTIONS}:
        raise ValueError("unknown_action")
    if effect is not None and effect not in ALLOWED_EFFECTS:
        raise ValueError("unknown_effect")
    if action in ALLOWED_ACTIONS and effect is None:
        raise ValueError("effect_required")
    if action in {"simulate", "broadcast"} and not approval:
        raise ValueError("approval_required")
    args = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(RUNNER_PS1), "-Command", action, "-RunRef", _validate_run_ref(RUN_REF)]
    if effect is not None:
        args += ["-Effect", effect]
    if action in {"simulate", "broadcast"}:
        args += ["-Approval", approval or ""]
    if action == "broadcast":
        args += ["-ApproveTestnetWrite"]
    return args


def _parse_runner_output(stdout: str, stderr: str) -> dict:
    for line in (line.strip() for line in stdout.splitlines() if line.strip()):
        if line.startswith("{") and line.endswith("}"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return {"error": "runner_non_json_output", "raw_stdout": stdout[-8000:], "raw_stderr": stderr[-8000:]}


def _run_command(action: str, effect: str | None, approval: str | None) -> tuple[int, dict]:
    if not RUNNER_PS1.is_file():
        return 500, {"error": "runner_not_found", "path": str(RUNNER_PS1)}
    try:
        command = _runner_command(action, effect, approval)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    try:
        proc = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, shell=False, check=False)
    except subprocess.TimeoutExpired:
        return 504, {"error": "runner_timeout", "outcome": "UNKNOWN", "note": "Outcome is ambiguous. Poll /api/mission/status. Do not retry the mutating action."}
    except OSError as exc:
        return 500, {"error": "runner_start_failed", "detail": str(exc)}
    payload = _parse_runner_output(proc.stdout, proc.stderr)
    if action == "status" and isinstance(payload, dict) and payload.get("status") in {"STOP", "LOCAL_STATUS", "READY_FOR_EXECUTION", "COMPLETED", "PASS"}:
        return 200, payload
    return (200 if proc.returncode == 0 else 502), payload


def _log_path(run_ref: str) -> Path | None:
    if not LOG_ROOT:
        return None
    return Path(LOG_ROOT) / run_ref / "operator_timeline.log"


class AdapterHandler(BaseHTTPRequestHandler):
    server_version = "NexusVectorAdapter/0.4"

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self, *, require_origin: bool) -> bool:
        if require_origin and self.headers.get("Origin") != ALLOWED_ORIGIN:
            return False
        return secrets.compare_digest(self.headers.get("X-Nexus-Token", ""), ADAPTER_TOKEN)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid_content_length") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("request_body_too_large")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(value, dict):
            raise ValueError("request_body_must_be_object")
        return value

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._serve_index()
            return
        if parsed.path in STATIC_FILES:
            if not self._authorized(require_origin=False):
                self._json(401, {"error": "unauthorized"})
                return
            self._serve_static(parsed.path)
            return
        if not self._authorized(require_origin=False):
            self._json(401, {"error": "unauthorized"})
            return
        if parsed.path == "/api/mission/status":
            status, body = _run_command("status", None, None)
            self._json(status, body)
            return
        if parsed.path == "/api/mission/log":
            run_ref = parse_qs(parsed.query).get("run_ref", [RUN_REF])[0]
            try:
                path = _log_path(_validate_run_ref(run_ref))
            except ValueError:
                self._json(400, {"error": "invalid_run_ref"})
                return
            if path is None:
                self._json(404, {"error": "operator_log_root_not_configured"})
                return
            try:
                text = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                self._json(404, {"error": "operator_log_not_found"})
            except OSError as exc:
                self._json(500, {"error": "operator_log_read_failed", "detail": str(exc)})
            else:
                self._json(200, {"run_ref": run_ref, "log": text[-50_000:]})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if not self._authorized(require_origin=True):
            self._json(401, {"error": "unauthorized"})
            return
        parsed = urlparse(self.path)
        parts = [part for part in parsed.path.split("/") if part]
        if parts[:2] != ["api", "mission"]:
            self._json(404, {"error": "not_found"})
            return
        try:
            body = self._body()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": str(exc)})
            return
        if len(parts) == 3 and parts[2] == "prepare":
            self._mutating("prepare", None, None)
            return
        if len(parts) == 4:
            effect, action = parts[2], parts[3]
            if effect not in ALLOWED_EFFECTS:
                self._json(400, {"error": "unknown_effect"})
                return
            if action not in ALLOWED_ACTIONS:
                self._json(400, {"error": "unknown_action"})
                return
            approval = body.get("approval") if isinstance(body.get("approval"), str) else None
            self._mutating(action, effect, approval)
            return
        self._json(404, {"error": "not_found"})

    def _mutating(self, action: str, effect: str | None, approval: str | None) -> None:
        lock = _locks[effect or "*"]
        if not lock.acquire(blocking=False):
            self._json(409, {"error": "effect_busy", "note": "A mutating call is already in flight. Poll status; do not retry automatically."})
            return
        try:
            status, body = _run_command(action, effect, approval)
            self._json(status, body)
        finally:
            lock.release()

    def _serve_index(self) -> None:
        index_path = STATIC_DIR / "index.html"
        if not index_path.is_file():
            self._json(404, {"error": "ui_not_found"})
            return
        html = index_path.read_text(encoding="utf-8")
        api_client_path = STATIC_DIR / "api-client.js"
        if api_client_path.is_file():
            js_code = api_client_path.read_text(encoding="utf-8")
            html = html.replace('<script src="./api-client.js"></script>', f'<script>\n{js_code}\n</script>')
        html = html.replace("__NEXUS_ADAPTER_TOKEN__", ADAPTER_TOKEN)
        html = html.replace("__NEXUS_ADAPTER_ORIGIN__", ALLOWED_ORIGIN)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; connect-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, request_path: str) -> None:
        filename, content_type = STATIC_FILES[request_path]
        path = STATIC_DIR / filename
        if not path.is_file():
            self._json(404, {"error": "static_file_not_found"})
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        super().log_message(fmt, *args)


def main() -> None:
    _validate_run_ref(RUN_REF)
    if not RUNNER_PS1.is_file():
        raise SystemExit(f"Runner not found: {RUNNER_PS1}")
    server = ThreadingHTTPServer((HOST, PORT), AdapterHandler)
    print(f"Nexus Vector local adapter listening on {ALLOWED_ORIGIN}")
    print(f"Execution root: {ROOT}")
    print(f"Run ref: {RUN_REF}")
    print(f"Open {ALLOWED_ORIGIN}/ in a browser.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down adapter.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

"""Authenticated, loopback-only GLM gateway with idle unloading and CPU fallback."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import select
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from originrag.model_hardware import foreign_gpu_jobs
from originrag.model_runtime import ModelManager, atomic_json, http_json
from originrag.cancellation import Cancellation, TurnCancelled, cancellation_scope, checkpoint, urlopen

MODEL = "glm-5.3"


class Gateway:
    def __init__(self, config):
        self.config = config
        self.manager = ModelManager(config["root"])
        self.manager.colibri = Path(config["colibri"])
        self.manager.state = Path(config["models"])
        self.lock = threading.Lock()
        self.admission = threading.BoundedSemaphore(2)
        self.last_used = time.monotonic()
        self.active = False
        self.stopped = threading.Event()
        threading.Thread(target=self.reaper, daemon=True).start()

    def status(self):
        process = self.manager.process
        return {"model": MODEL, "loaded": bool(process and process.poll() is None),
                "active": self.active, "accelerator": self.manager.accelerator,
                "gpu_policy": self.config.get("gpu_policy", "auto"),
                "idle_seconds": self.config.get("idle_seconds", 120)}

    def reaper(self):
        while not self.stopped.wait(5):
            if self.lock.acquire(blocking=False):
                try:
                    if time.monotonic() - self.last_used >= self.config.get("idle_seconds", 120):
                        self.manager.close()
                finally:
                    self.lock.release()

    @contextmanager
    def session(self):
        if not self.admission.acquire(blocking=False):
            raise RuntimeError("GLM is busy. Please retry shortly.")
        locked = False
        try:
            deadline = time.monotonic() + 30
            while not locked and time.monotonic() < deadline:
                checkpoint()
                locked = self.lock.acquire(timeout=0.1)
            if not locked:
                raise RuntimeError("GLM queue wait exceeded 30 seconds.")
            self.active = True
            if (self.manager.process and (self.manager.accelerator or "").startswith("cuda")
                    and foreign_gpu_jobs(self.manager.process.pid)):
                self.manager.close()
            try:
                url = self.manager.start_colibri(
                    "fast", MODEL, gpu_policy=self.config.get("gpu_policy", "auto"))
            except (ValueError, TimeoutError, OSError):
                # Retry startup, never an already submitted generation, on CPU.
                self.manager.close()
                url = self.manager.start_colibri("fast", MODEL, gpu_policy="cpu")
            yield url, self.manager.engine_api_key
        finally:
            if locked:
                self.active = False
                self.last_used = time.monotonic()
                self.lock.release()
            self.admission.release()

    def close(self):
        self.stopped.set()
        self.manager.close()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, address, gateway, key):
        self.gateway, self.key = gateway, key
        self.connections = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)

    def process_request(self, request, address):
        if not self.connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.connections.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.connections.release()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self, *_):
        pass

    def reply(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def authorised(self):
        host = self.headers.get("Host", "").split(":")[0]
        supplied = self.headers.get("Authorization", "")
        if host not in {"127.0.0.1", "localhost"} or self.headers.get("Origin"):
            self.reply(403, {"error": {"message": "Use an authenticated local API client or SSH tunnel."}})
            return False
        if not hmac.compare_digest(supplied, "Bearer " + self.server.key):
            self.reply(401, {"error": {"message": "An API key is required."}})
            return False
        return True

    def do_GET(self):
        if not self.authorised():
            return
        if self.path == "/health":
            self.reply(200, self.server.gateway.status())
        elif self.path == "/v1/models":
            self.reply(200, {"object": "list", "data": [
                {"id": MODEL, "object": "model", "owned_by": "local"}]})
        else:
            self.reply(404, {"error": {"message": "Unknown endpoint."}})

    def do_POST(self):
        if not self.authorised():
            return
        if self.path != "/v1/chat/completions":
            self.reply(404, {"error": {"message": "Unknown endpoint."}})
            return
        headers_sent = False
        try:
            if self.headers.get("Transfer-Encoding"):
                raise ValueError("Chunked request bodies are not supported.")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 2_000_000:
                raise ValueError("Request must be between 1 byte and 2 MB.")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict) or body.get("model") != MODEL:
                raise ValueError("Choose model glm-5.3.")
            if not isinstance(body.get("messages"), list) or not body["messages"]:
                raise ValueError("A nonempty messages list is required.")
            if not isinstance(body.get("stream", False), bool):
                raise ValueError("stream must be a boolean.")
            with self.disconnect_scope(), self.server.gateway.session() as (url, key):
                checkpoint()
                request = urllib.request.Request(
                    url + self.path, data=json.dumps(body).encode(),
                    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                )
                with urlopen(request, timeout=1800) as response:
                    self.send_response(response.status)
                    self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    headers_sent = True
                    if body.get("stream"):
                        while line := response.readline(1_000_000):
                            self.wfile.write(line)
                            self.wfile.flush()
                    else:
                        self.wfile.write(response.read(8 * 1024 * 1024))
        except (BrokenPipeError, ConnectionResetError, TurnCancelled):
            pass
        except urllib.error.HTTPError as error:
            if not headers_sent:
                self.reply(error.code, {"error": {"message": error.read(65536).decode(errors="replace")}})
        except (ValueError, TypeError) as error:
            if not headers_sent:
                self.reply(400, {"error": {"message": str(error)}})
        except (OSError, RuntimeError, TimeoutError):
            if not headers_sent:
                self.reply(503, {"error": {"message": "GLM is busy or unavailable. Check the host service log."}})

    @contextmanager
    def disconnect_scope(self):
        token = Cancellation()
        stopped = threading.Event()
        def watch():
            while not stopped.wait(0.25):
                try:
                    readable, _, _ = select.select([self.connection], [], [], 0)
                    if readable and not self.connection.recv(1, socket.MSG_PEEK):
                        token.cancel()
                        return
                except OSError:
                    token.cancel()
                    return
        thread = threading.Thread(target=watch, daemon=True)
        thread.start()
        try:
            with cancellation_scope(token):
                yield
        finally:
            stopped.set()
            thread.join(timeout=1)


def load_config(path):
    config = json.loads(Path(path).read_text())
    for name in ["root", "colibri", "models", "state"]:
        if not Path(config[name]).is_absolute():
            raise ValueError(f"{name} must be an absolute host path.")
    return config


def key_for(config):
    state = Path(config["state"])
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state / "api.key"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as out:
            out.write(secrets.token_urlsafe(32) + "\n")
    if path.stat().st_mode & 0o077:
        raise ValueError("API key file must be private (chmod 600).")
    key = path.read_text().strip()
    if len(key) < 32:
        raise ValueError("API key is missing or too short.")
    return key


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("action", choices=["serve", "start", "stop", "status", "chat"])
    args = parser.parse_args()
    config = load_config(args.config)
    state = Path(config["state"])
    key = key_for(config)
    base = f"http://127.0.0.1:{int(config.get('port', 8766))}"
    if args.action == "status":
        print(json.dumps(http_json(base + "/health", api_key=key), indent=2))
    elif args.action == "start":
        try:
            http_json(base + "/health", api_key=key)
            print("GLM service is already running.")
            return
        except OSError:
            pass
        with (state / "service.log").open("ab") as log:
            subprocess.Popen(
                [sys.executable, "-m", "originrag.colibri_service", "--config",
                 str(Path(args.config).resolve()), "serve"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
            )
        print("Starting GLM service. Check status before sending a request.")
    elif args.action == "stop":
        pid = json.loads((state / "pid.json").read_text())["pid"]
        command = Path(f"/proc/{int(pid)}/cmdline").read_bytes().split(b"\0")
        if b"originrag.colibri_service" not in command or str(Path(args.config).resolve()).encode() not in command:
            raise ValueError("PID does not belong to this service.")
        os.kill(pid, signal.SIGTERM)
        print("Stopping GLM service.")
    elif args.action == "serve":
        import fcntl
        with (state / "service.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            gateway = Gateway(config)
            server = Server(("127.0.0.1", int(config.get("port", 8766))), gateway, key)
            atomic_json(state / "pid.json", {"pid": os.getpid()})
            def shutdown(*_):
                threading.Thread(target=server.shutdown, daemon=True).start()
            signal.signal(signal.SIGTERM, shutdown)
            signal.signal(signal.SIGINT, shutdown)
            try:
                server.serve_forever()
            finally:
                server.server_close()
                gateway.close()
    else:
        history = []
        print("GLM-5.3 · /exit to leave · first answer loads the model")
        while True:
            try:
                prompt = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if prompt == "/exit":
                break
            if not prompt:
                continue
            messages = history + [{"role": "user", "content": prompt}]
            start = time.monotonic()
            body = {"model": MODEL, "messages": messages, "max_tokens": args.max_tokens,
                    "stream": True, "stream_options": {"include_usage": True}}
            request = urllib.request.Request(
                base + "/v1/chat/completions", data=json.dumps(body).encode(),
                headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            )
            text, usage, finish, first = "", {}, None, None
            print("\nGLM (loading / generating): ", end="", flush=True)
            try:
                with urllib.request.urlopen(request, timeout=1800) as response:
                    for line in response:
                        if not line.startswith(b"data: "):
                            continue
                        data = line[6:].strip()
                        if data == b"[DONE]":
                            break
                        event = json.loads(data)
                        if event.get("error"):
                            raise ValueError(str(event["error"]))
                        usage = event.get("usage") or usage
                        for choice in event.get("choices", []):
                            piece = choice.get("delta", {}).get("content") or ""
                            if piece and first is None:
                                first = time.monotonic() - start
                            text += piece
                            print(piece, end="", flush=True)
                            finish = choice.get("finish_reason") or finish
            except KeyboardInterrupt:
                print("\nCancelled.")
                break
            except (OSError, ValueError) as error:
                print(f"\nRequest failed: {error}")
                continue
            print()
            if finish == "length":
                print("[Output limit reached; answer may be incomplete.]")
            elif finish == "stop" and text:
                history = messages + [{"role": "assistant", "content": text}]
            else:
                print("[No complete answer returned.]")
            elapsed = time.monotonic() - start
            count = usage.get("completion_tokens", 0)
            if first is not None:
                print(f"[First text after {first:.1f}s]")
            print(f"[{count} tokens · {elapsed:.1f}s · {count / elapsed:.2f} tokens/s including loading/prompt]")


if __name__ == "__main__":
    main()

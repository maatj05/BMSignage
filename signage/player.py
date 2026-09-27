"""Drives a single long-running mpv process over its JSON IPC socket.

mpv renders straight to the screen (no desktop needed) and uses the Pi's
hardware video decoder. When nothing is loaded it shows a black screen.
"""

import json
import logging
import os
import socket
import subprocess
import time
from pathlib import Path

from .playlist import Slide

log = logging.getLogger(__name__)

BASE_ARGS = [
    "--idle=yes",
    "--force-window=yes",
    "--fullscreen",
    "--no-terminal",
    "--no-osc",
    "--osd-level=0",
    "--no-input-default-bindings",
    "--cursor-autohide=always",
]


class Mpv:
    def __init__(self, extra_args: list[str], socket_path: str):
        self.extra_args = extra_args
        self.socket_path = socket_path
        self.process: subprocess.Popen | None = None
        self.sock: socket.socket | None = None
        self._buffer = b""
        self._events: list[dict] = []
        self._request_id = 0

    # --- process management -------------------------------------------------

    def ensure_running(self) -> None:
        if self.process and self.process.poll() is None and self.sock:
            return
        self.close()
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        args = ["mpv", *BASE_ARGS, f"--input-ipc-server={self.socket_path}", *self.extra_args]
        log.info("Starting %s", " ".join(args))
        self.process = subprocess.Popen(args)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError(f"mpv exited with code {self.process.returncode}")
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(self.socket_path)
                self.sock = sock
                return
            except (FileNotFoundError, ConnectionRefusedError):
                sock.close()
                time.sleep(0.2)
        raise RuntimeError("mpv IPC socket did not appear")

    def close(self) -> None:
        if self.sock:
            self.sock.close()
            self.sock = None
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process = None
        self._buffer, self._events = b"", []

    # --- IPC ----------------------------------------------------------------

    def _read_message(self, timeout: float) -> dict | None:
        while b"\n" not in self._buffer:
            self.sock.settimeout(max(timeout, 0.01))
            try:
                data = self.sock.recv(65536)
            except socket.timeout:
                return None
            if not data:
                raise ConnectionError("mpv closed the IPC connection")
            self._buffer += data
        line, self._buffer = self._buffer.split(b"\n", 1)
        return json.loads(line) if line.strip() else None

    def command(self, *args):
        self._request_id += 1
        request_id = self._request_id
        payload = json.dumps({"command": list(args), "request_id": request_id}) + "\n"
        self.sock.sendall(payload.encode())
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            msg = self._read_message(deadline - time.monotonic())
            if msg is None:
                continue
            if "event" in msg:
                self._events.append(msg)
            elif msg.get("request_id") == request_id:
                if msg.get("error") != "success":
                    log.warning("mpv %s -> %s", args, msg.get("error"))
                return msg.get("data")
        raise TimeoutError(f"no reply from mpv to {args}")

    def _wait_event(self, names: set[str], timeout: float | None) -> dict | None:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            while self._events:
                event = self._events.pop(0)
                if event["event"] in names:
                    return event
            remaining = 1.0 if deadline is None else deadline - time.monotonic()
            if remaining <= 0:
                return None
            msg = self._read_message(min(remaining, 1.0))
            if msg and "event" in msg:
                self._events.append(msg)

    # --- high level ---------------------------------------------------------

    def apply_settings(self, sound: bool, scaling: str, rotate: int) -> None:
        self.ensure_running()
        self.command("set_property", "mute", not sound)
        self.command("set_property", "panscan", 1.0 if scaling == "fill" else 0.0)
        self.command("set_property", "video-rotate", rotate)

    def play(self, slide: Slide) -> str | None:
        """Shows a slide and blocks until it is done. Returns the end reason."""
        self.ensure_running()
        if slide.kind == "image":
            self.command("set_property", "image-display-duration", slide.duration)
        self._events.clear()
        self.command("loadfile", str(slide.path), "replace")
        # Skip the end-file of whatever was playing before; ours follows start-file.
        if not self._wait_event({"start-file"}, timeout=15):
            log.warning("mpv did not start %s", slide.path.name)
            return None
        if slide.kind == "image":
            timeout = slide.duration + 15
        else:
            timeout = slide.duration if slide.duration > 0 else None
        event = self._wait_event({"end-file"}, timeout)
        if event is None:
            return "timeout"  # video longer than allowed; next loadfile replaces it
        if event.get("reason") == "error":
            log.warning("mpv could not play %s: %s", slide.path.name, event.get("file_error"))
        return event.get("reason")

    def stop(self) -> None:
        """Clears the screen to black."""
        self.ensure_running()
        self.command("stop")

"""Capture the highlighted TSGCODE demo lineage from the local frontend.

This helper uses Chrome DevTools Protocol directly so it does not require
Selenium or Playwright. Run it while the frontend and backend are serving.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path


FIELD_NODE_ID = "59f37279-3a9e-4025-956c-088a0c8f217d:6734e211-63f0-4390-ac39-a5be815b5af5"
DEFAULT_BROWSER = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
FALLBACK_BROWSER = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


class CdpClient:
    def __init__(self, websocket_url: str) -> None:
        parsed = urllib.parse.urlparse(websocket_url)
        self.socket = socket.create_connection((parsed.hostname, parsed.port or 80), timeout=10)
        self.next_id = 1
        key = base64.b64encode(secrets.token_bytes(16)).decode("ascii")
        target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        request = (
            f"GET {target} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port or 80}\r\n"
            "Origin: http://127.0.0.1\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.socket.sendall(request.encode("ascii"))
        response = self._read_http_response()
        if " 101 " not in response.splitlines()[0]:
            raise RuntimeError(f"WebSocket upgrade failed: {response.splitlines()[0]}")

    def _read_http_response(self) -> str:
        chunks = bytearray()
        while b"\r\n\r\n" not in chunks:
            chunks.extend(self.socket.recv(4096))
        return chunks.decode("latin-1")

    def _read_exact(self, length: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < length:
            chunks.extend(self.socket.recv(length - len(chunks)))
        return bytes(chunks)

    def _send_text(self, payload: str) -> None:
        raw = payload.encode("utf-8")
        mask = secrets.token_bytes(4)
        length = len(raw)
        header = bytearray([0x81])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(raw))
        self.socket.sendall(bytes(header) + mask + masked)

    def _read_text(self) -> str:
        first, second = self._read_exact(2)
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._read_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._read_exact(8))[0]
        if second & 0x80:
            mask = self._read_exact(4)
        else:
            mask = None
        payload = self._read_exact(length)
        if mask:
            payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        if opcode == 0x8:
            raise RuntimeError("Browser closed the DevTools connection")
        if opcode == 0x9:
            return self._read_text()
        return payload.decode("utf-8")

    def command(self, method: str, params: dict | None = None) -> dict:
        command_id = self.next_id
        self.next_id += 1
        self._send_text(json.dumps({"id": command_id, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self._read_text())
            if message.get("id") != command_id:
                continue
            if "error" in message:
                raise RuntimeError(f"{method} failed: {message['error']}")
            return message.get("result", {})

    def evaluate(self, expression: str):
        result = self.command(
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
        )
        value = result.get("result", {})
        if value.get("subtype") == "error":
            raise RuntimeError(value.get("description", "JavaScript evaluation failed"))
        return value.get("value")

    def close(self) -> None:
        self.socket.close()


def wait_for(predicate, description: str, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for {description}")


def wait_for_json(url: str, timeout: float = 15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return json.load(response)
        except Exception:
            time.sleep(0.25)
    raise TimeoutError(f"Timed out waiting for {url}")


def create_tab(port: int, url: str) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(url, safe='')}",
        method="PUT",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)


def click(client: CdpClient, expression: str, description: str) -> None:
    if not client.evaluate(expression):
        raise RuntimeError(f"Could not click {description}")
    time.sleep(0.8)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="docs/images/tsgcode-highlighted-lineage.png",
        help="Screenshot path relative to the frontend directory.",
    )
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parents[1]
    output = (project_dir / args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    browser_path = DEFAULT_BROWSER if DEFAULT_BROWSER.exists() else FALLBACK_BROWSER
    if not browser_path.exists():
        raise RuntimeError("Chrome or Edge is required to capture the screenshot")

    profile_dir = Path(tempfile.mkdtemp(prefix="tsgcode-lineage-browser-"))
    port = 9237
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    browser_process = subprocess.Popen(
        [
            str(browser_path),
            "--headless=new",
            f"--remote-debugging-port={port}",
            "--remote-allow-origins=*",
            f"--user-data-dir={profile_dir}",
            "--disable-gpu",
            "--no-sandbox",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=2400,1400",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )

    client = None
    try:
        wait_for_json(f"http://127.0.0.1:{port}/json/version")
        tab = create_tab(port, "http://127.0.0.1:5176")
        client = CdpClient(tab["webSocketDebuggerUrl"])
        client.command("Page.enable")
        client.command("Runtime.enable")
        client.command(
            "Emulation.setDeviceMetricsOverride",
            {"width": 2400, "height": 1400, "deviceScaleFactor": 1, "mobile": False},
        )
        client.command("Page.navigate", {"url": "http://127.0.0.1:5176"})
        wait_for(lambda: client.evaluate("document.readyState === 'complete'"), "frontend page load")
        wait_for(
            lambda: client.evaluate(
                "[...document.querySelectorAll('.top-switcher button')].some(button => button.innerText.includes('Lineage Explorer'))"
            ),
            "lineage explorer switch",
        )
        click(
            client,
            """(() => {
              const button = [...document.querySelectorAll('.top-switcher button')]
                .find(item => item.innerText.includes('Lineage Explorer'));
              if (!button) return false;
              button.click();
              return true;
            })()""",
            "lineage explorer switch",
        )
        wait_for(
            lambda: client.evaluate("Boolean(document.querySelector('input[placeholder^=\"Search by name\"]'))"),
            "lineage search input",
        )

        client.evaluate(
            """
            (() => {
              const input = document.querySelector('input[placeholder^="Search by name"]');
              input.focus();
              return true;
            })()
            """
        )
        client.command("Input.insertText", {"text": FIELD_NODE_ID})
        time.sleep(0.5)
        click(
            client,
            "(() => { const button = document.querySelector('.plex-search-row button'); if (!button) return false; button.click(); return true; })()",
            "TSGCODE search",
        )
        wait_for(lambda: client.evaluate("document.querySelectorAll('.plex-results button').length > 0"), "TSGCODE search results")
        click(
            client,
            "(() => { const button = document.querySelector('.plex-results button'); if (!button) return false; button.click(); return true; })()",
            "TSGCODE search result",
        )
        wait_for(lambda: client.evaluate("document.querySelectorAll('.plex-node-card').length === 1"), "initial TSGCODE card")

        click(
            client,
            "(() => { const button = document.querySelector('.plex-row-expand-downstream'); if (!button) return false; button.click(); return true; })()",
            "TSGCODE downstream expansion",
        )
        wait_for(
            lambda: client.evaluate(
                "[...document.querySelectorAll('.plex-node-card')].some(card => card.innerText.includes('CLB > OAD Forbearance'))"
            ),
            "CLB > OAD Forbearance processing card",
        )
        click(
            client,
            """(() => {
              const card = [...document.querySelectorAll('.plex-node-card')]
                .find(item => item.innerText.includes('CLB > OAD Forbearance'));
              const button = card && card.querySelector('.plex-grouped-item .plex-row-expand-downstream');
              if (!button) return false;
              button.click();
              return true;
            })()""",
            "OAD processing item downstream expansion",
        )
        wait_for(
            lambda: client.evaluate(
                "[...document.querySelectorAll('.plex-node-card')].some(card => card.innerText.includes('Source') && card.innerText.includes('OAD'))"
            ),
            "OAD source card",
        )
        click(
            client,
            """(() => {
              const card = [...document.querySelectorAll('.plex-node-card')]
                .find(item => item.innerText.includes('Source') && item.innerText.includes('OAD'));
              const button = card && card.querySelector('.plex-expand-downstream');
              if (!button) return false;
              button.click();
              return true;
            })()""",
            "OAD source downstream expansion",
        )
        wait_for(
            lambda: client.evaluate(
                "[...document.querySelectorAll('.plex-node-card')].some(card => card.innerText.includes(\"OAD : OAD - Outil d'aide\"))"
            ),
            "OAD operational usage",
        )

        click(
            client,
            "(() => { const button = document.querySelector('.plex-node-menu-trigger'); if (!button) return false; button.click(); return true; })()",
            "TSGCODE node menu",
        )
        click(
            client,
            """(() => {
              const button = [...document.querySelectorAll('.plex-node-menu-popover button')]
                .find(item => item.innerText.includes('Highlight visible branch'));
              if (!button) return false;
              button.click();
              return true;
            })()""",
            "visible branch highlight action",
        )
        click(
            client,
            """(() => {
              const button = document.querySelector('.plex-highlight-swatch[title="#F59E0B"]');
              if (!button) return false;
              button.click();
              return true;
            })()""",
            "highlight color",
        )
        click(
            client,
            "(() => { const button = document.querySelector('button[title=\"Fit view\"]'); if (!button) return false; button.click(); return true; })()",
            "fit view",
        )
        time.sleep(1.5)
        client.evaluate(
            """
            (() => {
              const shell = document.querySelector('.plex-shell');
              shell.style.setProperty('--plex-left-width', '260px');
              shell.style.setProperty('--plex-right-width', '300px');
              return true;
            })()
            """
        )
        time.sleep(0.5)

        screenshot = client.command("Page.captureScreenshot", {"format": "png", "fromSurface": True})
        output.write_bytes(base64.b64decode(screenshot["data"]))
        cards = client.evaluate(
            "[...document.querySelectorAll('.plex-node-card')].map(card => card.innerText.replace(/\\s+/g, ' ').slice(0, 120))"
        )
        print(f"Captured {len(cards)} cards to {output}")
        for card in cards:
            print(f"- {card}")
    finally:
        if client:
            client.close()
        browser_process.terminate()
        try:
            browser_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            browser_process.kill()
        shutil.rmtree(profile_dir, ignore_errors=True)


if __name__ == "__main__":
    main()

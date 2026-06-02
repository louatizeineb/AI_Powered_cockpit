"""Capture the highlighted ALM source-to-usage demo from the local frontend."""

from __future__ import annotations

import argparse
import base64
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from capture_tsgcode_lineage_screenshot import (
    CdpClient,
    DEFAULT_BROWSER,
    FALLBACK_BROWSER,
    click,
    create_tab,
    wait_for,
    wait_for_json,
)


SOURCE_NODE_ID = "59f37279-3a9e-4025-956c-088a0c8f217d:1ca03d5e-6d24-41de-aa0a-39693ccc3404"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        default="docs/images/alm-source-to-usage-highlighted.png",
        help="Screenshot path relative to the frontend directory.",
    )
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parents[1]
    output = (project_dir / args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    browser_path = DEFAULT_BROWSER if DEFAULT_BROWSER.exists() else FALLBACK_BROWSER
    if not browser_path.exists():
        raise RuntimeError("Chrome or Edge is required to capture the screenshot")

    profile_dir = Path(tempfile.mkdtemp(prefix="alm-source-usage-browser-"))
    port = 9238
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
        client.command("Input.insertText", {"text": SOURCE_NODE_ID})
        time.sleep(0.5)
        click(
            client,
            "(() => { const button = document.querySelector('.plex-search-row button'); if (!button) return false; button.click(); return true; })()",
            "ALM source search",
        )
        wait_for(lambda: client.evaluate("document.querySelectorAll('.plex-results button').length > 0"), "ALM search results")
        click(
            client,
            "(() => { const button = document.querySelector('.plex-results button'); if (!button) return false; button.click(); return true; })()",
            "ALM search result",
        )
        wait_for(lambda: client.evaluate("document.querySelectorAll('.plex-node-card').length === 1"), "initial ALM source card")

        click(
            client,
            "(() => { const button = document.querySelector('.plex-node-card .plex-expand-downstream'); if (!button) return false; button.click(); return true; })()",
            "ALM source downstream expansion",
        )
        wait_for(
            lambda: client.evaluate(
                "[...document.querySelectorAll('.plex-node-card')].some(card => card.innerText.includes('ALM : ALM'))"
            ),
            "ALM operational usage",
        )

        click(
            client,
            "(() => { const button = document.querySelector('.plex-node-menu-trigger'); if (!button) return false; button.click(); return true; })()",
            "ALM source menu",
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
            "[...document.querySelectorAll('.plex-node-card')].map(card => card.innerText.replace(/\\s+/g, ' ').slice(0, 160))"
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

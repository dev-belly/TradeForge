#!/usr/bin/env python3
"""Capture the Streamlit dashboard, and fail if it raised.

Two jobs, and the second is the important one.

**Screenshots.** `make screenshots` renders each tab to `artifacts/screenshots/`
so the README can show what the dashboard actually looks like instead of
describing it.

**Smoke test.** Streamlit renders on the client over a websocket. A plain HTTP
request returns 200 as soon as the server is listening, *before* `render()` has
run, so `curl` cannot tell a working dashboard from one that raises on its first
line. This script drives a real browser, waits for the page to paint, and then
looks for Streamlit's exception element. `--check-only` exits non-zero on any
error, which is what CI and `make screenshots` use.

The readiness predicate is the whole trick: capturing on document load gives you
the skeleton loader, which looks like a rendering failure and is not one.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import httpx
import websockets.sync.client as ws_client

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts" / "screenshots"


def dashboard_tabs() -> tuple[str, ...]:
    """The tab labels, read from the dashboard itself.

    Read rather than duplicated: a second copy of this list would drift, and the
    failure mode is a capture run that silently screenshots the same tab six
    times or errors on a label that no longer exists.

    Tabs are clicked by label rather than deep-linked, because Streamlit's client
    router renders "Page not found" over a perfectly good page when a sub-route
    is requested directly.
    """
    src = ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        from tradeforge.interfaces.dashboard import TABS
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise SystemExit(
            f"cannot import the dashboard to read its tab labels ({exc}). "
            "Install the dashboard extra: pip install -e '.[full]'."
        ) from exc
    return tuple(TABS)


READY_JS = """
(() => {
  const app = document.querySelector('[data-testid="stApp"]');
  if (!app) return 'no-app';
  if (document.querySelector('[data-testid="stSkeleton"]')) return 'skeleton';
  if (document.querySelector('[data-testid="stStatusWidget"]')) return 'running';
  const heads = [...document.querySelectorAll('h1,h2,h3')].map(h => h.innerText.trim());
  if (!heads.some(h => h.includes(__TITLE__))) return 'wrong-page:' + heads.join('|');
  const text = app.innerText.trim();
  if (text.length < 400) return 'thin:' + text.length;
  return 'ready:' + text.length;
})()
"""

#: Streamlit renders an uncaught exception into this element rather than
#: returning a non-200, which is why a status-code check is not enough.
ERROR_JS = """
(() => {
  const nodes = [...document.querySelectorAll('[data-testid="stException"], .stException')];
  if (!nodes.length) return null;
  return nodes.map(n => n.innerText.trim()).join('\\n---\\n');
})()
"""


def find_browser() -> str:
    """A headless Chromium, preferring one already downloaded."""
    candidates: list[Path] = []
    for cache in (
        Path.home() / "Library/Caches/ms-playwright",
        Path.home() / ".cache/ms-playwright",
    ):
        candidates += sorted(
            cache.glob("chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell")
        )
        candidates += sorted(
            cache.glob("chromium-*/chrome-mac*/Chromium.app/Contents/MacOS/Chromium")
        )
    for app in (
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
    ):
        candidates.append(app)
    for path in candidates:
        if path.exists():
            return str(path)
    for name in ("chrome-headless-shell", "chromium", "google-chrome", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    raise SystemExit(
        "no headless browser found. Install one with `python -m playwright install "
        "chromium`, or set CHROME=/path/to/chrome."
    )


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class DevTools:
    """Minimal CDP client. Five methods, no Playwright."""

    def __init__(self, ws_url: str) -> None:
        self._ws = ws_client.connect(ws_url, max_size=None, open_timeout=30)
        self._id = 0

    def call(self, method: str, **params: object) -> dict:
        self._id += 1
        message_id = self._id
        self._ws.send(json.dumps({"id": message_id, "method": method, "params": params}))
        while True:
            reply = json.loads(self._ws.recv())
            if reply.get("id") == message_id:
                if "error" in reply:
                    raise RuntimeError(f"{method}: {reply['error']}")
                return reply.get("result", {})

    def evaluate(self, expression: str) -> object:
        result = self.call(
            "Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True
        )
        return result.get("result", {}).get("value")

    def close(self) -> None:
        # Closing an already-dead socket must not mask the result of the run.
        with contextlib.suppress(Exception):
            self._ws.close()


#: A proxy that answers for 127.0.0.1 returns an HTML 502 instead of the real
#: response, which surfaces as a JSON decode error rather than a connection
#: error. Every localhost request in this script bypasses the environment proxy.
LOCAL_CLIENT = httpx.Client(trust_env=False, timeout=10.0)
LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def wait_for_health(base_url: str, timeout_s: float = 90.0) -> None:
    """Poll Streamlit's health endpoint. Never `sleep 5` and hope.

    A cold Streamlit process imports pandas, sklearn and plotly before it serves
    anything; on a slow machine that is well past any fixed sleep.
    """
    deadline = time.monotonic() + timeout_s
    last = ""
    while time.monotonic() < deadline:
        try:
            with LOCAL_OPENER.open(f"{base_url}/_stcore/health", timeout=5) as response:
                if response.read().decode().strip() == "ok":
                    return
        except (urllib.error.URLError, OSError) as exc:
            last = str(exc)
        time.sleep(1.0)
    raise SystemExit(f"dashboard did not become healthy within {timeout_s:.0f}s ({last})")


def wait_for_ready(devtools: DevTools, title: str, timeout_s: float = 60.0) -> str:
    """Poll the readiness predicate, reporting which condition failed."""
    script = READY_JS.replace("__TITLE__", json.dumps(title))
    deadline = time.monotonic() + timeout_s
    state = "no-app"
    while time.monotonic() < deadline:
        state = str(devtools.evaluate(script) or "no-app")
        if state.startswith("ready:"):
            return state
        time.sleep(0.5)
    raise SystemExit(
        f"dashboard never became ready (last state: {state}). "
        "'skeleton'/'running' means it is still working; 'thin:' means the shell "
        "rendered but no content arrived; 'wrong-page:' means a different page is up."
    )


def click_tab(devtools: DevTools, label: str) -> None:
    """Click a Streamlit tab by its visible label.

    Clicked rather than deep-linked: a direct request to a sub-route reaches the
    server before the client router has run.
    """
    # Streamlit renders tabs with `data-testid="stTab"`, not `button[role="tab"]`.
    # Selecting on the test id rather than a role keeps this working when the
    # surrounding markup changes.
    script = f"""
    (() => {{
      const tabs = [...document.querySelectorAll('[data-testid="stTab"]')];
      const target = tabs.find(t => t.innerText.trim().includes({json.dumps(label)}));
      if (!target) return 'missing:' + tabs.map(t => t.innerText.trim()).join('|');
      target.click();
      return 'clicked';
    }})()
    """
    result = str(devtools.evaluate(script))
    if result != "clicked":
        raise SystemExit(
            f"tab {label!r} not found ({result}). The dashboard's TABS tuple in "
            "this script and the labels in dashboard.py have drifted apart."
        )


def capture(devtools: DevTools, path: Path) -> int:
    result = devtools.call("Page.captureScreenshot", format="png", captureBeyondViewport=False)
    data = base64.b64decode(result["data"])
    path.write_bytes(data)
    return len(data)


def run(args: argparse.Namespace) -> int:
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    browser = args.chrome or find_browser()
    debug_port = free_port()
    profile = Path(tempfile.mkdtemp(prefix="tradeforge-capture-"))
    base_url = f"http://127.0.0.1:{args.port}"

    process = subprocess.Popen(
        [
            browser,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            f"--user-data-dir={profile}",
            f"--remote-debugging-port={debug_port}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    devtools: DevTools | None = None
    try:
        wait_for_health(base_url)

        ws_url = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not ws_url:
            try:
                listing = LOCAL_CLIENT.get(f"http://127.0.0.1:{debug_port}/json/list").json()
                for target in listing:
                    if target.get("type") == "page":
                        ws_url = target["webSocketDebuggerUrl"]
                        break
            except (httpx.HTTPError, KeyError):
                pass
            if not ws_url:
                time.sleep(0.5)
        if not ws_url:
            raise SystemExit("could not reach the browser's DevTools endpoint")

        devtools = DevTools(ws_url)
        devtools.call("Page.enable")
        devtools.call("Runtime.enable")
        devtools.call(
            "Emulation.setDeviceMetricsOverride",
            width=args.width,
            height=args.height,
            deviceScaleFactor=args.scale,
            mobile=False,
        )
        devtools.call("Page.navigate", url=base_url)
        wait_for_ready(devtools, "TradeForge")

        failures: list[str] = []
        for index, label in enumerate(dashboard_tabs()):
            if index:
                click_tab(devtools, label)
                time.sleep(1.5)  # let the tab body paint before looking for errors
            error = devtools.evaluate(ERROR_JS)
            if error:
                failures.append(f"[{label}]\n{error}")
                print(f"  ERROR in tab {label!r}", file=sys.stderr)
            if not args.check_only:
                slug = label.lower().replace(" ", "-")
                size = capture(devtools, output / f"dashboard-{index:02d}-{slug}.png")
                if size < 20_000:
                    print(
                        f"  warning: {slug}.png is only {size:,} bytes; that is "
                        "usually a skeleton or an error page",
                        file=sys.stderr,
                    )
                print(f"  {slug}: {size:,} bytes")

        if failures:
            print(
                "\nThe dashboard raised while rendering. A Streamlit exception is "
                "rendered into the page, so the HTTP status stays 200 and curl "
                "would not have caught this:\n",
                file=sys.stderr,
            )
            print("\n\n".join(failures), file=sys.stderr)
            return 1

        print("\ndashboard rendered every tab without raising")
        return 0
    finally:
        if devtools is not None:
            devtools.close()
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(profile, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8511, help="Where the dashboard is serving.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--scale", type=int, default=2, help="deviceScaleFactor.")
    parser.add_argument("--chrome", default=None, help="Explicit browser path.")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Render every tab and report exceptions, without writing screenshots.",
    )
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())

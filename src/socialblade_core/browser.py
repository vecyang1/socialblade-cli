"""Browser execution layer for Social Blade web extraction (Tier 2 `web_trpc`).

Why a browser at all: plain HTTP to socialblade.com is answered with a
Cloudflare challenge (HTTP 403). A real browser session on the same origin can
call the site's own JSON endpoints with `fetch()`.

Single source of truth: the in-page JavaScript functions below are defined once
and shared by every backend. Backends only differ in how they obtain a page:

- `ego`        local `ego-browser` CLI (optional, workstation-specific)
- `cdp`        attach to an already running Chrome (`--remote-debugging-port`)
- `playwright` launch system Chrome through Playwright (headed by default,
               because headless Chrome is challenged by Cloudflare)
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from .config import SocialBladeConfig

BROWSER_BACKENDS = ("ego", "cdp", "playwright")
ANCHOR_PATH = "/robots.txt"  # lightweight same-origin page with no ad scripts


class BrowserError(RuntimeError):
    """Raised when no backend can run, or a backend fails."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# In-page functions (SSOT). Each is an `async (arg) => {...}` expression.
# ---------------------------------------------------------------------------

STATS_FN_JS = r"""
async (p) => {
  async function fetchTextWithTimeout(url, ms) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), ms);
    try {
      const res = await fetch(url, { signal: ctrl.signal });
      const text = await res.text();
      return { ok: true, status: res.status, text };
    } catch (err) {
      return { ok: false, status: 0, error: String(err) };
    } finally {
      clearTimeout(timer);
    }
  }

  const htmlResp = await fetchTextWithTimeout(p.webPath, p.timeoutMs);
  if (!htmlResp.ok) {
    return { ok: false, status: 0, error: htmlResp.error || "Fetch failed" };
  }
  if (htmlResp.status === 404) {
    return { ok: false, status: 404, error: "Creator not found on Social Blade (HTTP 404)" };
  }
  if (htmlResp.status === 403) {
    return { ok: false, status: 403, error: "Blocked by Cloudflare challenge (HTTP 403). Use a headed browser or attach to your own Chrome via CDP." };
  }
  const match = htmlResp.text.match(/<script id="__NEXT_DATA__" type="application\/json">([\s\S]*?)<\/script>/);
  if (!match) {
    return { ok: false, status: htmlResp.status, error: "Missing __NEXT_DATA__ on Social Blade response (possible Cloudflare challenge)" };
  }

  const nextData = JSON.parse(match[1]);
  const queries = nextData?.props?.pageProps?.trpcState?.json?.queries || [];
  const userQ = queries.find(q => q.queryKey?.[0]?.[0] === p.platform && q.queryKey?.[0]?.[1] === "user");
  const ssrHistoryQ = queries.find(q => q.queryKey?.[0]?.[0] === p.platform && q.queryKey?.[0]?.[1] === "history");

  const userData = userQ?.state?.data;
  if (!userData || !userData.id) {
    return { ok: false, status: 404, error: "Creator record not found in Social Blade __NEXT_DATA__" };
  }

  const creatorId = String(userData.id);
  let historyRows = Array.isArray(ssrHistoryQ?.state?.data) ? ssrHistoryQ.state.data : [];
  const ssrHistoryCount = historyRows.length;
  let growthData = null;
  let monthlyRows = [];
  let trpcAuthRequired = false;
  let trpcStatus = null;

  try {
    const procs = [`${p.platform}.history`, `${p.platform}.growth`];
    const batchInput = {
      "0": { "json": { "id": creatorId, "limit": p.historyLimit } },
      "1": { "json": { "id": creatorId } }
    };
    if (p.includeMonthly) {
      procs.push(`${p.platform}.monthly`);
      batchInput["2"] = { "json": { "id": creatorId, "period": "monthly" } };
    }
    const trpcUrl = `/api/trpc/${procs.join(",")}?batch=1&input=${encodeURIComponent(JSON.stringify(batchInput))}`;
    const trpcResp = await fetchTextWithTimeout(trpcUrl, p.timeoutMs);
    trpcStatus = trpcResp.status;
    if (trpcResp.status === 401) {
      trpcAuthRequired = true;
    }
    if (trpcResp.ok && trpcResp.text) {
      const parsedTrpc = JSON.parse(trpcResp.text);
      if (parsedTrpc?.[0]?.error?.json?.data?.httpStatus === 401) {
        trpcAuthRequired = true;
      }
      const hData = parsedTrpc?.[0]?.result?.data?.json;
      if (Array.isArray(hData) && hData.length > 0) {
        historyRows = hData;
      }
      const gData = parsedTrpc?.[1]?.result?.data?.json;
      if (gData && typeof gData === "object") {
        growthData = gData;
      }
      if (p.includeMonthly) {
        const mData = parsedTrpc?.[2]?.result?.data?.json;
        if (Array.isArray(mData)) {
          monthlyRows = mData;
        }
      }
    }
  } catch (e) {
    // Keep the SSR history if the tRPC expansion fails; completeness receipt reports the gap.
  }

  return {
    ok: true,
    status: 200,
    userData,
    historyRows,
    ssrHistoryCount,
    growthData,
    monthlyRows,
    trpcAuthRequired,
    trpcStatus
  };
}
"""

FAVORITES_FN_JS = r"""
async (plat) => {
  const input = { "0": { "json": { "platform": plat } } };
  const url = `/api/trpc/account.favourites.get?batch=1&input=${encodeURIComponent(JSON.stringify(input))}`;
  const r = await fetch(url);
  let j = null;
  try { j = await r.json(); } catch (e) {
    return { ok: false, status: r.status, error: "Non-JSON response (HTTP " + r.status + ")" };
  }
  const err = j?.[0]?.error?.json;
  if (err) {
    return { ok: false, status: err.data?.httpStatus || r.status || 401, error: err.message || "UNAUTHORIZED" };
  }
  return { ok: true, status: r.status, items: j?.[0]?.result?.data?.json || [] };
}
"""


# ---------------------------------------------------------------------------
# Backend resolution (diagnosable: returns the reason for every decision)
# ---------------------------------------------------------------------------


def playwright_installed() -> bool:
    return importlib.util.find_spec("playwright") is not None


def backend_availability(config: "SocialBladeConfig") -> dict[str, dict[str, Any]]:
    """Report which backends can run right now and why."""
    ego_bin = str(config.ego_browser_bin.value or "")
    cdp_url = str(config.cdp_url.value or "")
    pw = playwright_installed()
    return {
        "ego": {
            "available": bool(ego_bin),
            "reason": f"ego-browser found at {ego_bin}" if ego_bin else "ego-browser not on PATH",
        },
        "cdp": {
            "available": bool(cdp_url) and pw,
            "reason": (
                f"attach to {cdp_url}"
                if cdp_url and pw
                else ("SOCIALBLADE_CDP_URL not set" if not cdp_url else "playwright package not installed")
            ),
        },
        "playwright": {
            "available": pw,
            "reason": (
                f"launch channel={config.browser_channel.value} headless={config.browser_headless.value}"
                if pw
                else "playwright package not installed (pip install playwright)"
            ),
        },
    }


def resolve_backend(config: "SocialBladeConfig") -> tuple[str | None, str]:
    """Pick the backend. Explicit choice wins; `auto` tries ego -> cdp -> playwright."""
    wanted = str(config.browser_backend.value or "auto").lower()
    avail = backend_availability(config)
    if wanted != "auto":
        if wanted not in BROWSER_BACKENDS:
            return None, f"unknown browser backend '{wanted}' (valid: auto, {', '.join(BROWSER_BACKENDS)})"
        info = avail[wanted]
        if info["available"]:
            return wanted, f"explicit ({config.browser_backend.source}): {info['reason']}"
        return None, f"explicit backend '{wanted}' unavailable: {info['reason']}"
    for name in BROWSER_BACKENDS:
        if avail[name]["available"]:
            return name, f"auto: {avail[name]['reason']}"
    return None, "no browser backend available: " + "; ".join(
        f"{k}: {v['reason']}" for k, v in avail.items()
    )


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


def _parse_json_lines(text: str) -> dict[str, Any]:
    lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("[ego-browser:")
    ]
    for ln in reversed(lines):
        if ln.startswith("{") and ln.endswith("}"):
            try:
                return json.loads(ln)
            except json.JSONDecodeError:
                continue
    raise BrowserError(f"Could not parse JSON from browser output: {' '.join(lines)[:300]}")


def _run_ego(fn_js: str, arg: Any, config: "SocialBladeConfig") -> dict[str, Any]:
    ego_bin = str(config.ego_browser_bin.value)
    web_base = str(config.web_base_url.value).rstrip("/")
    space = str(config.ego_task_space.value)
    timeout_sec = max(15.0, float(config.timeout_ms.value) / 1000.0 + 12.0)
    script = f"""
const webBase = {json.dumps(web_base)};
let task;
try {{ task = await taskSpace({json.dumps(space)}); }} catch (e) {{ task = await taskSpace(76); }}
const pages = await task.pages();
const page = pages.length > 0 ? task.page(pages[0].label) : await task.newPage();
const curUrl = await page.url();
if (!curUrl || !curUrl.startsWith(webBase + "/")) {{
  await page.goto(webBase + {json.dumps(ANCHOR_PATH)}, {{ waitUntil: "domcontentloaded", timeout: 15000 }});
}}
const result = await page.evaluate({fn_js.strip()}, {json.dumps(arg)});
console.log(JSON.stringify(result));
"""
    try:
        proc = subprocess.run(
            [ego_bin, "nodejs"], input=script, capture_output=True, text=True, timeout=timeout_sec
        )
    except subprocess.TimeoutExpired as exc:
        raise BrowserError(f"ego-browser timed out after {timeout_sec:.0f}s") from exc
    except FileNotFoundError as exc:
        raise BrowserError("ego-browser executable not found") from exc
    out = f"{proc.stdout or ''}\n{proc.stderr or ''}"
    try:
        return _parse_json_lines(out)
    except BrowserError:
        if proc.returncode != 0:
            raise BrowserError(f"ego-browser exited {proc.returncode}: {out.strip()[:300]}")
        raise


def _evaluate_on_anchor(page: Any, fn_js: str, arg: Any, config: "SocialBladeConfig") -> dict[str, Any]:
    web_base = str(config.web_base_url.value).rstrip("/")
    if not str(page.url or "").startswith(web_base + "/"):
        page.goto(web_base + ANCHOR_PATH, wait_until="domcontentloaded", timeout=int(config.timeout_ms.value) + 8000)
    result = page.evaluate(fn_js.strip(), arg)
    if not isinstance(result, dict):
        raise BrowserError(f"Unexpected in-page result type: {type(result).__name__}")
    return result


def _run_playwright(fn_js: str, arg: Any, config: "SocialBladeConfig") -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - guarded by availability check
        raise BrowserError("playwright package not installed (pip install playwright)") from exc

    profile_dir = Path(str(config.browser_profile_dir.value)).expanduser()
    profile_dir.mkdir(parents=True, exist_ok=True)
    channel = str(config.browser_channel.value or "") or None
    try:
        with sync_playwright() as p:
            kwargs: dict[str, Any] = {"headless": bool(config.browser_headless.value)}
            if channel and channel != "chromium":
                kwargs["channel"] = channel
            ctx = p.chromium.launch_persistent_context(str(profile_dir), **kwargs)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                return _evaluate_on_anchor(page, fn_js, arg, config)
            finally:
                ctx.close()
    except BrowserError:
        raise
    except Exception as exc:
        raise BrowserError(f"playwright backend failed: {str(exc).splitlines()[0][:250]}") from exc


def _run_cdp(fn_js: str, arg: Any, config: "SocialBladeConfig") -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise BrowserError("playwright package not installed (pip install playwright)") from exc
    cdp_url = str(config.cdp_url.value)
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(cdp_url, timeout=int(config.timeout_ms.value))
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            page = ctx.new_page()  # own tab: never hijack the user's open tabs
            try:
                return _evaluate_on_anchor(page, fn_js, arg, config)
            finally:
                page.close()
    except BrowserError:
        raise
    except Exception as exc:
        raise BrowserError(f"cdp backend failed ({cdp_url}): {str(exc).splitlines()[0][:250]}") from exc


_RUNNERS = {"ego": _run_ego, "cdp": _run_cdp, "playwright": _run_playwright}


def evaluate_on_socialblade(fn_js: str, arg: Any, config: "SocialBladeConfig") -> tuple[dict[str, Any], str]:
    """Run an in-page function on the Social Blade origin. Returns (result, backend_name)."""
    backend, reason = resolve_backend(config)
    if backend is None:
        raise BrowserError(reason)
    return _RUNNERS[backend](fn_js, arg, config), backend

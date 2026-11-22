# -*- coding: utf-8 -*-
"""
⚡ Dedicated Telegram OTP Web Application Server
Pure standalone website engine with 0ms SSE push, Auto-Refresh (1s), and Dual-Account Round-Robin polling.
"""

import os
import sys

# Ensure project root is in sys.path
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

import time
import logging
import asyncio
from aiohttp import web

from website.worker import (
    recent_otps_buffer,
    recent_logs_buffer,
    seen_hashes,
    sse_clients,
    clients,
    check_account_records,
    start_worker_loop
)
from website.proxy_manager import proxy_mgr
import numbers_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("WebApp")

class WebLogHandler(logging.Handler):
    def emit(self, record):
        try:
            if "aiohttp.access" in record.name or "httpx" in record.name:
                return
            recent_logs_buffer.append(self.format(record))
        except Exception:
            pass

log_handler = WebLogHandler()
log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s]: %(message)s"))
logging.getLogger().addHandler(log_handler)

# Path to dashboard HTML
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_FILE = os.path.join(CURRENT_DIR, "dashboard.html")
if not os.path.exists(DASHBOARD_FILE):
    DASHBOARD_FILE = os.path.join(CURRENT_DIR, "templates", "dashboard.html")

DISABLE_WEBSITE = os.environ.get("DISABLE_WEBSITE", "true").lower() in ("true", "1", "yes")

async def handle_website_disabled(request):
    """Fallback handler when website is turned off."""
    if request.path == "/health":
        return web.json_response({
            "status": "online",
            "website": "disabled",
            "bot": "active",
            "total_seen": len(seen_hashes),
            "total_collected": numbers_store.get_collected_count()
        })
    disabled_html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Website Offline</title>
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; background: #0f172a; color: #f8fafc;">
  <div style="text-align: center; max-width: 500px; padding: 30px; background: #1e293b; border-radius: 12px; border: 1px solid #334155;">
    <h1 style="font-size: 2.2rem; margin-bottom: 10px;">⚠️ Website Offline</h1>
    <p style="font-size: 1.1rem; color: #94a3b8; line-height: 1.6;">The web dashboard has been turned off.<br>SMS monitoring and instant Telegram OTP dispatch remain fully active in the background.</p>
  </div>
</body>
</html>"""
    return web.Response(text=disabled_html, content_type="text/html", status=503)

# --- Endpoints ---

async def handle_dashboard(request):
    """Serves the live interactive dashboard HTML."""
    try:
        if os.path.exists(DASHBOARD_FILE):
            with open(DASHBOARD_FILE, "r", encoding="utf-8") as f:
                content = f.read()
            return web.Response(text=content, content_type="text/html", charset="utf-8")
        return web.Response(text="<h2>Dashboard file not found.</h2>", status=404)
    except Exception as e:
        return web.Response(text=f"Server error: {e}", status=500)

async def handle_stream(request):
    """Zero-delay Server-Sent Events (SSE) streaming endpoint."""
    response = web.StreamResponse(
        status=200,
        reason='OK',
        headers={
            'Content-Type': 'text/event-stream',
            'Cache-Control': 'no-cache, no-transform',
            'Connection': 'keep-alive',
            'Access-Control-Allow-Origin': '*'
        }
    )
    await response.prepare(request)
    q = asyncio.Queue(maxsize=200)
    sse_clients.add(q)
    try:
        await response.write(b": connected\n\n")
        while True:
            msg = await q.get()
            await response.write(msg.encode('utf-8'))
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        sse_clients.discard(q)
    return response

async def handle_recent_otps(request):
    """
    Returns recent OTP list directly from ultra-fast in-memory buffer (<0.1ms).
    Prevents redundant portal queries so the portal never encounters 429 rate limits.
    """
    return web.json_response({
        "status": "ok",
        "count": len(recent_otps_buffer),
        "otps": list(recent_otps_buffer)
    }, headers={'Access-Control-Allow-Origin': '*'})

async def handle_health(request):
    """Diagnostic health endpoint."""
    now = time.time()
    return web.json_response({
        "status": "online",
        "system": "Dedicated Pure Telegram OTP Web Hub (Modular website/)",
        "total_seen": len(seen_hashes),
        "total_collected_numbers": numbers_store.get_collected_count(),
        "total_consumed_numbers": numbers_store.get_consumed_count(),
        "total_proxies_loaded": len(proxy_mgr.proxies),
        "accounts": [
            {
                "username": c.username,
                "is_logged_in": c.is_logged_in,
                "proxy": c.current_proxy or "Direct (High-Speed)",
                "response_time": round(c.last_response_time, 2),
                "query_count": c.query_count,
                "is_rate_limited": c.rate_limited_until > now,
                "cooldown_remaining": max(0, round(c.rate_limited_until - now, 1))
            }
            for c in clients
        ],
        "active_sse_clients": len(sse_clients)
    })

async def handle_logs(request):
    """Returns recent server operational logs."""
    return web.Response(text="\n".join(list(recent_logs_buffer)), content_type="text/plain")

async def init_web_app():
    app = web.Application()
    if DISABLE_WEBSITE:
        logger.info("🔒 Website dashboard & APIs are DISABLED (Scraper running in background)")
        app.router.add_route("*", "/", handle_website_disabled)
        app.router.add_route("*", "/{tail:.*}", handle_website_disabled)
        return app

    app.router.add_get("/", handle_dashboard)
    app.router.add_get("/dashboard", handle_dashboard)
    app.router.add_get("/live", handle_dashboard)
    app.router.add_get("/api/stream", handle_stream)
    app.router.add_get("/api/recent-otps", handle_recent_otps)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/logs", handle_logs)
    return app

async def main():
    port = int(os.environ.get("PORT", 8080))
    app = await init_web_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("=" * 65)
    logger.info(f"⚡ Dedicated Real-Time OTP Web Server running on port {port}")
    logger.info("   - Running from dedicated 'website/' folder")
    logger.info("   - Dual Accounts: Atiar59_FD & Smswork21_FD (Round-Robin)")
    logger.info("   - Rate-Limit Shield & Proxy Switching active")
    logger.info("   - Auto-Refresh (1s) & 0ms SSE streaming ready")
    logger.info("=" * 65)

    # Launch background round-robin scraper
    await start_worker_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server terminated by user.")

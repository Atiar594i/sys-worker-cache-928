#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⚡ Dedicated High-Speed Web Server & Real-Time Telegram OTP Hub
Independent standalone system querying Flex SMS portal using two accounts:
  1. Atiar59_FD
  2. Smswork21_FD
Pushes new Telegram OTP codes to connected web clients with 0ms latency via Server-Sent Events (SSE).
Completely decoupled from python-telegram-bot to guarantee instantaneous website delivery.
"""

import os
import sys
import re
import json
import time
import email.utils
import logging
import asyncio
import collections
from datetime import datetime, timezone, timedelta
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("WebOTPHub")

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
DASHBOARD_FILE = os.path.join(os.path.dirname(__file__), "dashboard.html")

recent_logs_buffer = collections.deque(maxlen=500)
recent_otps_buffer = collections.deque(maxlen=200)
seen_hashes = set()
sse_clients = set()

class WebLogHandler(logging.Handler):
    def emit(self, record):
        try:
            recent_logs_buffer.append(self.format(record))
        except Exception:
            pass

log_handler = WebLogHandler()
log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s]: %(message)s"))
logger.addHandler(log_handler)

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading config: {e}")
    return {}

config = load_config()
BOT_TOKEN = config.get("bot_token", "8784442674:AAE-nwM7m7svAL9nxipswL8f6CwvssKfIyc")
GROUP_ID = config.get("group_id", "-1003996842463")

COUNTRY_INFO = {
    "998": ("Uzbekistan", "UZ", "🇺🇿"),
    "263": ("Zimbabwe", "ZW", "🇿🇼"),
    "855": ("Cambodia", "KH", "🇰🇭"),
    "258": ("Mozambique", "MZ", "🇲🇿"),
    "213": ("Algeria", "DZ", "🇩🇿"),
    "212": ("Morocco", "MA", "🇲🇦"),
    "232": ("Sierra Leone", "SL", "🇸🇱"),
    "229": ("Benin", "BJ", "🇧🇯"),
    "994": ("Azerbaijan", "AZ", "🇦🇿"),
    "62":  ("Indonesia", "ID", "🇮🇩"),
    "91":  ("India", "IN", "🇮🇳"),
    "880": ("Bangladesh", "BD", "🇧🇩"),
    "92":  ("Pakistan", "PK", "🇵🇰"),
    "234": ("Nigeria", "NG", "🇳🇬"),
    "254": ("Kenya", "KE", "🇰🇪"),
    "256": ("Uganda", "UG", "🇺🇬"),
    "255": ("Tanzania", "TZ", "🇹🇿"),
    "20":  ("Egypt", "EG", "🇪🇬"),
    "971": ("UAE", "AE", "🇦🇪"),
    "966": ("Saudi Arabia", "SA", "🇸🇦"),
    "968": ("Oman", "OM", "🇴🇲"),
    "965": ("Kuwait", "KW", "🇰🇼"),
    "974": ("Qatar", "QA", "🇶🇦"),
    "90":  ("Turkey", "TR", "🇹🇷"),
    "7":   ("Russia / Kazakhstan", "RU", "🇷🇺"),
    "380": ("Ukraine", "UA", "🇺🇦"),
    "44":  ("United Kingdom", "GB", "🇬🇧"),
    "1":   ("USA / Canada", "US", "🇺🇸"),
}

def get_country_info(phone):
    clean = re.sub(r'[^0-9]', '', str(phone))
    for prefix_len in (4, 3, 2, 1):
        if len(clean) >= prefix_len:
            pfx = clean[:prefix_len]
            if pfx in COUNTRY_INFO:
                name, iso, flag = COUNTRY_INFO[pfx]
                return name, iso, flag, pfx
    return f"Code +{clean[:3]}", "INTL", "🌐", clean[:3]

def extract_otp(message):
    if not message:
        return None
    raw = str(message)
    # Telegram code 12345
    m = re.search(r'Telegram\s+code\s*[:\s]*(\d{4,6})', raw, re.IGNORECASE)
    if m:
        return m.group(1)
    # 12345 is your Telegram code
    m = re.search(r'(\d{5,6})\s+is\s+your\s+Telegram\s+code', raw, re.IGNORECASE)
    if m:
        return m.group(1)
    # httpstmelogin12345
    m = re.search(r'login(\d{4,6})', raw, re.IGNORECASE)
    if m and "telegram" in raw.lower():
        return m.group(1)
    # Standalone code in Telegram message
    m = re.search(r'\b(\d{5,6})\b', raw)
    if m and "telegram" in raw.lower():
        return m.group(1)
    return None

def broadcast_to_sse(item):
    """Pushes a new Telegram OTP payload to all connected browser SSE clients instantly."""
    if not item or not item.get("otp"):
        return
    msg = f"data: {json.dumps(item)}\n\n"
    dead = []
    for q in list(sse_clients):
        try:
            q.put_nowait(msg)
        except Exception:
            dead.append(q)
    for dq in dead:
        sse_clients.discard(dq)

async def send_telegram_notification(msg_text):
    """Sends formatted Telegram message in background without blocking the web server."""
    if not BOT_TOKEN or not GROUP_ID:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": GROUP_ID,
        "text": msg_text,
        "parse_mode": "HTML"
    }
    for attempt in range(2):
        try:
            r = await asyncio.to_thread(requests.post, url, json=payload, timeout=4.0)
            if r.status_code == 200:
                break
        except Exception as e:
            logger.warning(f"Telegram notification attempt {attempt+1} error: {e}")
            await asyncio.sleep(0.3)

class FastPortalClient:
    """Dedicated fast portal scraper client with keep-alive connection pooling."""
    def __init__(self, username, password, base_url="http://168.119.13.175/ints"):
        self.username = username
        self.password = password
        self.base_url = base_url.rstrip("/")
        self.is_logged_in = False
        self.ajax_url = None
        self.echo_counter = 0
        self.last_response_time = 0.0
        self.rate_limited_until = 0.0
        self.session = None
        self._init_session()

    def _init_session(self):
        s = requests.Session()
        retries = Retry(total=1, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=retries)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        s.headers.update({
            'User-Agent': f'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
        })
        self.session = s

    def solve_captcha(self, text):
        m = re.search(r'(\d+)\s*([+\-*])\s*(\d+)\s*=\s*\?', text)
        if m:
            n1 = int(m.group(1))
            op = m.group(2)
            n2 = int(m.group(3))
            if op == '+': return n1 + n2
            if op == '-': return n1 - n2
            if op == '*': return n1 * n2
        return None

    def login(self):
        login_url = f"{self.base_url}/login"
        signin_url = f"{self.base_url}/signin"
        try:
            r = self.session.get(login_url, timeout=(2.0, 4.0))
            if r.status_code != 200:
                return False
            ans = self.solve_captcha(r.text)
            if ans is None:
                return False
            data = {
                'username': self.username,
                'password': self.password,
                'capt': ans
            }
            r_post = self.session.post(signin_url, data=data, timeout=(2.0, 4.0), allow_redirects=True)
            if r_post.status_code != 200:
                return False
            rep_url = f"{self.base_url}/agent/FailedSMSReports"
            r_rep = self.session.get(rep_url, timeout=(2.0, 4.0))
            if r_rep.status_code != 200:
                return False
            m = re.search(r'sAjaxSource":\s*"([^"]+)"', r_rep.text)
            if not m:
                return False
            self.ajax_url = f"{self.base_url}/agent/{m.group(1)}"
            self.is_logged_in = True
            self.rate_limited_until = 0.0
            logger.info(f"Fast Client login successful: {self.username}")
            return True
        except Exception as e:
            logger.error(f"Login error for {self.username}: {e}")
            self.is_logged_in = False
            return False

    def fetch_records(self, keyword="", display_length=40):
        if self.rate_limited_until > time.time():
            return None
        if not self.is_logged_in or not self.ajax_url:
            if not self.login():
                return None

        headers = {
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Referer': f"{self.base_url}/agent/FailedSMSReports",
            'Cache-Control': 'no-cache, no-store, must-revalidate',
        }
        self.echo_counter += 1
        params = {
            'sEcho': str(self.echo_counter),
            'iDisplayStart': '0',
            'iDisplayLength': str(display_length),
            'iSortingCols': '1',
            'iSortCol_0': '0',
            'sSortDir_0': 'desc',
            'bSortable_0': 'true',
            'bSearchable_0': 'true',
            'bSearchable_1': 'true',
            'bSearchable_2': 'true',
            'bSearchable_3': 'true',
            'bSearchable_4': 'true',
            'sSearch': keyword.strip() if keyword else '',
            '_': str(int(time.time() * 1000))
        }
        try:
            t0 = time.time()
            res = self.session.get(self.ajax_url, params=params, headers=headers, timeout=(2.0, 4.0))
            self.last_response_time = time.time() - t0

            if res.status_code == 429:
                logger.warning(f"Status 429 for {self.username}. Brief 1.5s cooldown.")
                self.rate_limited_until = time.time() + 1.5
                return None

            if res.status_code != 200 or "Direct Script Access Not Allowed" in res.text:
                self.is_logged_in = False
                return None

            data = res.json()
            raw_rows = data.get('aaData', [])
            records = []
            for row in raw_rows:
                if len(row) < 4: continue
                if not str(row[1]).strip() or not str(row[3]).strip(): continue
                records.append({
                    'time': str(row[0]).strip(),
                    'number': str(row[1]).strip(),
                    'cli': str(row[2]).strip(),
                    'message': str(row[3]).strip(),
                    'cause': str(row[4]).strip() if len(row) > 4 else ""
                })
            return records
        except Exception as e:
            logger.warning(f"Fetch error for {self.username}: {e}")
            self.rate_limited_until = time.time() + 0.5
            return None

# Initialize clients for both usernames
client1 = FastPortalClient("Atiar59_FD", "Atiar5942@#")
client2 = FastPortalClient("Smswork21_FD", "Smswork21_FD")
clients = [client1, client2]

async def check_account_records(client, use_keyword=True):
    kw = "Telegram" if use_keyword else ""
    records = await asyncio.to_thread(client.fetch_records, keyword=kw, display_length=35)
    if not records:
        return 0

    now_utc = datetime.now(timezone.utc)
    bd_tz = timezone(timedelta(hours=6))
    now_display = now_utc.astimezone(bd_tz).strftime("%Y-%m-%d %I:%M:%S %p")

    new_otps = 0
    records.sort(key=lambda r: str(r.get("time", "")))

    for rec in records:
        raw_msg = str(rec.get("message", "")).strip()
        raw_cli = str(rec.get("cli", "")).strip()
        
        # Check if Telegram related
        if "telegram" not in raw_msg.lower() and "telegram" not in raw_cli.lower():
            continue

        otp = extract_otp(raw_msg)
        if not otp:
            continue

        raw_num = str(rec.get("number", "")).strip()
        clean_num = re.sub(r'[^0-9]', '', raw_num)
        dedup_key = f"{clean_num}_OTP_{otp}"

        if dedup_key in seen_hashes:
            continue

        seen_hashes.add(dedup_key)
        new_otps += 1

        country_name, iso, flag, code = get_country_info(clean_num)
        panel_time_raw = str(rec.get("time", "")).strip()
        try:
            dt_p = datetime.strptime(panel_time_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            panel_display = dt_p.astimezone(bd_tz).strftime("%Y-%m-%d %I:%M:%S %p")
        except Exception:
            panel_display = panel_time_raw or now_display

        payload = {
            "id": dedup_key,
            "number": f"+{clean_num}",
            "clean_number": clean_num,
            "otp": otp,
            "account": client.username,
            "country_name": country_name,
            "country_flag": flag,
            "country_code": code,
            "cli": raw_cli or "Telegram",
            "message": raw_msg,
            "panel_time": panel_display,
            "received_time": now_display,
            "speed": "⚡ Instant Live",
            "timestamp": time.time()
        }

        # 1. PUSH TO WEB CLIENTS INSTANTLY (0ms latency!):
        recent_otps_buffer.appendleft(payload)
        broadcast_to_sse(payload)
        logger.info(f"🚀 [0ms Web Push] New OTP for {clean_num}: {otp} (Account: {client.username})")

        # 2. Telegram Group format and background dispatch:
        tg_text = (
            f"📩 <b>New SMS Received</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🕒 <b>Panel Time:</b> <code>{panel_display}</code>\n"
            f"⚡ <b>Delivery Time:</b> <code>{now_display}</code>\n"
            f"⏳ <b>Speed:</b> <code>Instant ⚡ (0ms)</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌍 <b>Country:</b> {flag} <b>{country_name}</b>\n"
            f"📱 <b>Number:</b> <code>+{clean_num}</code>\n"
            f"🏷 <b>CLI:</b> <b>{raw_cli or 'Telegram'}</b>\n"
            f"🔑 <b>OTP:</b> <code>{otp}</code>\n"
            f"💬 <b>Message:</b>\n"
            f"<blockquote>{raw_msg}</blockquote>\n"
            f"🔍 <b>Keyword:</b> <code>Telegram</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
        asyncio.create_task(send_telegram_notification(tg_text))

    return new_otps

async def background_scraper_loop():
    logger.info("Initializing dedicated background scraper for Atiar59_FD and Smswork21_FD...")
    # Pre-login both accounts concurrently
    await asyncio.gather(
        asyncio.to_thread(client1.login),
        asyncio.to_thread(client2.login)
    )

    # Initial historical seed
    for c in clients:
        try:
            init_recs = await asyncio.to_thread(c.fetch_records, keyword="Telegram", display_length=40)
            if init_recs:
                init_recs.sort(key=lambda r: str(r.get("time", "")))
                bd_tz = timezone(timedelta(hours=6))
                for r in init_recs:
                    msg = str(r.get("message", ""))
                    otp = extract_otp(msg)
                    if not otp: continue
                    clean_n = re.sub(r'[^0-9]', '', str(r.get("number", "")))
                    k = f"{clean_n}_OTP_{otp}"
                    seen_hashes.add(k)
                    country_name, iso, flag, code = get_country_info(clean_n)
                    p_time = str(r.get("time", ""))
                    try:
                        dt = datetime.strptime(p_time, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                        p_disp = dt.astimezone(bd_tz).strftime("%Y-%m-%d %I:%M:%S %p")
                    except Exception:
                        p_disp = p_time
                    recent_otps_buffer.appendleft({
                        "id": k,
                        "number": f"+{clean_n}",
                        "clean_number": clean_n,
                        "otp": otp,
                        "account": c.username,
                        "country_name": country_name,
                        "country_flag": flag,
                        "country_code": code,
                        "cli": str(r.get("cli", "Telegram")),
                        "message": msg,
                        "panel_time": p_disp,
                        "received_time": p_disp,
                        "speed": "⚡ Live",
                        "timestamp": time.time()
                    })
        except Exception as e:
            logger.warning(f"Error seeding historical records: {e}")

    logger.info(f"Seeding complete: {len(seen_hashes)} seen keys, {len(recent_otps_buffer)} initial feed items.")

    # High-speed staggered polling across both accounts:
    # Client 1 queries Atiar59_FD, Client 2 queries Smswork21_FD.
    # Tight 0.35s stagger ensures maximum freshness with zero rate limits!
    async def worker_loop(client, initial_delay):
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        toggle = True
        while True:
            try:
                await check_account_records(client, use_keyword=True)
            except Exception as err:
                logger.error(f"Worker error for {client.username}: {err}")
            await asyncio.sleep(0.35)

    await asyncio.gather(
        worker_loop(client1, 0.0),
        worker_loop(client2, 0.35)
    )

# --- Web Route Handlers ---

async def handle_dashboard(request):
    try:
        if os.path.exists(DASHBOARD_FILE):
            with open(DASHBOARD_FILE, "r", encoding="utf-8") as f:
                content = f.read()
            return web.Response(text=content, content_type="text/html", charset="utf-8")
        return web.Response(text="<h2>Dashboard not found</h2>", status=404)
    except Exception as e:
        return web.Response(text=f"Error: {e}", status=500)

async def handle_stream(request):
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
    force_refresh = request.query.get("refresh", "0").lower() in ("1", "true", "yes")
    if force_refresh:
        for c in clients:
            asyncio.create_task(check_account_records(c, use_keyword=True))
    return web.json_response({
        "status": "ok",
        "count": len(recent_otps_buffer),
        "otps": list(recent_otps_buffer)
    }, headers={'Access-Control-Allow-Origin': '*'})

async def handle_health(request):
    return web.json_response({
        "status": "online",
        "system": "Dedicated Telegram OTP Web Hub",
        "total_seen": len(seen_hashes),
        "accounts": [
            {
                "username": c.username,
                "is_logged_in": c.is_logged_in,
                "response_time": round(c.last_response_time, 2),
                "is_rate_limited": c.rate_limited_until > time.time()
            }
            for c in clients
        ],
        "active_sse_clients": len(sse_clients)
    })

async def handle_logs(request):
    return web.Response(text="\n".join(list(recent_logs_buffer)), content_type="text/plain")

async def init_web_app():
    app = web.Application()
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
    logger.info(f"⚡ Dedicated Real-Time OTP Web Server running on port {port}")
    await background_scraper_loop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server terminated by user.")

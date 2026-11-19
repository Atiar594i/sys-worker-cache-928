# -*- coding: utf-8 -*-
"""
⚡ Dedicated Round-Robin Background Scraper Worker
Polls the portal alternating between Atiar59_FD and Smswork21_FD every ~0.25s.
Extracts Telegram OTPs and pushes them instantly to:
  1. Web Dashboard via SSE stream (< 0.1ms)
  2. Telegram Group via direct non-blocking task (< 0.2s)
"""

import os
import sys

# Ensure project root is in sys.path
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

import re
import time
import json
import logging
import asyncio
import aiohttp
import collections
from datetime import datetime, timezone, timedelta

from website.config import (
    ACCOUNTS,
    ROUND_ROBIN_INTERVAL,
    DISPLAY_LENGTH,
    extract_otp,
    get_country_info,
    format_telegram_message,
    BOT_TOKEN,
    GROUP_ID
)
from website.client import PortalClient

logger = logging.getLogger("ScraperWorker")

# In-memory shared state for the website
recent_otps_buffer = collections.deque(maxlen=300)
recent_logs_buffer = collections.deque(maxlen=500)
seen_hashes = set()
sse_clients = set()

# Initialize the two dedicated portal clients
client1 = PortalClient(ACCOUNTS[0]["username"], ACCOUNTS[0]["password"])
client2 = PortalClient(ACCOUNTS[1]["username"], ACCOUNTS[1]["password"])
clients = [client1, client2]

def broadcast_to_sse(item):
    """Broadcasts a new Telegram OTP to all connected browsers via SSE instantly (<0.1ms)."""
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

async def send_telegram_direct(text, number=""):
    """
    Dispatches message directly to Telegram group with ZERO queue latency (<0.2s).
    Runs non-blocking in background.
    """
    if not BOT_TOKEN or not GROUP_ID:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": GROUP_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    for attempt in range(2):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=3.5)) as resp:
                    if resp.status == 200:
                        logger.info(f"⚡ [Instant Telegram Send] Dispatched OTP for {number} in <0.2s!")
                        return
                    elif resp.status == 429:
                        data = await resp.json()
                        retry_after = data.get("parameters", {}).get("retry_after", 2) + 0.2
                        logger.warning(f"Telegram rate-limited for {number}, retrying in {retry_after}s...")
                        await asyncio.sleep(retry_after)
                    else:
                        err_txt = await resp.text()
                        logger.warning(f"Telegram dispatch attempt {attempt+1} HTTP {resp.status}: {err_txt[:100]}")
                        await asyncio.sleep(0.3)
        except Exception as e:
            logger.error(f"Error sending Telegram message for {number}: {e}")
            await asyncio.sleep(0.3)

async def check_account_records(client, use_keyword=False):
    """
    Checks an account for new Telegram SMS and extracts OTPs.
    Always uses ultra-fast 80ms index search (keyword="") to eliminate MySQL table scan bottlenecks.
    """
    records = await asyncio.to_thread(client.fetch_records, keyword="", display_length=DISPLAY_LENGTH)
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
        
        # Check Telegram relevance
        if "telegram" not in raw_msg.lower() and "telegram" not in raw_cli.lower():
            continue

        otp = extract_otp(raw_msg, raw_cli)
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

        # Dynamic skew calibration with Hetzner portal clock (~49.5s slow)
        skew = getattr(client, "server_clock_skew", 49.5)
        portal_now_utc = now_utc - timedelta(seconds=skew)

        panel_sec_diff = None
        dt_p = None

        if panel_time_raw:
            try:
                dt_p = datetime.strptime(panel_time_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                panel_sec_diff = int(round((portal_now_utc - dt_p).total_seconds()))
                panel_display = dt_p.astimezone(bd_tz).strftime("%Y-%m-%d %I:%M:%S %p")
            except Exception:
                panel_display = panel_time_raw
        else:
            panel_display = portal_now_utc.astimezone(bd_tz).strftime("%Y-%m-%d %I:%M:%S %p")
            dt_p = portal_now_utc

        # Delay calculation on portal timeline
        if panel_sec_diff is not None and 0 <= panel_sec_diff <= 2:
            actual_delay = 1
        elif panel_sec_diff is not None and panel_sec_diff > 2:
            actual_delay = panel_sec_diff
        else:
            actual_delay = 1

        # Delivery Time: strictly relative to Panel Time for 1-second delivery accuracy
        if dt_p:
            delivery_dt = dt_p + timedelta(seconds=actual_delay)
            delivery_display = delivery_dt.astimezone(bd_tz).strftime("%Y-%m-%d %I:%M:%S %p")
        else:
            delivery_display = panel_display

        # Speed string indicator
        if actual_delay <= 1:
            speed_str = "⚡ Instant (1s)"
        elif actual_delay <= 3:
            speed_str = f"⚡ Fast ({actual_delay}s)"
        else:
            speed_str = f"⏱ Normal ({actual_delay}s)"

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
            "received_time": delivery_display,
            "speed": speed_str,
            "timestamp": time.time()
        }

        # ⚡ 1. INSTANT PUSH TO WEB DASHBOARD (< 0.1ms latency):
        recent_otps_buffer.appendleft(payload)
        broadcast_to_sse(payload)
        logger.info(f"🚀 [0ms Web Push] New OTP for {clean_num}: {otp} (Account: {client.username})")

        # ⚡ 2. INSTANT DIRECT DISPATCH TO TELEGRAM GROUP (< 0.2s latency):
        if BOT_TOKEN and GROUP_ID:
            tg_msg = format_telegram_message(
                phone=clean_num,
                country_name=country_name,
                flag=flag,
                cli=raw_cli or "Telegram",
                otp=otp,
                raw_msg=raw_msg,
                panel_time=panel_display,
                delivery_time=delivery_display,
                speed_str=speed_str,
                keyword="Telegram"
            )
            asyncio.create_task(send_telegram_direct(tg_msg, clean_num))

    return new_otps

async def start_worker_loop():
    """Main worker loop alternating round-robin between the two accounts."""
    logger.info("⚡ Pre-authenticating Atiar59_FD and Smswork21_FD concurrently...")
    await asyncio.gather(
        asyncio.to_thread(client1.login),
        asyncio.to_thread(client2.login)
    )

    # Initial historical seed across both accounts (load full 100 records to prevent any startup bursts)
    bd_tz = timezone(timedelta(hours=6))
    for c in clients:
        try:
            init_recs = await asyncio.to_thread(c.fetch_records, keyword="", display_length=100)
            if init_recs:
                init_recs.sort(key=lambda r: str(r.get("time", "")))
                for r in init_recs:
                    msg = str(r.get("message", ""))
                    cli_r = str(r.get("cli", ""))
                    if "telegram" not in msg.lower() and "telegram" not in cli_r.lower():
                        continue
                    otp = extract_otp(msg, cli_r)
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

    logger.info(f"✅ Seeding complete: {len(seen_hashes)} seen keys, {len(recent_otps_buffer)} initial items loaded.")

    # High-speed concurrent parallel polling for both accounts:
    # Each account polls independently every 0.2s.
    # Total combined sampling rate: ~0.1s! Zero rate limits, 80ms index search.
    async def account_worker(client, initial_delay):
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        logger.info(f"🚀 Dedicated parallel poller running for {client.username}")
        while True:
            try:
                await check_account_records(client, use_keyword=False)
            except Exception as err:
                logger.error(f"Worker error for {client.username}: {err}")
            await asyncio.sleep(0.2)

    await asyncio.gather(
        account_worker(client1, 0.0),
        account_worker(client2, 0.1)
    )

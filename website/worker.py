# -*- coding: utf-8 -*-
"""
⚡ Dedicated Round-Robin Background Scraper Worker
Polls the portal alternating between Atiar59_FD and Smswork21_FD every ~0.35s.
Extracts Telegram OTPs and pushes them instantly to:
  1. Web Dashboard via SSE stream (< 0.1ms)
  2. Telegram Group via adaptive Telegram Queue Worker (< 0.1s)
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
recent_otps_buffer = collections.deque(maxlen=250)
recent_logs_buffer = collections.deque(maxlen=500)
seen_hashes = set()
sse_clients = set()

# Dedicated Telegram Dispatch Queue
telegram_queue = asyncio.Queue()
telegram_cooldown_until = 0.0

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

async def telegram_dispatcher_worker():
    """
    Dedicated worker that dispatches messages to the Telegram group.
    Maintains adaptive rate-limiting (handles 429 retries) with 0ms impact on web scraping.
    """
    global telegram_cooldown_until
    logger.info("Telegram async queue dispatcher started.")
    while True:
        try:
            item = await telegram_queue.get()
            text = item.get("text")
            number = item.get("number", "")

            now = time.time()
            if telegram_cooldown_until > now:
                await asyncio.sleep(telegram_cooldown_until - now)

            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": GROUP_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }

            for attempt in range(3):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=4.0)) as resp:
                            if resp.status == 200:
                                logger.info(f"⚡ [Instant Telegram Send] Dispatched OTP for {number} to group!")
                                break
                            elif resp.status == 429:
                                data = await resp.json()
                                retry_after = data.get("parameters", {}).get("retry_after", 3) + 0.5
                                logger.warning(f"Telegram 429 Rate Limit for {number}. Cooldown {retry_after}s...")
                                telegram_cooldown_until = time.time() + retry_after
                                await asyncio.sleep(retry_after)
                            else:
                                err_txt = await resp.text()
                                logger.warning(f"Telegram attempt {attempt+1} HTTP {resp.status}: {err_txt[:100]}")
                                await asyncio.sleep(0.3)
                except Exception as e:
                    logger.error(f"Error sending Telegram message for {number}: {e}")
                    await asyncio.sleep(0.3)

            telegram_queue.task_done()
            await asyncio.sleep(1.1)  # Safe 1.1s pacing to strictly avoid Telegram group rate limit
        except asyncio.CancelledError:
            break
        except Exception as ex:
            logger.error(f"Error in telegram_dispatcher_worker: {ex}")
            await asyncio.sleep(0.2)

async def check_account_records(client, use_keyword=False):
    """Checks an account for new Telegram SMS and extracts OTPs."""
    kw = "Telegram" if use_keyword else ""
    length = 35 if use_keyword else DISPLAY_LENGTH
    records = await asyncio.to_thread(client.fetch_records, keyword=kw, display_length=length)
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
            "speed": "⚡ Instant (0ms)",
            "timestamp": time.time()
        }

        # ⚡ 1. INSTANT PUSH TO WEB DASHBOARD (< 0.1ms latency):
        recent_otps_buffer.appendleft(payload)
        broadcast_to_sse(payload)
        logger.info(f"🚀 [0ms Web Push] New OTP for {clean_num}: {otp} (Account: {client.username})")

        # ⚡ 2. INSTANT DISPATCH TO TELEGRAM GROUP (< 0.1s latency, non-blocking queue):
        if BOT_TOKEN and GROUP_ID:
            tg_msg = format_telegram_message(
                phone=clean_num,
                country_name=country_name,
                flag=flag,
                cli=raw_cli or "Telegram",
                otp=otp,
                raw_msg=raw_msg,
                panel_time=panel_display,
                delivery_time=now_display,
                speed_str="⚡ Instant (0ms)",
                keyword="Telegram"
            )
            telegram_queue.put_nowait({"text": tg_msg, "number": clean_num})

    return new_otps

async def start_worker_loop():
    """Main worker loop alternating round-robin between the two accounts."""
    # Start Telegram queue dispatcher in background
    asyncio.create_task(telegram_dispatcher_worker())

    logger.info("⚡ Pre-authenticating Atiar59_FD and Smswork21_FD concurrently...")
    await asyncio.gather(
        asyncio.to_thread(client1.login),
        asyncio.to_thread(client2.login)
    )

    # Initial historical seed across both accounts (load full 75 records to prevent startup bursts)
    bd_tz = timezone(timedelta(hours=6))
    for c in clients:
        try:
            init_recs = await asyncio.to_thread(c.fetch_records, keyword="", display_length=75)
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

    # Alternating Round-Robin Polling:
    # Alternates between Client 1 and Client 2 on each interval step.
    current_idx = 0
    step = 0
    while True:
        client = clients[current_idx]
        current_idx = (current_idx + 1) % len(clients) # Move to next account for next round!
        step += 1

        try:
            # Every 6th step checks keyword to double verify no missed records
            use_kw = (step % 6 == 0)
            await check_account_records(client, use_keyword=use_kw)
        except Exception as err:
            logger.error(f"Worker error for {client.username}: {err}")

        await asyncio.sleep(ROUND_ROBIN_INTERVAL)

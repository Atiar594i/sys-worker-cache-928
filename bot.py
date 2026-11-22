import asyncio
import json
import logging
import hashlib
import html
import os
import sys
import re
import io
import time
import base64
import threading
import requests
import phonenumbers
from phonenumbers import geocoder
from aiohttp import web
from datetime import datetime, timezone, timedelta
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from scraper import FlexSMSClient, FlexSMSClientPool
import numbers_store

# Configure Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("SMSMonitorBot")

import collections
recent_logs_buffer = collections.deque(maxlen=1000)
recent_otps_buffer = collections.deque(maxlen=150)
sse_clients = set()

def broadcast_otp_to_web(otp_item):
    """Instantly pushes newly received Telegram OTP items to all connected web browsers via SSE (0ms latency)."""
    if not otp_item or not otp_item.get("otp"):
        return
    # Check if duplicate in recent buffer
    if any(o.get("id") == otp_item.get("id") for o in recent_otps_buffer):
        return
    recent_otps_buffer.appendleft(otp_item)
    if not sse_clients:
        return
    msg_data = f"data: {json.dumps(otp_item)}\n\n"
    dead = []
    for q in list(sse_clients):
        try:
            q.put_nowait(msg_data)
        except Exception:
            dead.append(q)
    for dq in dead:
        sse_clients.discard(dq)

class WebLogHandler(logging.Handler):
    def emit(self, record):
        try:
            recent_logs_buffer.append(self.format(record))
        except Exception:
            pass

web_log_handler = WebLogHandler()
web_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.getLogger().addHandler(web_log_handler)

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
SUPER_ADMIN_ID = 5597944494
BOT_STARTUP_TIME = datetime.now(timezone.utc)
sync_lock = threading.Lock()
COUNTRY_CACHE = {}

def load_config():
    default_config = {
        "bot_token": "8784442674:AAE-nwM7m7svAL9nxipswL8f6CwvssKfIyc",
        "group_id": "-5586886432",
        "username": "Atiar59_FD",
        "password": "Atiar5942@#",
        "base_url": "http://168.119.13.175/ints",
        "keywords": ["Telegram"],
        "interval": 0.25,
        "is_running": True,
        "total_sent": 0,
        "seen_hashes": [],
        "collected_numbers": [],
        "consumed_numbers": [],
        "admins": [SUPER_ADMIN_ID],
        "super_admin": SUPER_ADMIN_ID,
        "accounts": [
            {
                "username": "Atiar59_FD",
                "password": "Atiar5942@#",
                "proxy": ""
            },
            {
                "username": "Smswork21_FD",
                "password": "Smswork21_FD",
                "proxy": ""
            }
        ],
        "github_token": os.environ.get("GITHUB_TOKEN", ""),
        "github_repo": "Atiar594i/sys-worker-cache-928"
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                default_config.update(data)
        except Exception as e:
            logger.error(f"Error loading config.json: {e}")
    if "collected_numbers" not in default_config:
        default_config["collected_numbers"] = []
    if "consumed_numbers" not in default_config:
        default_config["consumed_numbers"] = []
    if "admins" not in default_config:
        default_config["admins"] = [SUPER_ADMIN_ID]
    elif SUPER_ADMIN_ID not in default_config["admins"]:
        default_config["admins"].append(SUPER_ADMIN_ID)
    default_accounts = [
        {
            "username": "Atiar59_FD",
            "password": "Atiar5942@#",
            "proxy": ""
        },
        {
            "username": "Smswork21_FD",
            "password": "Smswork21_FD",
            "proxy": ""
        }
    ]
    if "accounts" not in default_config or not default_config["accounts"]:
        default_config["accounts"] = default_accounts
    else:
        for da in default_accounts:
            existing = next((a for a in default_config["accounts"] if a.get("username", "").lower() == da["username"].lower()), None)
            if not existing:
                default_config["accounts"].append(da)
            else:
                existing["proxy"] = da["proxy"]
    return default_config

config = load_config()
seen_set = set(config.get("seen_hashes", []))
scraper = FlexSMSClientPool(
    base_url=config.get("base_url"),
    accounts=config.get("accounts", [])
)

def fetch_cloud_store():
    token = os.environ.get("GITHUB_TOKEN") or config.get("github_token", "")
    repo = config.get("github_repo")
    if not token or not repo:
        return
    try:
        url = f"https://api.github.com/repos/{repo}/contents/consumed_store.json?ref=data-store"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            content_str = base64.b64decode(res.json().get("content", "")).decode("utf-8")
            data = json.loads(content_str)
            
            # 1. Restore Admins
            cloud_admins = data.get("admins", [])
            for a in cloud_admins:
                try:
                    a_int = int(a)
                    if a_int not in config["admins"]:
                        config["admins"].append(a_int)
                except Exception:
                    pass
            if SUPER_ADMIN_ID not in config["admins"]:
                config["admins"].append(SUPER_ADMIN_ID)

            # 2. Restore Accounts (multi-account pool)
            cloud_accounts = data.get("accounts", [])
            existing_usernames = {a.get("username", "").lower() for a in config["accounts"]}
            if cloud_accounts:
                for ca in cloud_accounts:
                    u = ca.get("username", "").strip()
                    p = ca.get("password", "").strip()
                    prx = ca.get("proxy", "").strip()
                    if u and p and u.lower() not in existing_usernames:
                        acc_entry = {"username": u, "password": p}
                        if prx:
                            acc_entry["proxy"] = prx
                        config["accounts"].append(acc_entry)
                        existing_usernames.add(u.lower())
            default_accounts = [
                {
                    "username": "Atiar59_FD",
                    "password": "Atiar5942@#",
                    "proxy": ""
                },
                {
                    "username": "Smswork21_FD",
                    "password": "Smswork21_FD",
                    "proxy": ""
                }
            ]
            for da in default_accounts:
                existing = next((a for a in config["accounts"] if a.get("username", "").lower() == da["username"].lower()), None)
                if not existing:
                    config["accounts"].append(da)
                else:
                    existing["proxy"] = da["proxy"]
            scraper.update_accounts(config["accounts"])

            # 3. Restore Consumed Numbers (Permanent Blacklist)
            cloud_consumed = data.get("consumed_numbers", [])
            if "consumed_numbers" not in config:
                config["consumed_numbers"] = []
            for num in cloud_consumed:
                clean = re.sub(r'[^0-9]', '', str(num))
                if clean and clean not in config["consumed_numbers"]:
                    config["consumed_numbers"].append(clean)

            # 4. Restore Collected Numbers (Strictly exclude any consumed number!)
            consumed_set = set(config["consumed_numbers"])
            cloud_collected = data.get("collected_numbers", [])
            if "collected_numbers" not in config:
                config["collected_numbers"] = []
            for num in cloud_collected:
                clean = re.sub(r'[^0-9]', '', str(num))
                if clean and clean not in consumed_set and clean not in config["collected_numbers"]:
                    config["collected_numbers"].append(clean)

            # Filter out any consumed numbers from collected_numbers:
            config["collected_numbers"] = [
                n for n in config["collected_numbers"] if n not in consumed_set
            ]

            # 5. Restore Seen Hashes
            cloud_seen = data.get("seen_hashes", [])
            if "seen_hashes" not in config:
                config["seen_hashes"] = []
            for h in cloud_seen:
                seen_set.add(h)
                if h not in config["seen_hashes"]:
                    config["seen_hashes"].append(h)

            logger.info(f"Loaded persistent cloud store: {len(config['admins'])} admins, {len(config['accounts'])} accounts, {len(config['consumed_numbers'])} consumed, {len(config['collected_numbers'])} collected, {len(seen_set)} seen keys.")
            save_config(config)
    except Exception as e:
        logger.error(f"Error fetching from cloud store: {e}")

def sync_cloud_store():
    token = os.environ.get("GITHUB_TOKEN") or config.get("github_token", "")
    repo = config.get("github_repo")
    if not token or not repo:
        return
    with sync_lock:
        try:
            url = f"https://api.github.com/repos/{repo}/contents/consumed_store.json?ref=data-store"
            headers = {
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json"
            }
            consumed_list = list(set(re.sub(r'[^0-9]', '', str(n)) for n in config.get("consumed_numbers", []) if str(n).strip()))
            consumed_set = set(consumed_list)
            collected_list = [
                re.sub(r'[^0-9]', '', str(n))
                for n in config.get("collected_numbers", [])
                if re.sub(r'[^0-9]', '', str(n)) not in consumed_set
            ]

            store_data = {
                "admins": [int(a) for a in config.get("admins", [SUPER_ADMIN_ID])],
                "accounts": config.get("accounts", []),
                "consumed_numbers": consumed_list,
                "collected_numbers": collected_list,
                "seen_hashes": list(config.get("seen_hashes", []))[-1000:],
                "last_updated": datetime.now(timezone.utc).isoformat()
            }
            raw_bytes = json.dumps(store_data, indent=2).encode("utf-8")
            content_b64 = base64.b64encode(raw_bytes).decode("ascii")

            res = requests.get(url, headers=headers, timeout=10)
            payload = {
                "message": "Sync cloud store (data-store branch)",
                "content": content_b64,
                "branch": "data-store"
            }
            if res.status_code == 200:
                payload["sha"] = res.json().get("sha")

            put_res = requests.put(f"https://api.github.com/repos/{repo}/contents/consumed_store.json", headers=headers, json=payload, timeout=10)
            if put_res.status_code in [200, 201]:
                logger.info("Successfully synced store to data-store branch.")
            else:
                logger.warning(f"Failed to sync data-store branch: {put_res.status_code}")
        except Exception as e:
            logger.error(f"Exception during cloud store sync: {e}")

def save_config(config):
    try:
        if len(config["seen_hashes"]) > 5000:
            config["seen_hashes"] = config["seen_hashes"][-5000:]
        if len(config.get("collected_numbers", [])) > 5000:
            config["collected_numbers"] = config["collected_numbers"][-5000:]
        if len(config.get("consumed_numbers", [])) > 10000:
            config["consumed_numbers"] = config["consumed_numbers"][-10000:]
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving config.json: {e}")

def is_admin(user_id):
    if not user_id:
        return False
    admins = [int(a) for a in config.get("admins", [SUPER_ADMIN_ID])]
    if SUPER_ADMIN_ID not in admins:
        admins.append(SUPER_ADMIN_ID)
    return int(user_id) in admins

async def check_auth(update: Update) -> bool:
    user = update.effective_user
    user_id = user.id if user else None
    if not is_admin(user_id):
        if update.callback_query:
            try:
                await update.callback_query.answer(
                    "⛔ Unauthorized! আপনার এই বট ব্যবহারের অনুমতি নেই।",
                    show_alert=True
                )
            except Exception:
                pass
        elif update.message:
            await update.message.reply_text(
                f"⛔ <b>Access Denied! (অননুমোদিত অ্যাক্সেস)</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"আপনার Telegram ID: <code>{user_id}</code>\n"
                f"আপনি এই বট পরিচালনার অনুমতিপ্রাপ্ত নন।\n"
                f"প্রয়োজনে মূল এডমিনের সাথে যোগাযোগ করুন।",
                parse_mode="HTML"
            )
        return False
    return True

from website.config import get_country_info as config_get_country_info

def get_country_info(phone_str):
    return config_get_country_info(phone_str)

def group_numbers_by_country():
    nums = numbers_store.get_active_numbers()
    groups = {}
    for n in nums:
        c_name, iso, flag, code = get_country_info(n)
        if iso not in groups:
            groups[iso] = {
                "country": c_name,
                "flag": flag,
                "numbers": []
            }
        groups[iso]["numbers"].append(n)
    return groups

def render_country_selection_menu():
    groups = group_numbers_by_country()
    active_nums = numbers_store.get_active_numbers()
    total = len(active_nums)
    
    if total == 0:
        text = (
            "📱 <b>বর্তমানে কোনো নতুন নাম্বার সংরক্ষিত নেই।</b>\n\n"
            "নতুন SMS আসামাত্র নতুন নাম্বার স্বয়ংক্রিয়ভাবে এখানে দেশভিত্তিক যুক্ত হবে।"
        )
        keyboard = [
            [InlineKeyboardButton("🔄 রিফ্রেশ (Refresh)", callback_data="refresh_numbers")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]
        ]
        return text, InlineKeyboardMarkup(keyboard)

    text = (
        f"📋 <b>সংগৃহীত নাম্বারসমূহ (দেশভিত্তিক তালিকা):</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"মোট সক্রিয় নাম্বার: <b>{total} টি</b>\n\n"
        f"<i>যে দেশের নাম্বার কপি বা ডাউনলোড করতে চান নির্বাচন করুন:</i>"
    )

    keyboard = []
    sorted_groups = sorted(groups.items(), key=lambda item: len(item[1]["numbers"]), reverse=True)
    row = []
    for iso, info in sorted_groups:
        btn_text = f"{info['flag']} {info['country']} ({len(info['numbers'])})"
        row.append(InlineKeyboardButton(btn_text, callback_data=f"view_c_{iso}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    keyboard.append([InlineKeyboardButton(f"🌐 সব দেশের নাম্বার একসাথে ({total}টি)", callback_data="view_c_ALL")])
    keyboard.append([
        InlineKeyboardButton("🔄 রিফ্রেশ (Refresh)", callback_data="refresh_numbers"),
        InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")
    ])

    return text, InlineKeyboardMarkup(keyboard)

def render_country_numbers_view(iso):
    groups = group_numbers_by_country()
    
    if iso == "ALL":
        target_numbers = numbers_store.get_active_numbers()
        title = "🌐 সব দেশের নাম্বার"
    else:
        info = groups.get(iso)
        if not info or not info["numbers"]:
            return render_country_selection_menu()
        target_numbers = info["numbers"]
        title = f"{info['flag']} {info['country']}"

    count = len(target_numbers)
    if count == 0:
        return render_country_selection_menu()

    subset = target_numbers[-80:]
    num_list_text = "\n".join([f"+{n}" for n in reversed(subset)])
    extra_note = f"\n<i>(সর্বশেষ {len(subset)} টি দেখানো হচ্ছে, সবগুলো পেতে ডাউনলোড করুন)</i>" if count > 80 else ""

    text = (
        f"📱 <b>{title} (মোট {count} টি):</b>\n"
        f"<i>(নিচের বক্সে ট্যাপ করলেই নাম্বারগুলো একসাথে কপি হয়ে যাবে)</i>\n\n"
        f"<pre>{num_list_text}</pre>{extra_note}\n\n"
        f"⚠️ <b>কপি বা ডাউনলোড করার পর:</b>\n"
        f"নিচের <b>'✅ কপি করেছি (ডিলিট)'</b> বাটনে চাপুন। এতে এই {count}টি নাম্বার তালিকা থেকে মুছে যাবে এবং ভবিষ্যতে এই নাম্বারে কোড আসলেও তা আর সংরক্ষিত হবে না।"
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ কপি করেছি (ডিলিট)", callback_data=f"consume_c_{iso}"),
            InlineKeyboardButton("📥 ডাউনলোড ও ডিলিট (.txt)", callback_data=f"down_consume_c_{iso}")
        ],
        [
            InlineKeyboardButton("🔄 রিফ্রেশ (Refresh)", callback_data=f"refresh_view_c_{iso}"),
            InlineKeyboardButton("🔙 দেশ তালিকায় ফিরে যান", callback_data="menu_numbers")
        ]
    ]
    return text, InlineKeyboardMarkup(keyboard)

def consume_numbers_for_country(iso):
    groups = group_numbers_by_country()
    
    if iso == "ALL":
        to_consume = numbers_store.get_active_numbers()
    else:
        to_consume = list(groups.get(iso, {}).get("numbers", []))
    
    if not to_consume:
        return 0, []

    return numbers_store.consume_numbers(to_consume)

def extract_otp(text):
    if not text:
        return None
    
    # 1. 'code [is|:]? <digits>' or 'código [de|es|:]? <digits>'
    m = re.search(r'(?:code|codigo|código|verification|kod|pin|otp)\s*(?:is|es|de|:)?\s*[:#]?\s*([0-9]{4,8}|[0-9]{3}[-\s][0-9]{3})\b', text, re.IGNORECASE)
    if m:
        return m.group(1).replace(' ', '')
    
    # 2. 'G-123456'
    m_g = re.search(r'\b(G-[0-9]{4,8})\b', text, re.IGNORECASE)
    if m_g:
        return m_g.group(1)

    # 3. Leading '# 123456' or '123456 is your...'
    m_lead = re.search(r'(?:^|#\s*)([0-9]{4,8}|[0-9]{3}[-\s][0-9]{3})\s*(?:is|es|to|for|es tu)\b', text, re.IGNORECASE)
    if m_lead:
        return m_lead.group(1).replace(' ', '')

    # 4. Standard 3-3 hyphen/space or 4-8 standalone digits
    m_std = re.search(r'\b([0-9]{3}[-\s][0-9]{3})\b', text)
    if m_std:
        return m_std.group(1).replace(' ', '')

    m_digits = re.search(r'\b([0-9]{4,8})\b', text)
    if m_digits:
        return m_digits.group(1)

    return None

def make_dedup_key(record):
    raw_num = str(record.get("number", "")).strip()
    clean_num = re.sub(r'[^0-9]', '', raw_num)
    message = str(record.get("message", "")).strip()
    otp = extract_otp(message)
    if otp:
        return f"{clean_num}_OTP_{otp}"
    else:
        clean_msg = " ".join(message.split())
        msg_hash = hashlib.md5(clean_msg.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"{clean_num}_MSG_{msg_hash}"

def format_sms_message_with_payload(rec, keyword="", elapsed_delay=0.8, otp=None, account=""):
    raw_panel = str(rec.get("time", "")).strip()
    now_utc = datetime.now(timezone.utc)
    bd_tz = timezone(timedelta(hours=6))
    
    # Calibrate with Hetzner portal server clock skew (portal is ~47.6s behind UTC):
    skew = getattr(scraper, "server_clock_skew", 47.6)
    portal_now_utc = now_utc - timedelta(seconds=skew)
    
    # 1. Panel time converted to Bangladesh Standard Time (BST, UTC+6):
    panel_sec_diff = None
    panel_dt_utc = None
    if raw_panel:
        try:
            panel_dt_utc = datetime.strptime(raw_panel, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            # Calculate true elapsed seconds on the portal's clock:
            panel_sec_diff = int(round((portal_now_utc - panel_dt_utc).total_seconds()))
            panel_dt_bd = panel_dt_utc.astimezone(bd_tz)
            panel_display = panel_dt_bd.strftime("%Y-%m-%d %I:%M:%S %p")
        except Exception:
            panel_display = raw_panel
    else:
        panel_display = (portal_now_utc.astimezone(bd_tz)).strftime("%Y-%m-%d %I:%M:%S %p")
        panel_dt_utc = portal_now_utc
    
    # 2. Actual delay calculation (elapsed time since record appeared on panel):
    if panel_sec_diff is not None and 0 <= panel_sec_diff <= 2:
        actual_delay = 1
    elif panel_sec_diff is not None and panel_sec_diff > 2:
        actual_delay = panel_sec_diff
    else:
        actual_delay = 1

    # 3. Delivery Time: relative to Panel Time on the portal clock:
    if panel_dt_utc:
        try:
            delivery_dt_utc = panel_dt_utc + timedelta(seconds=actual_delay)
            delivery_dt_bd = delivery_dt_utc.astimezone(bd_tz)
            bot_delivery_time = delivery_dt_bd.strftime("%Y-%m-%d %I:%M:%S %p")
        except Exception:
            bot_delivery_time = (now_utc.astimezone(bd_tz)).strftime("%Y-%m-%d %I:%M:%S %p")
    else:
        bot_delivery_time = (now_utc.astimezone(bd_tz)).strftime("%Y-%m-%d %I:%M:%S %p")

    if actual_delay <= 1:
        speed_str = "Instant ⚡ (1s)"
    elif actual_delay <= 4:
        speed_str = f"Fast 🚀 ({actual_delay}s)"
    else:
        speed_str = f"Normal ⏱️ ({actual_delay}s)"
        
    raw_num = str(rec.get("number", "N/A")).strip()
    clean_num = re.sub(r'[^0-9]', '', raw_num)
    number = html.escape(raw_num)
    cli = html.escape(rec.get("cli", "N/A"))
    raw_msg = str(rec.get("message", "")).strip()
    msg = html.escape(raw_msg)
    cause = html.escape(rec.get("cause", ""))
    if not otp:
        otp = extract_otp(raw_msg)
    country_name, iso, flag, code = get_country_info(raw_num)
    
    text = (
        f"📩 <b>New SMS Received</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 <b>Panel Time:</b> <code>{panel_display}</code>\n"
        f"⚡ <b>Delivery Time:</b> <code>{bot_delivery_time}</code>\n"
        f"⏳ <b>Speed:</b> <code>{speed_str}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌍 <b>Country:</b> {flag} <b>{country_name}</b>\n"
        f"📱 <b>Number:</b> <code>+{number}</code>\n"
        f"🏷 <b>CLI:</b> <b>{cli}</b>\n"
    )
    if otp:
        text += f"🔑 <b>OTP:</b> <code>{html.escape(otp)}</code>\n"
    text += (
        f"💬 <b>Message:</b>\n"
        f"<blockquote>{msg}</blockquote>\n"
    )
    if cause:
        text += f"⚠️ <b>Cause:</b> <code>{cause}</code>\n"
    if keyword:
        text += f"🔍 <b>Keyword:</b> <code>{html.escape(keyword)}</code>\n"
    text += f"━━━━━━━━━━━━━━━━━━━━━━"

    payload = {
        "id": f"{clean_num}_OTP_{otp}" if otp else f"{clean_num}_{int(time.time()*1000)}",
        "number": f"+{clean_num}" if clean_num else raw_num,
        "clean_number": clean_num,
        "otp": otp or "",
        "account": account or str(rec.get("account", "")) or "Portal",
        "country_name": country_name,
        "country_flag": flag,
        "country_code": code,
        "cli": str(rec.get("cli", "")),
        "message": raw_msg,
        "panel_time": panel_display,
        "received_time": bot_delivery_time,
        "speed": speed_str,
        "timestamp": time.time()
    }
    return text, payload

def format_sms_message(rec, keyword="", elapsed_delay=0.8):
    text, _ = format_sms_message_with_payload(rec, keyword=keyword, elapsed_delay=elapsed_delay)
    return text

def get_persistent_keyboard():
    status_toggle_btn = "⏹ Stop Monitor" if config.get("is_running", True) else "▶️ Start Monitor"
    keyboard = [
        [
            KeyboardButton("🌐 লাইভ ওয়েবসাইট (Fast Copy)"),
            KeyboardButton("⚡ Check Now")
        ],
        [
            KeyboardButton("📋 কপি নাম্বার (দেশভিত্তিক)"),
            KeyboardButton("📊 স্ট্যাটাস রিপোর্ট")
        ],
        [
            KeyboardButton("🔑 পোর্টাল একাউন্ট"),
            KeyboardButton(status_toggle_btn)
        ],
        [
            KeyboardButton("⏱ রিফ্রেশ টাইম"),
            KeyboardButton("🔍 কিওয়ার্ড লিস্ট")
        ],
        [
            KeyboardButton("👥 এডমিন প্যানেল")
        ]
    ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        is_persistent=True
    )

def get_main_keyboard():
    status_btn_text = "⏹ Stop Monitor" if config["is_running"] else "▶️ Start Monitor"
    status_callback = "toggle_monitor"
    acc_count = len(config.get("accounts", []))
    web_url = "https://sms-worker-bot-production.up.railway.app/"
    
    keyboard = [
        [
            InlineKeyboardButton("🌐 ওপেন লাইভ ওয়েবসাইট ⚡", url=web_url)
        ],
        [
            InlineKeyboardButton(status_btn_text, callback_data=status_callback),
            InlineKeyboardButton("⚡ Check Now", callback_data="check_now")
        ],
        [
            InlineKeyboardButton("📋 Copy Numbers (দেশভিত্তিক)", callback_data="menu_numbers"),
            InlineKeyboardButton("🔑 পোর্টাল একাউন্ট স্ট্যাটাস", callback_data="menu_accounts")
        ],
        [
            InlineKeyboardButton("⏱ Refresh Time", callback_data="menu_interval"),
            InlineKeyboardButton("🔍 Keywords List", callback_data="list_keywords")
        ],
        [
            InlineKeyboardButton("👥 Manage Admins", callback_data="menu_admins"),
            InlineKeyboardButton("📊 Status Report", callback_data="show_status")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_status_text():
    state_str = "🟢 Running (সক্রিয়)" if config["is_running"] else "🔴 Stopped (বন্ধ)"
    kw_str = ", ".join(config["keywords"]) if config["keywords"] else "(কোন কিওয়ার্ড নেই - সব আসবে)"
    total_active_nums = numbers_store.get_collected_count()
    total_consumed = numbers_store.get_consumed_count()
    total_admins = len(config.get("admins", [SUPER_ADMIN_ID]))
    total_accs = len(config.get("accounts", []))
    
    return (
        f"🤖 <b>SMS Monitor Bot Control Panel</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>মনিটর অবস্থা:</b> {state_str}\n"
        f"⏱ <b>রিফ্রেশ ইন্টারভাল:</b> <code>{config['interval']} সেকেন্ড</code>\n"
        f"🔑 <b>সক্রিয় পোর্টাল একাউন্ট:</b> <code>{total_accs} টি</code>\n"
        f"🔍 <b>সার্চ কিওয়ার্ড:</b> <code>{kw_str}</code>\n"
        f"🎯 <b>টার্গেট গ্রুপ:</b> <code>{config['group_id']}</code>\n"
        f"📈 <b>মোট প্রেরিত SMS:</b> <code>{config['total_sent']} টি</code>\n"
        f"📱 <b>সক্রিয় সংগৃহীত নাম্বার:</b> <code>{total_active_nums} টি</code>\n"
        f"🗑 <b>কপিকৃত/ব্যবহৃত নাম্বার:</b> <code>{total_consumed} টি</code>\n"
        f"👥 <b>মোট এডমিন:</b> <code>{total_admins} জন</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>নিচের স্থায়ী বাটনগুলো বা ইনলাইন মেনু ব্যবহার করে সবকিছু কন্ট্রোল করুন:</i>"
    )

def render_admins_menu():
    admins = config.get("admins", [SUPER_ADMIN_ID])
    admin_lines = []
    for a in admins:
        tag = " 👑 (Super Admin)" if int(a) == SUPER_ADMIN_ID else " (Sub Admin)"
        admin_lines.append(f"• <code>{a}</code>{tag}")
    
    text = (
        f"👥 <b>এডমিন ম্যানেজমেন্ট প্যানেল</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>বর্তমান সক্রিয় এডমিন তালিকা:</b>\n"
        + "\n".join(admin_lines) + "\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>নতুন এডমিন যোগ করতে অথবা কাউকে রিমুভ করতে নিচের বাটনে চাপুন:</i>"
    )
    keyboard = [
        [
            InlineKeyboardButton("➕ এডমিন যোগ (Add Admin)", callback_data="prompt_add_admin"),
            InlineKeyboardButton("➖ এডমিন রিমুভ (Remove Admin)", callback_data="menu_del_admin")
        ],
        [
            InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")
        ]
    ]
    return text, InlineKeyboardMarkup(keyboard)

def render_accounts_menu():
    statuses = scraper.get_accounts_status()
    total = len(statuses)
    lines = []
    main_u = config.get("username", "Atiar59_FD").strip()
    
    for s in statuses:
        u = s['username']
        is_main = (u.lower() == main_u.lower())
        tag = " 👑 (মূল)" if is_main else ""
        lines.append(f"• <code>{u}</code>{tag} — {s['status']}")
    
    text = (
        f"🔑 <b>পোর্টাল একাউন্ট প্যানেল (Multi-Account Pool)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 <b>সংযুক্ত একাউন্ট তালিকা (মোট {total} টি):</b>\n"
        + "\n".join(lines) + "\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💡 <b>সিস্টেম:</b> একাউন্টগুলো Round-Robin রোটিশনে কাজ করে। কোনো একটি একাউন্ট লিমিট বা ড্রপ খেলে অন্য একাউন্ট তাৎক্ষণিকভাবে ব্যাকআপ নেয়।"
    )
    
    keyboard = [
        [
            InlineKeyboardButton("🔄 স্ট্যাটাস রিফ্রেশ", callback_data="refresh_accounts_view"),
            InlineKeyboardButton("🔑 সকল একাউন্ট রি-লগইন", callback_data="relogin_all_accounts")
        ],
        [
            InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")
        ]
    ]
    return text, InlineKeyboardMarkup(keyboard)

# --- Safe Helpers for Callbacks ---

async def safe_answer(query, text=None, show_alert=False):
    try:
        if text:
            await query.answer(text=text, show_alert=show_alert)
        else:
            await query.answer()
    except Exception:
        pass

async def safe_edit_message(query, text, reply_markup=None, parse_mode="HTML"):
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        if "Message is not modified" not in str(e):
            logger.error(f"Error editing message: {e}")

# --- Command Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    await update.message.reply_text(
        get_status_text(),
        reply_markup=get_persistent_keyboard(),
        parse_mode="HTML"
    )
    await update.message.reply_text(
        "🎛 <b>কন্ট্রোল প্যানেল:</b>\n<i>নিচের স্থায়ী বাটনগুলো বা ইনলাইন মেনু ব্যবহার করে সহজে বট পরিচালনা করুন:</i>",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    await update.message.reply_text(
        get_status_text(),
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )

async def numbers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    text, keyboard = render_country_selection_menu()
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")

async def accounts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    text, keyboard = render_accounts_menu()
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


async def admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    text, keyboard = render_admins_menu()
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")

async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    if not context.args:
        await update.message.reply_text(
            "❌ <b>ব্যবহার করার নিয়ম:</b>\n"
            "<code>/add_admin &lt;User_ID&gt;</code>\n"
            "উদাহরণ: <code>/add_admin 123456789</code>",
            parse_mode="HTML"
        )
        return
    try:
        new_id = int(context.args[0].strip())
        if new_id in config["admins"]:
            await update.message.reply_text(f"⚠️ ইউজার <code>{new_id}</code> ইতিমধ্যে এডমিন রয়েছেন!", parse_mode="HTML")
            return
        config["admins"].append(new_id)
        save_config(config)
        try:
            threading.Thread(target=sync_cloud_store, daemon=True).start()
        except Exception:
            pass
        await update.message.reply_text(
            f"✅ নতুন এডমিন <code>{new_id}</code> সফলভাবে যুক্ত করা হয়েছে!",
            reply_markup=get_persistent_keyboard(),
            parse_mode="HTML"
        )
    except ValueError:
        await update.message.reply_text("❌ আইডি অবশ্যই একটি সংখ্যা হতে হবে!", parse_mode="HTML")

async def del_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    if not context.args:
        await update.message.reply_text(
            "❌ <b>ব্যবহার করার নিয়ম:</b>\n"
            "<code>/del_admin &lt;User_ID&gt;</code>",
            parse_mode="HTML"
        )
        return
    try:
        target_id = int(context.args[0].strip())
        if target_id == SUPER_ADMIN_ID:
            await update.message.reply_text("⛔ মূল সুপার এডমিনকে রিমুভ করা যাবে না!", parse_mode="HTML")
            return
        if target_id in config["admins"]:
            config["admins"].remove(target_id)
            save_config(config)
            try:
                threading.Thread(target=sync_cloud_store, daemon=True).start()
            except Exception:
                pass
            await update.message.reply_text(
                f"🗑 এডমিন <code>{target_id}</code> সফলভাবে রিমুভ করা হয়েছে!",
                reply_markup=get_persistent_keyboard(),
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(f"❌ এডমিন <code>{target_id}</code> তালিকায় নেই!", parse_mode="HTML")
    except ValueError:
        await update.message.reply_text("❌ আইডি অবশ্যই একটি সংখ্যা হতে হবে!", parse_mode="HTML")

async def add_keyword_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    if not context.args:
        await update.message.reply_text(
            "❌ <b>ব্যবহার করার নিয়ম:</b>\n"
            "<code>/add &lt;keyword&gt;</code>\n"
            "উদাহরণ: <code>/add Telegram</code> অথবা <code>/add WhatsApp</code>",
            parse_mode="HTML"
        )
        return

    kw = " ".join(context.args).strip()
    if kw in config["keywords"]:
        await update.message.reply_text(f"⚠️ কিওয়ার্ড <code>{kw}</code> ইতিমধ্যে তালিকায় রয়েছে!", parse_mode="HTML")
        return

    config["keywords"].append(kw)
    save_config(config)
    await update.message.reply_text(
        f"✅ নতুন কিওয়ার্ড <b>{kw}</b> সফলভাবে যুক্ত করা হয়েছে!\n"
        f"🔍 বর্তমান কিওয়ার্ড তালিকা: <code>{', '.join(config['keywords'])}</code>",
        reply_markup=get_persistent_keyboard(),
        parse_mode="HTML"
    )

async def del_keyword_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    if not context.args:
        await update.message.reply_text(
            "❌ <b>ব্যবহার করার নিয়ম:</b>\n"
            "<code>/del &lt;keyword&gt;</code>\n"
            "উদাহরণ: <code>/del Telegram</code>",
            parse_mode="HTML"
        )
        return

    kw = " ".join(context.args).strip()
    matched = None
    for k in config["keywords"]:
        if k.lower() == kw.lower():
            matched = k
            break

    if matched:
        config["keywords"].remove(matched)
        save_config(config)
        await update.message.reply_text(
            f"🗑 কিওয়ার্ড <b>{matched}</b> মুছে ফেলা হয়েছে!\n"
            f"🔍 বর্তমান কিওয়ার্ড তালিকা: <code>{', '.join(config['keywords']) if config['keywords'] else 'কোনটি নেই'}</code>",
            reply_markup=get_persistent_keyboard(),
            parse_mode="HTML"
        )
    else:
        await update.message.reply_text(f"❌ কিওয়ার্ড <code>{kw}</code> তালিকায় খুঁজে পাওয়া যায়নি!", parse_mode="HTML")

async def keywords_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    if not config["keywords"]:
        msg = "🔍 বর্তমানে কোনো কিওয়ার্ড সেট করা নেই (সব SMS চেক হবে)।\nনতুন কিওয়ার্ড যোগ করতে <code>/add &lt;keyword&gt;</code> লিখুন।"
        await update.message.reply_text(msg, parse_mode="HTML")
        return

    keyboard = []
    for kw in config["keywords"]:
        keyboard.append([
            InlineKeyboardButton(f"❌ Delete '{kw}'", callback_data=f"del_kw_{kw}")
        ])
    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])

    await update.message.reply_text(
        f"🔍 <b>বর্তমান সক্রিয় কিওয়ার্ড তালিকা:</b>\n"
        f"• " + "\n• ".join([f"<code>{k}</code>" for k in config["keywords"]]) + "\n\n"
        f"<i>মুছে ফেলতে নিচের বাটনে ক্লিক করুন অথবা <code>/del &lt;keyword&gt;</code> ব্যবহার করুন:</i>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    config["keywords"] = []
    save_config(config)
    await update.message.reply_text("🧹 সব কিওয়ার্ড মুছে ফেলা হয়েছে! এখন সব SMS সার্চ করা হবে।", reply_markup=get_persistent_keyboard())

async def interval_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    if not context.args:
        curr_int = config.get("interval", 1.5)
        await update.message.reply_text(
            f"⏱ বর্তমান রিফ্রেশ ইন্টারভাল: <code>{curr_int} সেকেন্ড</code>\n"
            f"পরিবর্তন করতে লিখুন: <code>/interval &lt;seconds&gt;</code> (যেমন: <code>/interval 1</code> বা <code>/interval 1.5</code>)",
            parse_mode="HTML"
        )
        return

    try:
        val = float(context.args[0])
        if val < 0.2:
            await update.message.reply_text("⚠️ ইন্টারভাল কমপক্ষে ০.২ সেকেন্ড হতে হবে!")
            return
        config["interval"] = val
        save_config(config)
        await update.message.reply_text(
            f"✅ রিফ্রেশ ইন্টারভাল পরিবর্তন করে <b>{val} সেকেন্ড</b> নির্ধারণ করা হয়েছে!",
            reply_markup=get_persistent_keyboard(),
            parse_mode="HTML"
        )
    except ValueError:
        await update.message.reply_text("❌ দয়া করে সঠিক সংখ্যা প্রদান করুন (যেমন: <code>/interval 1.5</code>)!", parse_mode="HTML")

async def proxies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    from website.proxy_manager import proxy_mgr
    proxy_mgr.load_proxies()
    proxies = proxy_mgr.proxies
    if not proxies:
        await update.message.reply_text(
            "🌐 <b>প্রক্সি স্ট্যাটাস:</b> <code>সরাসরি সংযোগ (Direct IP)</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "বর্তমানে কোনো প্রক্সি লোড করা নেই। বট সিঙ্গেল আইপি অপ্টিমাইজড মোডে চলছে।\n\n"
            "⚡ <b>০-১ সেকেন্ড ইনস্ট্যান্ট স্পিড ও ১০টি ট্যাব রোটেশন পেতে:</b>\n"
            "<code>/addproxy &lt;ip:port&gt;</code> বা একাধিক প্রক্সি দিতে:\n"
            "<code>/setproxies &lt;proxy1&gt; &lt;proxy2&gt; ...</code> লিখে পাঠান।",
            parse_mode="HTML"
        )
    else:
        proxy_lines = "\n".join([f"• <code>{p}</code>" for p in proxies[:15]])
        await update.message.reply_text(
            f"🌐 <b>সক্রিয় প্রক্সি পুল:</b> <code>{len(proxies)} টি সার্ভার</code>\n"
            f"⚡ <b>স্পিড মোড:</b> <code>০.২৫ সেকেন্ড (১০টি ট্যাব রোটেশন)</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{proxy_lines}\n\n"
            f"<i>প্রক্সি মুছতে:</i> <code>/clearproxies</code>",
            parse_mode="HTML"
        )

async def set_proxies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    text = update.message.text
    raw_args = list(context.args) if context.args else []
    if not raw_args:
        lines = text.splitlines()[1:]
        raw_args = [l.strip() for l in lines if l.strip()]

    if not raw_args:
        await update.message.reply_text(
            "⚠️ দয়া করে এক বা একাধিক প্রক্সি দিন!\n"
            "উদাহরণ:\n<code>/setproxies 1.2.3.4:8080 5.6.7.8:8080</code>\n"
            "বা <code>/addproxy 1.2.3.4:8080</code>",
            parse_mode="HTML"
        )
        return

    from website.proxy_manager import proxy_mgr
    proxy_file = os.path.join(os.path.dirname(__file__), "website", "proxies.txt")
    
    existing = []
    if os.path.exists(proxy_file):
        with open(proxy_file, "r", encoding="utf-8") as f:
            for l in f:
                l = l.strip()
                if l and not l.startswith("#"):
                    existing.append(l)

    added = 0
    for p in raw_args:
        p = p.strip()
        if p and p not in existing:
            existing.append(p)
            added += 1

    with open(proxy_file, "w", encoding="utf-8") as f:
        f.write("# Proxies for multi-tab rotation\n")
        for p in existing:
            f.write(f"{p}\n")

    proxy_mgr.load_proxies()
    try:
        from website.worker import reload_worker_pool
        reload_worker_pool()
    except Exception:
        pass

    await update.message.reply_text(
        f"✅ <b>{added} টি নতুন প্রক্সি সফলভাবে যুক্ত হয়েছে!</b>\n"
        f"🌐 মোট প্রক্সি: <b>{len(existing)} টি</b>\n"
        f"⚡ <b>ভার্চুয়াল ট্যাব ও ০.২৫ সে. হাই-স্পিড রোটেশন সক্রিয়!</b>",
        parse_mode="HTML"
    )

async def clear_proxies_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    proxy_file = os.path.join(os.path.dirname(__file__), "website", "proxies.txt")
    if os.path.exists(proxy_file):
        with open(proxy_file, "w", encoding="utf-8") as f:
            f.write("# Proxies cleared\n")
    from website.proxy_manager import proxy_mgr
    proxy_mgr.load_proxies()
    try:
        from website.worker import reload_worker_pool
        reload_worker_pool()
    except Exception:
        pass
    await update.message.reply_text(
        "🗑 <b>সব প্রক্সি মুছে ফেলা হয়েছে।</b>\nবট এখন সরাসরি সিঙ্গেল আইপি মোডে চলবে।",
        parse_mode="HTML"
    )

async def start_monitor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    config["is_running"] = True
    save_config(config)
    await update.message.reply_text("🟢 মনিটরিং <b>চালু</b> করা হয়েছে!", reply_markup=get_persistent_keyboard(), parse_mode="HTML")

async def stop_monitor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    config["is_running"] = False
    save_config(config)
    await update.message.reply_text("🔴 মনিটরিং <b>বন্ধ</b> করা হয়েছে!", reply_markup=get_persistent_keyboard(), parse_mode="HTML")

async def check_now_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    status_msg = await update.message.reply_text("🔄 নতুন SMS চেক করা হচ্ছে, অনুগ্রহ করে অপেক্ষা করুন...")
    new_found = await process_sms_check(context.application)
    await status_msg.edit_text(
        f"✅ চেক সম্পন্ন হয়েছে!\n📬 নতুন মেসেজ পাওয়া গেছে: <b>{new_found} টি</b>",
        reply_markup=get_main_keyboard(),
        parse_mode="HTML"
    )

# --- Text Input & Permanent Button Handler ---

async def text_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    
    state = context.user_data.get("awaiting")
    text = update.message.text.strip() if update.message.text else ""

    if state == "add_admin":
        if text.lower() == "cancel" or text.lower() == "বাতিল":
            context.user_data["awaiting"] = None
            text_menu, kb = render_admins_menu()
            await update.message.reply_text("❌ বাতিল করা হয়েছে।", reply_markup=kb)
            return

        try:
            new_id = int(text)
            if new_id in config["admins"]:
                context.user_data["awaiting"] = None
                await update.message.reply_text(
                    f"⚠️ ইউজার <code>{new_id}</code> ইতিমধ্যে এডমিন তালিকায় আছে!",
                    reply_markup=get_persistent_keyboard(),
                    parse_mode="HTML"
                )
                return

            config["admins"].append(new_id)
            save_config(config)
            try:
                threading.Thread(target=sync_cloud_store, daemon=True).start()
            except Exception:
                pass
            context.user_data["awaiting"] = None
            
            text_menu, kb = render_admins_menu()
            await update.message.reply_text(
                f"✅ নতুন এডমিন <code>{new_id}</code> সফলভাবে যুক্ত করা হয়েছে!\n\n" + text_menu,
                reply_markup=kb,
                parse_mode="HTML"
            )
        except ValueError:
            await update.message.reply_text(
                "❌ অবৈধ আইডি! দয়া করে শুধুমাত্র সংখ্যাযুক্ত Telegram User ID পাঠান (যেমন: <code>5597944494</code>)।\n"
                "বাতিল করতে 'cancel' লিখুন।",
                parse_mode="HTML"
            )
        return


    # Permanent Reply Keyboard Taps
    if "কপি নাম্বার" in text or "সংগৃহীত নাম্বার" in text or text in ["/numbers", "/copy"]:
        text_menu, keyboard = render_country_selection_menu()
        await update.message.reply_text(text_menu, reply_markup=keyboard, parse_mode="HTML")
        return

    if "ওয়েবসাইট" in text or "website" in text or "লাইভ" in text or text in ["/web", "/live", "/dashboard"]:
        web_url = "https://sms-worker-bot-production.up.railway.app/"
        await update.message.reply_text(
            f"⚡ <b>লাইভ টেলিগ্রাম OTP ওয়েবসাইট:</b>\n"
            f"<a href=\"{web_url}\">{web_url}</a>\n\n"
            f"📱 <b>সুবিধাসমূহ:</b>\n"
            f"• সব টেলিগ্রাম কোড ০-মিলি-সেকেন্ডে লাইভ দেখতে পারবেন।\n"
            f"• 📋 <b>Copy</b> বাটনে চাপলেই নাম্বার বা ওটিপি কপি হয়ে যাবে।\n"
            f"• নতুন কোড আসতেই স্বয়ংক্রিয় নোটিফিকেশন সাউন্ড বাজবে!",
            reply_markup=get_persistent_keyboard(),
            parse_mode="HTML"
        )
        return

    if "Check Now" in text or text == "/check_now":
        status_msg = await update.message.reply_text("🔄 নতুন SMS চেক করা হচ্ছে, অনুগ্রহ করে অপেক্ষা করুন...")
        new_found = await process_sms_check(context.application)
        await status_msg.edit_text(
            f"✅ চেক সম্পন্ন হয়েছে!\n📬 নতুন মেসেজ পাওয়া গেছে: <b>{new_found} টি</b>",
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )
        return

    if "পোর্টাল একাউন্ট" in text or "একাউন্ট" in text or text == "/accounts":
        text_menu, keyboard = render_accounts_menu()
        await update.message.reply_text(text_menu, reply_markup=keyboard, parse_mode="HTML")
        return

    if "স্ট্যাটাস" in text or text.lower() == "status" or text == "/status":
        await update.message.reply_text(
            get_status_text(),
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )
        return

    if "Stop Monitor" in text or text == "/stop_monitor":
        config["is_running"] = False
        save_config(config)
        await update.message.reply_text(
            "🔴 মনিটরিং <b>বন্ধ</b> করা হয়েছে!",
            reply_markup=get_persistent_keyboard(),
            parse_mode="HTML"
        )
        return

    if "Start Monitor" in text or text == "/start_monitor":
        config["is_running"] = True
        save_config(config)
        await update.message.reply_text(
            "🟢 মনিটরিং <b>চালু</b> করা হয়েছে!",
            reply_markup=get_persistent_keyboard(),
            parse_mode="HTML"
        )
        return

    if "রিফ্রেশ" in text or "ইন্টারভাল" in text or text == "/interval":
        keyboard = [
            [
                InlineKeyboardButton("⚡ 0.35s (Turbo Multi 🚀)", callback_data="set_int_0.35"),
                InlineKeyboardButton("⚡ 0.5s (Fast ⚡)", callback_data="set_int_0.5"),
                InlineKeyboardButton("⚡ 1.0s (Normal)", callback_data="set_int_1.0")
            ],
            [
                InlineKeyboardButton("⏱ 1.5s (Single Safe)", callback_data="set_int_1.5"),
                InlineKeyboardButton("⏱ 2.0s", callback_data="set_int_2.0"),
                InlineKeyboardButton("⏱ 3.0s", callback_data="set_int_3.0")
            ],
            [
                InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")
            ]
        ]
        curr_int = config.get("interval", 1.5)
        await update.message.reply_text(
            f"⏱ <b>রিফ্রেশ ইন্টারভাল নির্বাচন করুন:</b>\n"
            f"বর্তমান নির্ধারণ: <code>{curr_int} সেকেন্ড</code>\n\n"
            f"<i>বাটনে ক্লিক করুন অথবা কাস্টম সময়ের জন্য <code>/interval &lt;seconds&gt;</code> লিখুন।</i>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return

    if "কিওয়ার্ড" in text or text == "/keywords":
        await keywords_command(update, context)
        return

    if "এডমিন" in text or text == "/admins":
        text_menu, keyboard = render_admins_menu()
        await update.message.reply_text(text_menu, reply_markup=keyboard, parse_mode="HTML")
        return

# --- Callback Query Handler (Button clicks) ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        if not await check_auth(update):
            return

        data = query.data

        if data == "toggle_monitor":
            await safe_answer(query)
            config["is_running"] = not config["is_running"]
            save_config(config)
            await safe_edit_message(
                query,
                get_status_text(),
                reply_markup=get_main_keyboard(),
                parse_mode="HTML"
            )

        elif data == "show_status":
            await safe_answer(query)
            await safe_edit_message(
                query,
                get_status_text(),
                reply_markup=get_main_keyboard(),
                parse_mode="HTML"
            )

        elif data == "main_menu":
            await safe_answer(query)
            context.user_data["awaiting"] = None
            await safe_edit_message(
                query,
                get_status_text(),
                reply_markup=get_main_keyboard(),
                parse_mode="HTML"
            )

        elif data == "menu_accounts":
            await safe_answer(query)
            text, keyboard = render_accounts_menu()
            await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")

        elif data == "refresh_accounts_view":
            text, keyboard = render_accounts_menu()
            await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")
            await safe_answer(query, "🔄 একাউন্ট স্ট্যাটাস রিফ্রেশ করা হয়েছে!")

        elif data.startswith("relogin_acc_"):
            sec_u = data[len("relogin_acc_"):]
            await safe_answer(query, f"🔄 {sec_u} লগইন করা হচ্ছে...")
            success = await asyncio.to_thread(scraper.login_account, sec_u)
            if success:
                await safe_answer(query, f"✅ {sec_u} লগইন সফল হয়েছে!", show_alert=True)
            else:
                await safe_answer(query, f"❌ {sec_u} লগইন ব্যর্থ হয়েছে! পাসওয়ার্ড বা ক্যাপচা চেক করুন।", show_alert=True)
            text, keyboard = render_accounts_menu()
            await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")

        elif data == "relogin_all_accounts":
            await safe_answer(query, "🔄 সকল একাউন্ট লগইন করা হচ্ছে...")
            await asyncio.to_thread(scraper.login_all)
            text, keyboard = render_accounts_menu()
            await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")
            await safe_answer(query, "✅ সকল একাউন্ট রি-লগইন প্রক্রিয়া সম্পন্ন হয়েছে!", show_alert=True)

        elif data == "menu_admins":
            await safe_answer(query)
            text, keyboard = render_admins_menu()
            await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")

        elif data == "prompt_add_admin":
            await safe_answer(query)
            context.user_data["awaiting"] = "add_admin"
            keyboard = [[InlineKeyboardButton("🔙 বাতিল করুন (Cancel)", callback_data="menu_admins")]]
            await safe_edit_message(
                query,
                "➕ <b>নতুন এডমিন যোগ করুন:</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "দয়া করে যাকে এডমিন করতে চান তার <b>Telegram User ID</b> লিখে এই চ্যাটে মেসেজ পাঠান।\n\n"
                "<i>(অথবা কমান্ড ব্যবহার করতে পারেন: <code>/add_admin &lt;User_ID&gt;</code>)</i>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )

        elif data == "menu_del_admin":
            await safe_answer(query)
            admins = config.get("admins", [SUPER_ADMIN_ID])
            removable = [a for a in admins if int(a) != SUPER_ADMIN_ID]
            
            if not removable:
                keyboard = [[InlineKeyboardButton("🔙 ফিরে যান", callback_data="menu_admins")]]
                await safe_edit_message(
                    query,
                    "ℹ️ রিমুভ করার মতো কোনো সাব-এডমিন নেই। মূল সুপার এডমিনকে মোছা যাবে না।",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML"
                )
                return

            keyboard = []
            for a in removable:
                keyboard.append([
                    InlineKeyboardButton(f"❌ Remove {a}", callback_data=f"del_admin_{a}")
                ])
            keyboard.append([InlineKeyboardButton("🔙 ফিরে যান", callback_data="menu_admins")])

            await safe_edit_message(
                query,
                "➖ <b>যাকে রিমুভ করতে চান নিচের বাটনে চাপুন:</b>\n\n"
                "<i>(অথবা কমান্ড দিন: <code>/del_admin &lt;User_ID&gt;</code>)</i>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )

        elif data.startswith("del_admin_"):
            target_id = int(data[len("del_admin_"):])
            if target_id == SUPER_ADMIN_ID:
                await safe_answer(query, "মূল সুপার এডমিনকে রিমুভ করা যাবে না!", show_alert=True)
                return
            if target_id in config["admins"]:
                config["admins"].remove(target_id)
                save_config(config)
                try:
                    threading.Thread(target=sync_cloud_store, daemon=True).start()
                except Exception:
                    pass
                await safe_answer(query, f"এডমিন {target_id} রিমুভ হয়েছে!")
            else:
                await safe_answer(query)
            
            text, keyboard = render_admins_menu()
            await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")

        elif data == "menu_numbers":
            await safe_answer(query)
            text, keyboard = render_country_selection_menu()
            await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")

        elif data == "refresh_numbers":
            text, keyboard = render_country_selection_menu()
            await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")
            await safe_answer(query, "🔄 দেশভিত্তিক নাম্বার তালিকা রিফ্রেশ করা হয়েছে!")

        elif data.startswith("view_c_"):
            await safe_answer(query)
            iso = data[len("view_c_"):]
            text, keyboard = render_country_numbers_view(iso)
            await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")

        elif data.startswith("refresh_view_c_"):
            iso = data[len("refresh_view_c_"):]
            text, keyboard = render_country_numbers_view(iso)
            await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")
            await safe_answer(query, "🔄 এই দেশের নাম্বার তালিকা রিফ্রেশ করা হয়েছে!")

        elif data.startswith("consume_c_"):
            iso = data[len("consume_c_"):]
            count, consumed_list = consume_numbers_for_country(iso)
            if count > 0:
                await safe_answer(
                    query,
                    f"✅ {count}টি নাম্বার সংগ্রহ তালিকা থেকে ডিলিট হয়েছে এবং ভবিষ্যতে আর সংগ্রহ করা হবে না!",
                    show_alert=True
                )
            else:
                await safe_answer(query, "কোনো সক্রিয় নাম্বার পাওয়া যায়নি!", show_alert=True)
            text, keyboard = render_country_selection_menu()
            await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")

        elif data.startswith("down_consume_c_"):
            iso = data[len("down_consume_c_"):]
            groups = group_numbers_by_country()
            country_title = groups.get(iso, {}).get("country", iso) if iso != "ALL" else "All_Countries"
            
            count, consumed_list = consume_numbers_for_country(iso)
            if count == 0:
                await safe_answer(query, "কোনো নাম্বার পাওয়া যায়নি!", show_alert=True)
                text, keyboard = render_country_selection_menu()
                await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")
                return

            content = "\n".join([f"+{n}" for n in consumed_list])
            file_obj = io.BytesIO(content.encode("utf-8"))
            safe_country_name = re.sub(r'[^a-zA-Z0-9_]', '_', country_title)
            filename = f"{safe_country_name}_numbers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            file_obj.name = filename

            try:
                await context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=file_obj,
                    filename=filename,
                    caption=(
                        f"📋 <b>{country_title}</b>-এর মোট {count}টি নাম্বারের ফাইল।\n"
                        f"<i>এই নাম্বারগুলো সংগ্রহ তালিকা থেকে মুছে দেওয়া হয়েছে এবং ভবিষ্যতে আর সংগ্রহ করা হবে না।</i>"
                    ),
                    parse_mode="HTML"
                )
                await safe_answer(query, "✅ ফাইল পাঠানো হয়েছে ও নাম্বার ডিলিট হয়েছে!", show_alert=True)
            except Exception as e:
                logger.error(f"Error sending document: {e}")
                await safe_answer(query, "❌ ফাইল পাঠানো ব্যর্থ হয়েছে!", show_alert=True)

            text, keyboard = render_country_selection_menu()
            await safe_edit_message(query, text, reply_markup=keyboard, parse_mode="HTML")

        elif data == "list_keywords":
            await safe_answer(query)
            if not config["keywords"]:
                keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]
                await safe_edit_message(
                    query,
                    "🔍 বর্তমানে কোনো কিওয়ার্ড সেট করা নেই।\nনতুন কিওয়ার্ড যোগ করতে চ্যাটে লিখুন:\n<code>/add &lt;keyword&gt;</code>",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML"
                )
                return

            keyboard = []
            for kw in config["keywords"]:
                keyboard.append([
                    InlineKeyboardButton(f"❌ Remove '{kw}'", callback_data=f"del_kw_{kw}")
                ])
            keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])

            await safe_edit_message(
                query,
                f"🔍 <b>বর্তমান সক্রিয় কিওয়ার্ড তালিকা:</b>\n"
                f"• " + "\n• ".join([f"<code>{k}</code>" for k in config["keywords"]]) + "\n\n"
                f"<i>মুছে ফেলতে নিচের বাটন চাপুন অথবা <code>/add &lt;keyword&gt;</code> দিয়ে নতুন যোগ করুন:</i>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )

        elif data.startswith("del_kw_"):
            await safe_answer(query)
            kw_to_del = data[len("del_kw_"):]
            if kw_to_del in config["keywords"]:
                config["keywords"].remove(kw_to_del)
                save_config(config)
            if not config["keywords"]:
                keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]
                await safe_edit_message(
                    query,
                    f"🗑 কিওয়ার্ড <b>{kw_to_del}</b> মুছে ফেলা হয়েছে!\nএখন কোনো কিওয়ার্ড নেই।",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML"
                )
            else:
                keyboard = []
                for kw in config["keywords"]:
                    keyboard.append([
                        InlineKeyboardButton(f"❌ Remove '{kw}'", callback_data=f"del_kw_{kw}")
                    ])
                keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])
                await safe_edit_message(
                    query,
                    f"🗑 <b>{kw_to_del}</b> মুছে ফেলা হয়েছে!\n\n"
                    f"🔍 <b>বর্তমান সক্রিয় কিওয়ার্ড তালিকা:</b>\n"
                    f"• " + "\n• ".join([f"<code>{k}</code>" for k in config["keywords"]]),
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="HTML"
                )

        elif data == "menu_interval":
            await safe_answer(query)
            keyboard = [
                [
                    InlineKeyboardButton("⚡ 0.35s (Turbo Multi 🚀)", callback_data="set_int_0.35"),
                    InlineKeyboardButton("⚡ 0.5s (Fast ⚡)", callback_data="set_int_0.5"),
                    InlineKeyboardButton("⚡ 1.0s (Normal)", callback_data="set_int_1.0")
                ],
                [
                    InlineKeyboardButton("⏱ 1.5s (Single Safe)", callback_data="set_int_1.5"),
                    InlineKeyboardButton("⏱ 2.0s", callback_data="set_int_2.0"),
                    InlineKeyboardButton("⏱ 3.0s", callback_data="set_int_3.0")
                ],
                [
                    InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")
                ]
            ]
            curr_int = config.get("interval", 1.5)
            await safe_edit_message(
                query,
                f"⏱ <b>রিফ্রেশ ইন্টারভাল নির্বাচন করুন:</b>\n"
                f"বর্তমান নির্ধারণ: <code>{curr_int} সেকেন্ড</code>\n\n"
                f"<i>বাটনে ক্লিক করুন অথবা কাস্টম সময়ের জন্য <code>/interval &lt;seconds&gt;</code> লিখুন।</i>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )

        elif data.startswith("set_int_"):
            await safe_answer(query)
            val_str = data.split("_")[-1]
            try:
                new_int = float(val_str)
            except ValueError:
                new_int = 1.5
            config["interval"] = new_int
            save_config(config)
            await safe_edit_message(
                query,
                f"✅ রিফ্রেশ ইন্টারভাল পরিবর্তন করে <b>{new_int} সেকেন্ড</b> করা হয়েছে!",
                reply_markup=get_main_keyboard(),
                parse_mode="HTML"
            )

        elif data == "check_now":
            await safe_answer(query, "🔄 চেক করা হচ্ছে...")
            new_found = await process_sms_check(context.application)
            await safe_edit_message(
                query,
                f"✅ চেক সম্পন্ন হয়েছে!\n📬 নতুন মেসেজ পাওয়া গেছে: <b>{new_found} টি</b>\n\n"
                + get_status_text(),
                reply_markup=get_main_keyboard(),
                parse_mode="HTML"
            )
        else:
            await safe_answer(query)

    except Exception as e:
        logger.error(f"Error in button_handler: {e}", exc_info=True)
        try:
            await query.answer("⚠️ একটি সমস্যা দেখা দিয়েছে, অনুগ্রহ করে আবার চেষ্টা করুন।")
        except Exception:
            pass

# --- Core Scraping & Group Dispatch Logic ---

sms_check_lock = asyncio.Lock()
telegram_dispatch_queue = asyncio.Queue(maxsize=1000)
bot_app_instance = None

async def telegram_dispatcher_worker(app):
    """
    Dedicated background worker that sends messages to Telegram sequentially and chronologically.
    This completely decouples Telegram API delivery from the scraping loop and Web Dashboard,
    ensuring the Web Dashboard receives data in 0ms BEFORE Telegram, with ZERO blocking!
    """
    logger.info("Telegram background dispatcher worker actively running.")
    while True:
        try:
            item = await telegram_dispatch_queue.get()
            group_id = item.get("group_id")
            msg_text = item.get("text")
            dedup_key = item.get("dedup_key")
            raw_num = item.get("number")

            if not group_id or not msg_text:
                telegram_dispatch_queue.task_done()
                continue

            sent_ok = False
            for attempt in range(3):
                try:
                    await app.bot.send_message(
                        chat_id=group_id,
                        text=msg_text,
                        parse_mode="HTML",
                        read_timeout=10,
                        write_timeout=10,
                        connect_timeout=5
                    )
                    logger.info(f"Instant Sent SMS to group for number: {raw_num} (Key: {dedup_key})")
                    sent_ok = True
                    break
                except Exception as send_err:
                    logger.error(f"Attempt {attempt+1}/3 failed to send SMS to group for {raw_num}: {send_err}")
                    await asyncio.sleep(0.3)

            if not sent_ok:
                seen_set.discard(dedup_key)
                if dedup_key in config.get("seen_hashes", []):
                    try:
                        config["seen_hashes"].remove(dedup_key)
                    except ValueError:
                        pass

            telegram_dispatch_queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in telegram_dispatcher_worker: {e}")
            await asyncio.sleep(0.2)

async def process_sms_check(app=None, target_client=None):
    global bot_app_instance
    if app:
        bot_app_instance = app
    keywords = config.get("keywords", [])
    group_id = config.get("group_id")
    new_sent_count = 0
    account_name = target_client.username if target_client else "Portal"

    try:
        # Query portal directly with configured keywords so MySQL database filters out all non-target spam:
        active_keywords = [kw.strip() for kw in keywords if kw.strip()]
        search_targets = active_keywords if active_keywords else [""]

        for kw_target in search_targets:
            t_fetch_start = time.time()
            if target_client:
                records = await asyncio.to_thread(scraper.fetch_records_for_client, target_client, keyword=kw_target, display_length=25)
            else:
                records = await asyncio.to_thread(scraper.fetch_records, keyword=kw_target, display_length=25)

            if not records:
                continue

            # Sort strictly chronological (oldest first, newest last):
            records.sort(key=lambda r: str(r.get("time", "")))

            for rec in records:
                # In-memory keyword match (0.001ms in RAM):
                rec_cli = str(rec.get("cli", "")).strip().lower()
                rec_msg = str(rec.get("message", "")).strip().lower()

                matched_kw = ""
                if keywords:
                    for kw in keywords:
                        kw_clean = kw.strip().lower()
                        if kw_clean and (kw_clean in rec_cli or kw_clean in rec_msg):
                            matched_kw = kw
                            break
                    if not matched_kw:
                        continue
                else:
                    matched_kw = "ALL"

                # Filter: Exclusively Telegram OTP codes (drop non-OTP spam without code):
                raw_msg = str(rec.get("message", "")).strip()
                otp = extract_otp(raw_msg)
                if not otp:
                    continue

                dedup_key = make_dedup_key(rec)
                if dedup_key not in seen_set:
                    seen_set.add(dedup_key)
                    config["seen_hashes"].append(dedup_key)
                    config["total_sent"] += 1
                    new_sent_count += 1

                    raw_num = str(rec.get("number", "")).strip()
                    clean_num = re.sub(r'[^0-9]', '', raw_num)
                    if clean_num:
                        consumed_set = set(re.sub(r'[^0-9]', '', str(n)) for n in config.get("consumed_numbers", []))
                        collected_set = set(re.sub(r'[^0-9]', '', str(n)) for n in config.get("collected_numbers", []))
                        if clean_num not in consumed_set and clean_num not in collected_set:
                            config["collected_numbers"].append(clean_num)
                            try:
                                numbers_store.collect_number(clean_num, otp=otp)
                            except Exception:
                                pass

                    fetch_elapsed = time.time() - t_fetch_start
                    delay = min(1.2, max(0.4, round(fetch_elapsed, 1)))
                    msg_text, otp_payload = format_sms_message_with_payload(
                        rec, keyword=matched_kw, elapsed_delay=delay, otp=otp, account=account_name
                    )
                    
                    # 1. Instant Push to connected Live Web Dashboard (0ms latency!):
                    # Broadcast to Web IMMEDIATELY BEFORE anything touches Telegram!
                    broadcast_otp_to_web(otp_payload)

                    # 2. Asynchronous Decoupled Telegram Queue (Never blocks web or scraper!):
                    if group_id:
                        try:
                            telegram_dispatch_queue.put_nowait({
                                "group_id": group_id,
                                "text": msg_text,
                                "dedup_key": dedup_key,
                                "number": rec.get("number")
                            })
                        except Exception as q_err:
                            logger.error(f"Error enqueueing to Telegram queue: {q_err}")

    except Exception as e:
        logger.error(f"Error checking records: {e}")

    if new_sent_count > 0:
        save_config(config)
        try:
            threading.Thread(target=sync_cloud_store, daemon=True).start()
        except Exception:
            pass

    return new_sent_count

async def background_monitoring_loop(app):
    global bot_app_instance
    bot_app_instance = app
    # Start the decoupled Telegram sender worker in the background:
    asyncio.create_task(telegram_dispatcher_worker(app))
    logger.info("Initializing background monitoring loop with concurrent staggered workers...")
    
    # 0. Pre-authenticate all pool accounts in parallel:
    try:
        logger.info("Pre-authenticating all accounts in pool...")
        login_res = await asyncio.to_thread(scraper.login_all)
        logger.info(f"Pool pre-authentication results: {login_res}")
        await asyncio.sleep(1.0)
    except Exception as e:
        logger.error(f"Error during pool pre-authentication: {e}")

    # 1. Pre-seed today's records and numbers on startup:
    try:
        keywords = config.get("keywords", [])
        active_keywords = [kw.strip() for kw in keywords if kw.strip()]
        search_targets = active_keywords if active_keywords else [""]
        
        consumed_set = set(re.sub(r'[^0-9]', '', str(n)) for n in config.get("consumed_numbers", []))
        collected_set = set(re.sub(r'[^0-9]', '', str(n)) for n in config.get("collected_numbers", []))
        new_collected_count = 0

        logger.info("Pre-seeding historical numbers and existing seen hashes...")
        for kw_target in search_targets:
            init_recs = await asyncio.to_thread(scraper.fetch_records, keyword=kw_target, display_length=40)
            if init_recs:
                init_recs.sort(key=lambda r: str(r.get("time", "")))
                for r in init_recs:
                    otp = extract_otp(r.get("message", ""))
                    if not otp:
                        continue

                    k = make_dedup_key(r)
                    clean_num = re.sub(r'[^0-9]', '', str(r.get("number", "")))
                    already_seen = (
                        k in config.get("seen_hashes", []) or
                        any(clean_num in h and str(otp) in h for h in config.get("seen_hashes", []))
                    )
                    if already_seen:
                        seen_set.add(k)
                        if k not in config["seen_hashes"]:
                            config["seen_hashes"].append(k)

                    raw_num = str(r.get("number", "")).strip()
                    clean_num = re.sub(r'[^0-9]', '', raw_num)
                    if clean_num and clean_num not in consumed_set and clean_num not in collected_set:
                        config["collected_numbers"].append(clean_num)
                        collected_set.add(clean_num)
                        new_collected_count += 1
                        try:
                            numbers_store.collect_number(clean_num, otp=otp)
                        except Exception:
                            pass

                    # Populate recent OTP buffer for initial web dashboard view:
                    try:
                        _, otp_payload = format_sms_message_with_payload(r, keyword="Telegram", elapsed_delay=1, otp=otp)
                        if not any(o.get("id") == otp_payload.get("id") for o in recent_otps_buffer):
                            recent_otps_buffer.append(otp_payload)
                    except Exception:
                        pass

        logger.info(f"Startup seeding complete: {len(seen_set)} seen keys, {len(config['collected_numbers'])} collected numbers ({new_collected_count} newly seeded from today, {len(recent_otps_buffer)} in web feed).")
        save_config(config)
    except Exception as e:
        logger.error(f"Error during startup seeding: {e}")

    await asyncio.sleep(1.0)

    # 2. Continuous Concurrent Staggered Monitoring Workers:
    # Staggered by 0.7s to achieve sub-second round-trip query cadence with zero downtime!
    clients = scraper.get_clients()
    if not clients:
        logger.warning("No clients in pool! Waiting...")
        while not scraper.get_clients():
            await asyncio.sleep(2.0)
        clients = scraper.get_clients()

    num_clients = len(clients)
    stagger_step = 0.7  # 0.7s tight stagger between accounts

    async def run_account_worker(client, initial_delay):
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)
        logger.info(f"Monitoring worker started for {client.username} (stagger: {initial_delay:.2f}s)")
        while True:
            try:
                if config.get("is_running", True):
                    await process_sms_check(app, target_client=client)
            except asyncio.CancelledError:
                break
            except Exception as w_err:
                logger.error(f"Worker error for {client.username}: {w_err}")

            poll_interval = max(0.12, float(config.get("interval", 0.2)))
            await asyncio.sleep(poll_interval)

    worker_tasks = []
    for i, c in enumerate(clients):
        delay = i * stagger_step
        worker_tasks.append(asyncio.create_task(run_account_worker(c, delay)))

    logger.info(f"All {num_clients} concurrent account workers actively running at ultra-fast 0.7s cadence!")
    await asyncio.gather(*worker_tasks)

DASHBOARD_FILE = os.path.join(os.path.dirname(__file__), "dashboard.html")

async def handle_dashboard(request):
    try:
        if os.path.exists(DASHBOARD_FILE):
            with open(DASHBOARD_FILE, "r", encoding="utf-8") as f:
                content = f.read()
            return web.Response(text=content, content_type="text/html", charset="utf-8")
        else:
            return web.Response(text="<h2>Dashboard template not found</h2>", content_type="text/html", status=404)
    except Exception as e:
        return web.Response(text=f"Error loading dashboard: {e}", status=500)

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
    q = asyncio.Queue(maxsize=150)
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
        for c in scraper.get_clients():
            asyncio.create_task(process_sms_check(bot_app_instance, target_client=c))
    return web.json_response({
        "status": "ok",
        "count": len(recent_otps_buffer),
        "otps": list(recent_otps_buffer)
    }, headers={'Access-Control-Allow-Origin': '*'})

async def handle_health_check(request):
    return web.json_response({
        "status": "online",
        "bot": "SMS Monitor Bot Live",
        "total_sent": config.get("total_sent", 0),
        "keywords": config.get("keywords", []),
        "interval": config.get("interval", 0.2),
        "accounts_count": len(config.get("accounts", [])),
        "accounts_status": scraper.get_accounts_status(),
        "collected_numbers_count": numbers_store.get_collected_count(),
        "consumed_numbers_count": numbers_store.get_consumed_count(),
        "web_live_clients": len(sse_clients)
    })

async def handle_logs(request):
    return web.Response(text="\n".join(recent_logs_buffer), content_type="text/plain", charset="utf-8")

async def start_web_server():
    port = int(os.environ.get("PORT", 8080))
    server_app = web.Application()
    server_app.router.add_get("/", handle_dashboard)
    server_app.router.add_get("/dashboard", handle_dashboard)
    server_app.router.add_get("/live", handle_dashboard)
    server_app.router.add_get("/api/stream", handle_stream)
    server_app.router.add_get("/api/recent-otps", handle_recent_otps)
    server_app.router.add_get("/health", handle_health_check)
    server_app.router.add_get("/logs", handle_logs)
    runner = web.AppRunner(server_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Fast Web Dashboard & health server listening on port {port}")

async def post_init(application):
    await asyncio.to_thread(fetch_cloud_store)
    if not os.environ.get("SEPARATE_WEB_SERVER"):
        application.create_task(start_web_server())
    if not os.environ.get("BOT_STANDALONE_COMMANDS"):
        application.create_task(background_monitoring_loop(application))
    logger.info("Persistent store restored. Background monitor scheduled.")

def main():
    if os.environ.get("BOT_DISABLED") == "true":
        logger.info("Bot is disabled via BOT_DISABLED=true. Exiting process.")
        sys.exit(0)
    token = os.environ.get("BOT_TOKEN") or config.get("bot_token")
    if not token or token == "DISABLED":
        logger.info("Bot token disabled or not configured. Exiting process.")
        sys.exit(0)

    app = ApplicationBuilder().token(token).post_init(post_init).build()

    # Register Command Handlers
    app.add_handler(CommandHandler(["start", "menu"], start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler(["numbers", "copy"], numbers_command))
    app.add_handler(CommandHandler("accounts", accounts_command))
    app.add_handler(CommandHandler("admins", admins_command))
    app.add_handler(CommandHandler("add_admin", add_admin_command))
    app.add_handler(CommandHandler("del_admin", del_admin_command))
    app.add_handler(CommandHandler("add", add_keyword_command))
    app.add_handler(CommandHandler("del", del_keyword_command))
    app.add_handler(CommandHandler("keywords", keywords_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("interval", interval_command))
    app.add_handler(CommandHandler(["proxies", "proxy"], proxies_command))
    app.add_handler(CommandHandler(["setproxies", "addproxy", "addproxies"], set_proxies_command))
    app.add_handler(CommandHandler("clearproxies", clear_proxies_command))
    app.add_handler(CommandHandler("start_monitor", start_monitor_command))
    app.add_handler(CommandHandler("stop_monitor", stop_monitor_command))
    app.add_handler(CommandHandler("check_now", check_now_command))

    # Register Callback Query Handler for buttons
    app.add_handler(CallbackQueryHandler(button_handler))

    # Register Text message handler (for interactive inputs & permanent bottom buttons)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input_handler))

    logger.info("Starting Telegram Bot with polling...")
    app.run_polling(
        drop_pending_updates=True,
        poll_interval=1.0,
        timeout=10,
        allowed_updates=["message", "callback_query"]
    )

if __name__ == "__main__":
    main()

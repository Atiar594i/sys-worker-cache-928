import asyncio
import json
import logging
import hashlib
import html
import os
import sys
import re
import io
import phonenumbers
from phonenumbers import geocoder
from aiohttp import web
from datetime import datetime, timezone, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)
from scraper import FlexSMSClient

# Configure Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("SMSMonitorBot")

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
SUPER_ADMIN_ID = 5597944494

def load_config():
    default_config = {
        "bot_token": "8784442674:AAE-nwM7m7svAL9nxipswL8f6CwvssKfIyc",
        "group_id": "-1003996842463",
        "username": "Atiar59_FD",
        "password": "Atiar5942@#",
        "base_url": "http://168.119.13.175/ints",
        "keywords": ["Telegram"],
        "interval": 2,
        "is_running": True,
        "total_sent": 0,
        "seen_hashes": [],
        "collected_numbers": [],
        "consumed_numbers": [],
        "admins": [SUPER_ADMIN_ID],
        "super_admin": SUPER_ADMIN_ID
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
    return default_config

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

config = load_config()
seen_set = set(config.get("seen_hashes", []))
scraper = FlexSMSClient(
    base_url=config.get("base_url"),
    username=config.get("username"),
    password=config.get("password")
)

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
            await update.callback_query.answer(
                "⛔ Unauthorized! আপনার এই বট ব্যবহারের অনুমতি নেই।",
                show_alert=True
            )
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

def get_country_info(phone_str):
    clean = str(phone_str).lstrip("+").strip()
    try:
        p = phonenumbers.parse("+" + clean)
        country_name = geocoder.country_name_for_number(p, "en")
        iso = phonenumbers.region_code_for_number(p)
        if not country_name:
            country_name = f"Code +{p.country_code}"
        if not iso:
            iso = "XX"
        flag = "".join(chr(127397 + ord(c)) for c in iso.upper()) if len(iso) == 2 and iso != "XX" else "🌐"
        return country_name, iso, flag, p.country_code
    except Exception:
        return "Other Countries", "XX", "🌐", 0

def group_numbers_by_country():
    nums = config.get("collected_numbers", [])
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
    total = len(config.get("collected_numbers", []))
    
    if total == 0:
        text = (
            "📱 <b>বর্তমানে কোনো নতুন নাম্বার সংরক্ষিত নেই।</b>\n\n"
            "নতুন SMS আসামাত্র নতুন নাম্বার স্বয়ংক্রিয়ভাবে এখানে দেশভিত্তিক যুক্ত হবে।"
        )
        keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]
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
    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")])

    return text, InlineKeyboardMarkup(keyboard)

def render_country_numbers_view(iso):
    groups = group_numbers_by_country()
    if iso == "ALL":
        target_numbers = config.get("collected_numbers", [])
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

    subset = target_numbers[-100:]
    num_list_text = "\n".join([f"+{n}" for n in reversed(subset)])
    extra_note = f"\n<i>(সর্বশেষ {len(subset)} টি দেখানো হচ্ছে)</i>" if count > 100 else ""

    text = (
        f"📱 <b>{title} (মোট {count} টি):</b>\n"
        f"<i>(নিচের বক্সে ট্যাপ করলেই সবগুলো নাম্বার একসাথে কপি হয়ে যাবে)</i>\n\n"
        f"<pre>{num_list_text}</pre>{extra_note}\n\n"
        f"⚠️ <b>কপি বা ডাউনলোড করার পর:</b>\n"
        f"নিচের <b>'✅ কপি করেছি (ডিলিট)'</b> বাটনে চাপুন। এতে এই {count}টি নাম্বার তালিকা থেকে মুছে যাবে এবং ভবিষ্যতে এই নাম্বারে কোড আসলে তা আর সংগ্রহ হবে না।"
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ কপি করেছি (ডিলিট)", callback_data=f"consume_c_{iso}"),
            InlineKeyboardButton("📥 ডাউনলোড ও ডিলিট (.txt)", callback_data=f"down_consume_c_{iso}")
        ],
        [
            InlineKeyboardButton("🔙 দেশ তালিকায় ফিরে যান", callback_data="menu_numbers")
        ]
    ]
    return text, InlineKeyboardMarkup(keyboard)

def consume_numbers_for_country(iso):
    groups = group_numbers_by_country()
    if iso == "ALL":
        to_consume = list(config.get("collected_numbers", []))
    else:
        to_consume = list(groups.get(iso, {}).get("numbers", []))
    
    if not to_consume:
        return 0, []

    if "consumed_numbers" not in config:
        config["consumed_numbers"] = []
    
    for n in to_consume:
        if n not in config["consumed_numbers"]:
            config["consumed_numbers"].append(n)

    config["collected_numbers"] = [n for n in config["collected_numbers"] if n not in to_consume]
    save_config(config)
    return len(to_consume), to_consume

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
    number = str(record.get("number", "")).strip()
    message = str(record.get("message", "")).strip()
    otp = extract_otp(message)
    if otp:
        return f"{number}_OTP_{otp}"
    else:
        msg_hash = hashlib.md5(message.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return f"{number}_MSG_{msg_hash}"

def calculate_latency(panel_time_str):
    try:
        raw_str = panel_time_str.strip()
        if raw_str.endswith("00:00:00"):
            return "N/A"
        
        panel_dt = datetime.strptime(raw_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        
        server_clock_offset = getattr(scraper, "server_clock_offset", 36.0)
        server_now = now_utc - timedelta(seconds=server_clock_offset)
        
        diff_seconds = int((server_now - panel_dt).total_seconds())

        if diff_seconds <= 1:
            return "১ সেকেন্ড লেট (ইনস্ট্যান্ট)"
        elif diff_seconds < 60:
            return f"{diff_seconds} সেকেন্ড লেট"
        elif diff_seconds < 3600:
            mins = diff_seconds // 60
            secs = diff_seconds % 60
            return f"{mins} মিনিট {secs} সেকেন্ড লেট"
        else:
            hours = diff_seconds // 3600
            mins = (diff_seconds % 3600) // 60
            return f"{hours} ঘণ্টা {mins} মিনিট লেট"
    except Exception:
        return "N/A"

def format_sms_message(rec, keyword=""):
    panel_time = html.escape(rec.get("time", "N/A"))
    number = html.escape(rec.get("number", "N/A"))
    cli = html.escape(rec.get("cli", "N/A"))
    msg = html.escape(rec.get("message", ""))
    cause = html.escape(rec.get("cause", ""))
    
    bot_delivery_time = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
    latency_str = calculate_latency(rec.get("time", ""))
    otp = extract_otp(rec.get("message", ""))
    country_name, iso, flag, code = get_country_info(number)
    
    text = (
        f"📩 <b>New SMS Received</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 <b>প্যানেল টাইম (Panel):</b> <code>{panel_time}</code>\n"
        f"⚡ <b>গ্রুপে ডেলিভারি (Bot):</b> <code>{bot_delivery_time}</code>\n"
        f"⏳ <b>বিলম্ব (Latency):</b> <code>{latency_str}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌍 <b>দেশ (Country):</b> {flag} <b>{country_name}</b>\n"
        f"📱 <b>নাম্বার (Number):</b> <code>+{number}</code>\n"
        f"🏷 <b>প্রেরক (CLI):</b> <b>{cli}</b>\n"
    )
    if otp:
        text += f"🔑 <b>ওটিপি (OTP):</b> <code>{html.escape(otp)}</code>\n"
    text += (
        f"💬 <b>মেসেজ (Message):</b>\n"
        f"<blockquote>{msg}</blockquote>\n"
    )
    if cause:
        text += f"⚠️ <b>Cause:</b> <code>{cause}</code>\n"
    if keyword:
        text += f"🔍 <b>কিওয়ার্ড:</b> <code>{html.escape(keyword)}</code>\n"
    text += f"━━━━━━━━━━━━━━━━━━━━━━"
    return text

def get_main_keyboard():
    status_btn_text = "⏹ Stop Monitor" if config["is_running"] else "▶️ Start Monitor"
    status_callback = "toggle_monitor"
    
    keyboard = [
        [
            InlineKeyboardButton(status_btn_text, callback_data=status_callback),
            InlineKeyboardButton("⚡ Check Now", callback_data="check_now")
        ],
        [
            InlineKeyboardButton("📋 Copy Numbers (দেশভিত্তিক)", callback_data="menu_numbers"),
            InlineKeyboardButton("🔍 Keywords List", callback_data="list_keywords")
        ],
        [
            InlineKeyboardButton("⏱ Refresh Time", callback_data="menu_interval"),
            InlineKeyboardButton("👥 Manage Admins", callback_data="menu_admins")
        ],
        [
            InlineKeyboardButton("📊 Status Report", callback_data="show_status")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_status_text():
    state_str = "🟢 Running (সক্রিয়)" if config["is_running"] else "🔴 Stopped (বন্ধ)"
    kw_str = ", ".join(config["keywords"]) if config["keywords"] else "(কোন কিওয়ার্ড নেই - সব আসবে)"
    total_active_nums = len(config.get("collected_numbers", []))
    total_consumed = len(config.get("consumed_numbers", []))
    total_admins = len(config.get("admins", [SUPER_ADMIN_ID]))
    return (
        f"🤖 <b>SMS Monitor Bot Control Panel</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>মনিটর অবস্থা:</b> {state_str}\n"
        f"⏱ <b>রিফ্রেশ ইন্টারভাল:</b> <code>{config['interval']} সেকেন্ড</code>\n"
        f"🔍 <b>সার্চ কিওয়ার্ড:</b> <code>{kw_str}</code>\n"
        f"🎯 <b>টার্গেট গ্রুপ:</b> <code>{config['group_id']}</code>\n"
        f"📈 <b>মোট প্রেরিত SMS:</b> <code>{config['total_sent']} টি</code>\n"
        f"📱 <b>সক্রিয় সংগৃহীত নাম্বার:</b> <code>{total_active_nums} টি</code>\n"
        f"🗑 <b>কপিকৃত/ব্যবহৃত নাম্বার:</b> <code>{total_consumed} টি</code>\n"
        f"👥 <b>মোট এডমিন:</b> <code>{total_admins} জন</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>নিচের বাটনগুলো ব্যবহার করে সবকিছু কন্ট্রোল করুন:</i>"
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

# --- Command Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    await update.message.reply_text(
        get_status_text(),
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
        await update.message.reply_text(
            f"✅ নতুন এডমিন <code>{new_id}</code> সফলভাবে যুক্ত করা হয়েছে!",
            reply_markup=get_main_keyboard(),
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
            await update.message.reply_text(
                f"🗑 এডমিন <code>{target_id}</code> সফলভাবে রিমুভ করা হয়েছে!",
                reply_markup=get_main_keyboard(),
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
        reply_markup=get_main_keyboard(),
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
            reply_markup=get_main_keyboard(),
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
    await update.message.reply_text("🧹 সব কিওয়ার্ড মুছে ফেলা হয়েছে! এখন সব SMS সার্চ করা হবে।", reply_markup=get_main_keyboard())

async def interval_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    if not context.args:
        await update.message.reply_text(
            f"⏱ বর্তমান রিফ্রেশ ইন্টারভাল: <code>{config['interval']} সেকেন্ড</code>\n"
            f"পরিবর্তন করতে লিখুন: <code>/interval &lt;seconds&gt;</code> (যেমন: <code>/interval 1</code> বা <code>/interval 2</code>)",
            parse_mode="HTML"
        )
        return

    try:
        val = int(context.args[0])
        if val < 1:
            await update.message.reply_text("⚠️ ইন্টারভাল কমপক্ষে ১ সেকেন্ড হতে হবে!")
            return
        config["interval"] = val
        save_config(config)
        await update.message.reply_text(
            f"✅ রিফ্রেশ ইন্টারভাল পরিবর্তন করে <b>{val} সেকেন্ড</b> নির্ধারণ করা হয়েছে!",
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )
    except ValueError:
        await update.message.reply_text("❌ দয়া করে সঠিক সংখ্যা প্রদান করুন (যেমন: <code>/interval 2</code>)!", parse_mode="HTML")

async def start_monitor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    config["is_running"] = True
    save_config(config)
    await update.message.reply_text("🟢 মনিটরিং <b>চালু</b> করা হয়েছে!", reply_markup=get_main_keyboard(), parse_mode="HTML")

async def stop_monitor_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return
    config["is_running"] = False
    save_config(config)
    await update.message.reply_text("🔴 মনিটরিং <b>বন্ধ</b> করা হয়েছে!", reply_markup=get_main_keyboard(), parse_mode="HTML")

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

# --- Text Input Handler ---

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
                    reply_markup=get_main_keyboard(),
                    parse_mode="HTML"
                )
                return

            config["admins"].append(new_id)
            save_config(config)
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

# --- Callback Query Handler (Button clicks) ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_auth(update):
        return

    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "toggle_monitor":
        config["is_running"] = not config["is_running"]
        save_config(config)
        await query.edit_message_text(
            get_status_text(),
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )

    elif data == "show_status":
        await query.edit_message_text(
            get_status_text(),
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )

    elif data == "main_menu":
        context.user_data["awaiting"] = None
        await query.edit_message_text(
            get_status_text(),
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )

    elif data == "menu_admins":
        text, keyboard = render_admins_menu()
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")

    elif data == "prompt_add_admin":
        context.user_data["awaiting"] = "add_admin"
        keyboard = [[InlineKeyboardButton("🔙 বাতিল করুন (Cancel)", callback_data="menu_admins")]]
        await query.edit_message_text(
            "➕ <b>নতুন এডমিন যোগ করুন:</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "দয়া করে যাকে এডমিন করতে চান তার <b>Telegram User ID</b> লিখে এই চ্যাটে মেসেজ পাঠান।\n\n"
            "<i>(অথবা কমান্ড ব্যবহার করতে পারেন: <code>/add_admin &lt;User_ID&gt;</code>)</i>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    elif data == "menu_del_admin":
        admins = config.get("admins", [SUPER_ADMIN_ID])
        removable = [a for a in admins if int(a) != SUPER_ADMIN_ID]
        
        if not removable:
            keyboard = [[InlineKeyboardButton("🔙 ফিরে যান", callback_data="menu_admins")]]
            await query.edit_message_text(
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

        await query.edit_message_text(
            "➖ <b>যাকে রিমুভ করতে চান নিচের বাটনে চাপুন:</b>\n\n"
            "<i>(অথবা কমান্ড দিন: <code>/del_admin &lt;User_ID&gt;</code>)</i>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    elif data.startswith("del_admin_"):
        target_id = int(data[len("del_admin_"):])
        if target_id == SUPER_ADMIN_ID:
            await query.answer("মূল সুপার এডমিনকে রিমুভ করা যাবে না!", show_alert=True)
            return
        if target_id in config["admins"]:
            config["admins"].remove(target_id)
            save_config(config)
            await query.answer(f"এডমিন {target_id} রিমুভ হয়েছে!")
        
        text, keyboard = render_admins_menu()
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")

    elif data == "menu_numbers":
        text, keyboard = render_country_selection_menu()
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")

    elif data.startswith("view_c_"):
        iso = data[len("view_c_"):]
        text, keyboard = render_country_numbers_view(iso)
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")

    elif data.startswith("consume_c_"):
        iso = data[len("consume_c_"):]
        count, consumed_list = consume_numbers_for_country(iso)
        if count > 0:
            await query.answer(
                f"✅ {count}টি নাম্বার সংগ্রহ তালিকা থেকে ডিলিট হয়েছে এবং ভবিষ্যতে আর সংগ্রহ করা হবে না!",
                show_alert=True
            )
        else:
            await query.answer("কোনো সক্রিয় নাম্বার পাওয়া যায়নি!", show_alert=True)
        text, keyboard = render_country_selection_menu()
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")

    elif data.startswith("down_consume_c_"):
        iso = data[len("down_consume_c_"):]
        groups = group_numbers_by_country()
        country_title = groups.get(iso, {}).get("country", iso) if iso != "ALL" else "All_Countries"
        
        count, consumed_list = consume_numbers_for_country(iso)
        if count == 0:
            await query.answer("কোনো নাম্বার পাওয়া যায়নি!", show_alert=True)
            text, keyboard = render_country_selection_menu()
            await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
            return

        content = "\n".join([f"+{n}" for n in consumed_list])
        file_obj = io.BytesIO(content.encode("utf-8"))
        filename = f"{country_title}_numbers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        file_obj.name = filename

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
        await query.answer("✅ ফাইল পাঠানো হয়েছে ও নাম্বার ডিলিট হয়েছে!", show_alert=True)
        text, keyboard = render_country_selection_menu()
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")

    elif data == "list_keywords":
        if not config["keywords"]:
            keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]
            await query.edit_message_text(
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

        await query.edit_message_text(
            f"🔍 <b>বর্তমান সক্রিয় কিওয়ার্ড তালিকা:</b>\n"
            f"• " + "\n• ".join([f"<code>{k}</code>" for k in config["keywords"]]) + "\n\n"
            f"<i>মুছে ফেলতে নিচের বাটন চাপুন অথবা <code>/add &lt;keyword&gt;</code> দিয়ে নতুন যোগ করুন:</i>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    elif data.startswith("del_kw_"):
        kw_to_del = data[len("del_kw_"):]
        if kw_to_del in config["keywords"]:
            config["keywords"].remove(kw_to_del)
            save_config(config)
        if not config["keywords"]:
            keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")]]
            await query.edit_message_text(
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
            await query.edit_message_text(
                f"🗑 <b>{kw_to_del}</b> মুছে ফেলা হয়েছে!\n\n"
                f"🔍 <b>বর্তমান সক্রিয় কিওয়ার্ড তালিকা:</b>\n"
                f"• " + "\n• ".join([f"<code>{k}</code>" for k in config["keywords"]]),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )

    elif data == "menu_interval":
        keyboard = [
            [
                InlineKeyboardButton("⚡ 1 সেকেন্ড", callback_data="set_int_1"),
                InlineKeyboardButton("⚡ 2 সেকেন্ড", callback_data="set_int_2"),
                InlineKeyboardButton("⚡ 3 সেকেন্ড", callback_data="set_int_3")
            ],
            [
                InlineKeyboardButton("⏱ 5 সেকেন্ড", callback_data="set_int_5"),
                InlineKeyboardButton("⏱ 10 সেকেন্ড", callback_data="set_int_10"),
                InlineKeyboardButton("⏱ 30 সেকেন্ড", callback_data="set_int_30")
            ],
            [
                InlineKeyboardButton("🔙 Main Menu", callback_data="main_menu")
            ]
        ]
        await query.edit_message_text(
            f"⏱ <b>রিফ্রেশ ইন্টারভাল নির্বাচন করুন:</b>\n"
            f"বর্তমান নির্ধারণ: <code>{config['interval']} সেকেন্ড</code>\n\n"
            f"<i>বাটনে ক্লিক করুন অথবা কাস্টম সময়ের জন্য <code>/interval &lt;seconds&gt;</code> লিখুন।</i>",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )

    elif data.startswith("set_int_"):
        new_int = int(data.split("_")[-1])
        config["interval"] = new_int
        save_config(config)
        await query.edit_message_text(
            f"✅ রিফ্রেশ ইন্টারভাল পরিবর্তন করে <b>{new_int} সেকেন্ড</b> করা হয়েছে!",
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )

    elif data == "check_now":
        new_found = await process_sms_check(context.application)
        await query.edit_message_text(
            f"✅ চেক সম্পন্ন হয়েছে!\n📬 নতুন মেসেজ পাওয়া গেছে: <b>{new_found} টি</b>\n\n"
            + get_status_text(),
            reply_markup=get_main_keyboard(),
            parse_mode="HTML"
        )

# --- Core Scraping & Group Dispatch Logic ---

async def process_sms_check(app):
    keywords = config.get("keywords", [])
    group_id = config.get("group_id")
    new_sent_count = 0

    search_list = keywords if keywords else [""]

    for kw in search_list:
        try:
            records = await asyncio.to_thread(scraper.fetch_records, keyword=kw, display_length=40)
            if not records:
                continue

            for rec in records:
                if kw:
                    kw_lower = kw.lower().strip()
                    msg_lower = rec.get("message", "").lower()
                    cli_lower = rec.get("cli", "").lower()
                    num_lower = rec.get("number", "").lower()
                    if kw_lower not in msg_lower and kw_lower not in cli_lower and kw_lower not in num_lower:
                        continue

                dedup_key = make_dedup_key(rec)
                if dedup_key not in seen_set:
                    seen_set.add(dedup_key)
                    config["seen_hashes"].append(dedup_key)
                    config["total_sent"] += 1
                    new_sent_count += 1

                    # Collect number ONLY if it has never been copied/consumed before:
                    raw_num = str(rec.get("number", "")).strip()
                    if raw_num:
                        consumed = config.get("consumed_numbers", [])
                        collected = config.get("collected_numbers", [])
                        if raw_num not in consumed and raw_num not in collected:
                            config["collected_numbers"].append(raw_num)

                    # Send to Telegram Group
                    msg_text = format_sms_message(rec, keyword=kw)
                    try:
                        await app.bot.send_message(
                            chat_id=group_id,
                            text=msg_text,
                            parse_mode="HTML"
                        )
                        logger.info(f"Instant Sent SMS to group for number: {rec.get('number')} (Key: {dedup_key})")
                        await asyncio.sleep(0.05)
                    except Exception as send_err:
                        logger.error(f"Failed to send message to group: {send_err}")

        except Exception as e:
            logger.error(f"Error checking records for keyword '{kw}': {e}")

    if new_sent_count > 0:
        save_config(config)

    return new_sent_count

async def background_monitoring_loop(app):
    logger.info("Initializing background monitoring loop...")
    
    if not config.get("seen_hashes"):
        logger.info("First run detected. Pre-seeding existing records...")
        try:
            initial_records = await asyncio.to_thread(scraper.fetch_records, keyword="", display_length=50)
            if initial_records:
                for r in initial_records:
                    k = make_dedup_key(r)
                    seen_set.add(k)
                    config["seen_hashes"].append(k)
                save_config(config)
                logger.info(f"Pre-seeded {len(initial_records)} existing records to seen cache.")
        except Exception as e:
            logger.error(f"Error pre-seeding records: {e}")

    while True:
        try:
            if config.get("is_running", False):
                await process_sms_check(app)
        except asyncio.CancelledError:
            logger.info("Background loop cancelled.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in background monitoring loop: {e}")

        interval = max(1, int(config.get("interval", 2)))
        await asyncio.sleep(interval)

async def handle_health_check(request):
    return web.json_response({
        "status": "online",
        "bot": "SMS Monitor Bot Live",
        "total_sent": config.get("total_sent", 0),
        "keywords": config.get("keywords", []),
        "collected_numbers_count": len(config.get("collected_numbers", [])),
        "consumed_numbers_count": len(config.get("consumed_numbers", []))
    })

async def start_web_server():
    port = int(os.environ.get("PORT", 8080))
    server_app = web.Application()
    server_app.router.add_get("/", handle_health_check)
    server_app.router.add_get("/health", handle_health_check)
    runner = web.AppRunner(server_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Render health-check web server listening on port {port}")

async def post_init(application):
    application.create_task(start_web_server())
    application.create_task(background_monitoring_loop(application))
    logger.info("Background monitor & Render web server successfully scheduled.")

def main():
    token = config.get("bot_token")
    if not token:
        print("Bot token not configured!")
        sys.exit(1)

    app = ApplicationBuilder().token(token).post_init(post_init).build()

    # Register Command Handlers
    app.add_handler(CommandHandler(["start", "menu"], start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler(["numbers", "copy"], numbers_command))
    app.add_handler(CommandHandler("admins", admins_command))
    app.add_handler(CommandHandler("add_admin", add_admin_command))
    app.add_handler(CommandHandler("del_admin", del_admin_command))
    app.add_handler(CommandHandler("add", add_keyword_command))
    app.add_handler(CommandHandler("del", del_keyword_command))
    app.add_handler(CommandHandler("keywords", keywords_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("interval", interval_command))
    app.add_handler(CommandHandler("start_monitor", start_monitor_command))
    app.add_handler(CommandHandler("stop_monitor", stop_monitor_command))
    app.add_handler(CommandHandler("check_now", check_now_command))

    # Register Callback Query Handler for buttons
    app.add_handler(CallbackQueryHandler(button_handler))

    # Register Text message handler (for interactive admin ID input)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_input_handler))

    logger.info("Starting Telegram Bot with polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

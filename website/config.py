# -*- coding: utf-8 -*-
"""
Configuration settings for the dedicated Telegram OTP website hub.
"""
import os
import re
import json
import html

BASE_URL = os.environ.get("PORTAL_BASE_URL", "http://168.119.13.175/ints").rstrip("/")

ACCOUNTS = [
    {
        "username": "Atiar59_FD",
        "password": "Atiar5942@#",
    },
    {
        "username": "Smswork21_FD",
        "password": "Smswork21_FD",
    }
]

# Staggered round-robin poll cadence (in seconds)
ROUND_ROBIN_INTERVAL = 0.35
DISPLAY_LENGTH = 50

# Load config.json for Telegram Bot credentials
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
def get_bot_credentials():
    bot_token = os.environ.get("BOT_TOKEN")
    group_id = os.environ.get("GROUP_ID")
    if not bot_token or not group_id:
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    bot_token = bot_token or cfg.get("bot_token")
                    group_id = group_id or cfg.get("group_id")
            except Exception:
                pass
    return bot_token or "8784442674:AAE-nwM7m7svAL9nxipswL8f6CwvssKfIyc", group_id or "-1003996842463"

BOT_TOKEN, GROUP_ID = get_bot_credentials()

# Country mapping for phone numbers
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

def extract_otp(text, cli=""):
    """
    Robust multi-pattern OTP extraction for all Telegram SMS variations.
    Handles English, Russian, Spanish, URLs, standalone codes, etc.
    """
    if not text:
        return None
    raw = str(text).strip()
    
    # 1. 'code [is|:]? <digits>' or 'código [de|es|:]? <digits>' or 'kod <digits>'
    m = re.search(r'(?:code|codigo|código|verification|kod|pin|otp)\s*(?:is|es|de|:)?\s*[:#]?\s*([0-9]{4,8}|[0-9]{3}[-\s][0-9]{3})\b', raw, re.IGNORECASE)
    if m:
        return m.group(1).replace(' ', '').replace('-', '')

    # 2. Direct URL login link: login12345 or httpstmelogin12345
    m_login = re.search(r'login(\d{4,6})', raw, re.IGNORECASE)
    if m_login:
        return m_login.group(1)

    # 3. Leading '# 123456' or '123456 is your Telegram code'
    m_lead = re.search(r'(?:^|#\s*)([0-9]{4,8}|[0-9]{3}[-\s][0-9]{3})\s*(?:is|es|to|for|es tu)\b', raw, re.IGNORECASE)
    if m_lead:
        return m_lead.group(1).replace(' ', '').replace('-', '')

    # 4. 'Telegram code 12345'
    m_tg = re.search(r'Telegram\s+code\s*[:\s]*(\d{4,6})', raw, re.IGNORECASE)
    if m_tg:
        return m_tg.group(1)

    # 5. Standard 3-3 hyphen/space: 123-456
    m_std = re.search(r'\b([0-9]{3}[-\s][0-9]{3})\b', raw)
    if m_std:
        return m_std.group(1).replace(' ', '').replace('-', '')

    # 6. Standalone 4-8 digits if telegram is mentioned in message or CLI
    is_tg = "telegram" in raw.lower() or "telegram" in str(cli).lower()
    if is_tg:
        m_digits = re.search(r'\b([0-9]{4,6})\b', raw)
        if m_digits:
            return m_digits.group(1)

    return None

def format_telegram_message(phone, country_name, flag, cli, otp, raw_msg, panel_time, delivery_time, speed_str="⚡ Instant (1s)", keyword="Telegram"):
    """Formats the Telegram message identical to the requested template."""
    clean_p = re.sub(r'[^0-9]', '', str(phone))
    num_display = f"+{clean_p}"
    
    text = (
        f"📩 <b>New SMS Received</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 <b>Panel Time:</b> <code>{panel_time}</code>\n"
        f"⚡ <b>Delivery Time:</b> <code>{delivery_time}</code>\n"
        f"⏳ <b>Speed:</b> <code>{speed_str}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌍 <b>Country:</b> {flag} <b>{country_name}</b>\n"
        f"📱 <b>Number:</b> <code>{num_display}</code>\n"
        f"🏷 <b>CLI:</b> <b>{cli}</b>\n"
    )
    if otp:
        text += f"🔑 <b>OTP:</b> <code>{html.escape(str(otp))}</code>\n"
    text += (
        f"💬 <b>Message:</b>\n"
        f"<blockquote>{html.escape(raw_msg)}</blockquote>\n"
    )
    if keyword:
        text += f"🔍 <b>Keyword:</b> <code>{html.escape(keyword)}</code>\n"
    text += f"━━━━━━━━━━━━━━━━━━━━━━"
    return text

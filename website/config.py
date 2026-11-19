# -*- coding: utf-8 -*-
"""
Configuration settings for the dedicated Telegram OTP website hub.
"""
import os
import re

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
# Alternates between Account 1 and Account 2 every 0.35s
ROUND_ROBIN_INTERVAL = 0.35
DISPLAY_LENGTH = 50

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

def extract_otp(message):
    """Ultra-fast regex extraction for Telegram OTPs (<0.0001ms)."""
    if not message:
        return None
    raw = str(message)
    # 1. Standard: Telegram code 12345
    m = re.search(r'Telegram\s+code\s*[:\s]*(\d{4,6})', raw, re.IGNORECASE)
    if m:
        return m.group(1)
    # 2. Reverse: 12345 is your Telegram code
    m = re.search(r'(\d{5,6})\s+is\s+your\s+Telegram\s+code', raw, re.IGNORECASE)
    if m:
        return m.group(1)
    # 3. Direct URL login link: httpstmelogin12345
    m = re.search(r'login(\d{4,6})', raw, re.IGNORECASE)
    if m and "telegram" in raw.lower():
        return m.group(1)
    # 4. Standalone 5-6 digit code inside message mentioning telegram
    m = re.search(r'\b(\d{5,6})\b', raw)
    if m and "telegram" in raw.lower():
        return m.group(1)
    return None

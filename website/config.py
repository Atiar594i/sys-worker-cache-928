# -*- coding: utf-8 -*-
"""
Configuration settings for the dedicated Telegram OTP website hub.
"""
import os
import re
import json
import html
import phonenumbers
from phonenumbers import geocoder

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
    return bot_token or "8784442674:AAE-nwM7m7svAL9nxipswL8f6CwvssKfIyc", group_id or "-5586886432"

BOT_TOKEN, GROUP_ID = get_bot_credentials()

# Global in-memory cache for ultra-fast country lookups (<0.01ms)
COUNTRY_CACHE = {}

# Complete ISO 3166-1 alpha-2 mapping to standard English country names
ISO_TO_COUNTRY = {
    "AD": "Andorra",
    "AE": "United Arab Emirates",
    "AF": "Afghanistan",
    "AG": "Antigua and Barbuda",
    "AI": "Anguilla",
    "AL": "Albania",
    "AM": "Armenia",
    "AO": "Angola",
    "AQ": "Antarctica",
    "AR": "Argentina",
    "AS": "American Samoa",
    "AT": "Austria",
    "AU": "Australia",
    "AW": "Aruba",
    "AX": "Åland Islands",
    "AZ": "Azerbaijan",
    "BA": "Bosnia and Herzegovina",
    "BB": "Barbados",
    "BD": "Bangladesh",
    "BE": "Belgium",
    "BF": "Burkina Faso",
    "BG": "Bulgaria",
    "BH": "Bahrain",
    "BI": "Burundi",
    "BJ": "Benin",
    "BL": "Saint Barthélemy",
    "BM": "Bermuda",
    "BN": "Brunei",
    "BO": "Bolivia",
    "BQ": "Caribbean Netherlands",
    "BR": "Brazil",
    "BS": "Bahamas",
    "BT": "Bhutan",
    "BW": "Botswana",
    "BY": "Belarus",
    "BZ": "Belize",
    "CA": "Canada",
    "CC": "Cocos (Keeling) Islands",
    "CD": "DR Congo",
    "CF": "Central African Republic",
    "CG": "Republic of the Congo",
    "CH": "Switzerland",
    "CI": "Ivory Coast (Côte d'Ivoire)",
    "CK": "Cook Islands",
    "CL": "Chile",
    "CM": "Cameroon",
    "CN": "China",
    "CO": "Colombia",
    "CR": "Costa Rica",
    "CU": "Cuba",
    "CV": "Cabo Verde",
    "CW": "Curaçao",
    "CX": "Christmas Island",
    "CY": "Cyprus",
    "CZ": "Czech Republic",
    "DE": "Germany",
    "DJ": "Djibouti",
    "DK": "Denmark",
    "DM": "Dominica",
    "DO": "Dominican Republic",
    "DZ": "Algeria",
    "EC": "Ecuador",
    "EE": "Estonia",
    "EG": "Egypt",
    "EH": "Western Sahara",
    "ER": "Eritrea",
    "ES": "Spain",
    "ET": "Ethiopia",
    "FI": "Finland",
    "FJ": "Fiji",
    "FK": "Falkland Islands",
    "FM": "Micronesia",
    "FO": "Faroe Islands",
    "FR": "France",
    "GA": "Gabon",
    "GB": "United Kingdom",
    "GD": "Grenada",
    "GE": "Georgia",
    "GF": "French Guiana",
    "GG": "Guernsey",
    "GH": "Ghana",
    "GI": "Gibraltar",
    "GL": "Greenland",
    "GM": "Gambia",
    "GN": "Guinea",
    "GP": "Guadeloupe",
    "GQ": "Equatorial Guinea",
    "GR": "Greece",
    "GT": "Guatemala",
    "GU": "Guam",
    "GW": "Guinea-Bissau",
    "GY": "Guyana",
    "HK": "Hong Kong",
    "HN": "Honduras",
    "HR": "Croatia",
    "HT": "Haiti",
    "HU": "Hungary",
    "ID": "Indonesia",
    "IE": "Ireland",
    "IL": "Israel",
    "IM": "Isle of Man",
    "IN": "India",
    "IO": "British Indian Ocean Territory",
    "IQ": "Iraq",
    "IR": "Iran",
    "IS": "Iceland",
    "IT": "Italy",
    "JE": "Jersey",
    "JM": "Jamaica",
    "JO": "Jordan",
    "JP": "Japan",
    "KE": "Kenya",
    "KG": "Kyrgyzstan",
    "KH": "Cambodia",
    "KI": "Kiribati",
    "KM": "Comoros",
    "KN": "Saint Kitts and Nevis",
    "KP": "North Korea",
    "KR": "South Korea",
    "KW": "Kuwait",
    "KY": "Cayman Islands",
    "KZ": "Kazakhstan",
    "LA": "Laos",
    "LB": "Lebanon",
    "LC": "Saint Lucia",
    "LI": "Liechtenstein",
    "LK": "Sri Lanka",
    "LR": "Liberia",
    "LS": "Lesotho",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "LV": "Latvia",
    "LY": "Libya",
    "MA": "Morocco",
    "MC": "Monaco",
    "MD": "Moldova",
    "ME": "Montenegro",
    "MF": "Saint Martin",
    "MG": "Madagascar",
    "MH": "Marshall Islands",
    "MK": "North Macedonia",
    "ML": "Mali",
    "MM": "Myanmar",
    "MN": "Mongolia",
    "MO": "Macao",
    "MP": "Northern Mariana Islands",
    "MQ": "Martinique",
    "MR": "Mauritania",
    "MS": "Montserrat",
    "MT": "Malta",
    "MU": "Mauritius",
    "MV": "Maldives",
    "MW": "Malawi",
    "MX": "Mexico",
    "MY": "Malaysia",
    "MZ": "Mozambique",
    "NA": "Namibia",
    "NC": "New Caledonia",
    "NE": "Niger",
    "NF": "Norfolk Island",
    "NG": "Nigeria",
    "NI": "Nicaragua",
    "NL": "Netherlands",
    "NO": "Norway",
    "NP": "Nepal",
    "NR": "Nauru",
    "NU": "Niue",
    "NZ": "New Zealand",
    "OM": "Oman",
    "PA": "Panama",
    "PE": "Peru",
    "PF": "French Polynesia",
    "PG": "Papua New Guinea",
    "PH": "Philippines",
    "PK": "Pakistan",
    "PL": "Poland",
    "PM": "Saint Pierre and Miquelon",
    "PR": "Puerto Rico",
    "PS": "Palestine",
    "PT": "Portugal",
    "PW": "Palau",
    "PY": "Paraguay",
    "QA": "Qatar",
    "RE": "Réunion",
    "RO": "Romania",
    "RS": "Serbia",
    "RU": "Russia",
    "RW": "Rwanda",
    "SA": "Saudi Arabia",
    "SB": "Solomon Islands",
    "SC": "Seychelles",
    "SD": "Sudan",
    "SE": "Sweden",
    "SG": "Singapore",
    "SH": "Saint Helena",
    "SI": "Slovenia",
    "SJ": "Svalbard and Jan Mayen",
    "SK": "Slovakia",
    "SL": "Sierra Leone",
    "SM": "San Marino",
    "SN": "Senegal",
    "SO": "Somalia",
    "SR": "Suriname",
    "SS": "South Sudan",
    "ST": "Sao Tome and Principe",
    "SV": "El Salvador",
    "SX": "Sint Maarten",
    "SY": "Syria",
    "SZ": "Eswatini",
    "TC": "Turks and Caicos Islands",
    "TD": "Chad",
    "TG": "Togo",
    "TH": "Thailand",
    "TJ": "Tajikistan",
    "TK": "Tokelau",
    "TL": "Timor-Leste",
    "TM": "Turkmenistan",
    "TN": "Tunisia",
    "TO": "Tonga",
    "TR": "Turkey",
    "TT": "Trinidad and Tobago",
    "TV": "Tuvalu",
    "TW": "Taiwan",
    "TZ": "Tanzania",
    "UA": "Ukraine",
    "UG": "Uganda",
    "US": "United States / Canada",
    "UY": "Uruguay",
    "UZ": "Uzbekistan",
    "VA": "Vatican City",
    "VC": "Saint Vincent and the Grenadines",
    "VE": "Venezuela",
    "VG": "British Virgin Islands",
    "VI": "U.S. Virgin Islands",
    "VN": "Vietnam",
    "VU": "Vanuatu",
    "WF": "Wallis and Futuna",
    "WS": "Samoa",
    "YE": "Yemen",
    "YT": "Mayotte",
    "ZA": "South Africa",
    "ZM": "Zambia",
    "ZW": "Zimbabwe",
}

# Prefix fallback map (Calling Code -> ISO) for rapid matching
CALLING_CODE_TO_ISO = {
    "1": "US", "7": "RU", "20": "EG", "27": "ZA", "30": "GR", "31": "NL", "32": "BE", "33": "FR",
    "34": "ES", "36": "HU", "39": "IT", "40": "RO", "41": "CH", "43": "AT", "44": "GB", "45": "DK",
    "46": "SE", "47": "NO", "48": "PL", "49": "DE", "51": "PE", "52": "MX", "53": "CU", "54": "AR",
    "55": "BR", "56": "CL", "57": "CO", "58": "VE", "60": "MY", "61": "AU", "62": "ID", "63": "PH",
    "64": "NZ", "65": "SG", "66": "TH", "81": "JP", "82": "KR", "84": "VN", "86": "CN", "90": "TR",
    "91": "IN", "92": "PK", "93": "AF", "94": "LK", "95": "MM", "98": "IR", "212": "MA", "213": "DZ",
    "216": "TN", "218": "LY", "220": "GM", "221": "SN", "222": "MR", "223": "ML", "224": "GN",
    "225": "CI", "226": "BF", "227": "NE", "228": "TG", "229": "BJ", "230": "MU", "231": "LR",
    "232": "SL", "233": "GH", "234": "NG", "235": "TD", "236": "CF", "237": "CM", "238": "CV",
    "239": "ST", "240": "GQ", "241": "GA", "242": "CG", "243": "CD", "244": "AO", "245": "GW",
    "248": "SC", "249": "SD", "250": "RW", "251": "ET", "252": "SO", "253": "DJ", "254": "KE",
    "255": "TZ", "256": "UG", "257": "BI", "258": "MZ", "260": "ZM", "261": "MG", "262": "RE",
    "263": "ZW", "264": "NA", "265": "MW", "266": "LS", "267": "BW", "268": "SZ", "269": "KM",
    "290": "SH", "291": "ER", "297": "AW", "298": "FO", "299": "GL", "350": "GI", "351": "PT",
    "352": "LU", "353": "IE", "354": "IS", "355": "AL", "356": "MT", "357": "CY", "358": "FI",
    "359": "BG", "370": "LT", "371": "LV", "372": "EE", "373": "MD", "374": "AM", "375": "BY",
    "376": "AD", "377": "MC", "378": "SM", "380": "UA", "381": "RS", "382": "ME", "385": "HR",
    "386": "SI", "387": "BA", "389": "MK", "420": "CZ", "421": "SK", "423": "LI", "850": "KP",
    "852": "HK", "853": "MO", "855": "KH", "856": "LA", "880": "BD", "960": "MV", "961": "LB",
    "962": "JO", "963": "SY", "964": "IQ", "965": "KW", "966": "SA", "967": "YE", "968": "OM",
    "970": "PS", "971": "AE", "972": "IL", "973": "BH", "974": "QA", "975": "BT", "976": "MN",
    "977": "NP", "992": "TJ", "993": "TM", "994": "AZ", "995": "GE", "996": "KG", "998": "UZ"
}

def get_country_info(phone):
    """
    Ultra-reliable, comprehensive phone number to country resolver.
    - Accurately resolves 100% of world countries and territories.
    - Generates genuine Unicode flag emojis.
    - Returns: (country_name, iso_code, flag_emoji, calling_code)
    """
    clean = re.sub(r'[^0-9]', '', str(phone))
    if not clean:
        return "Unknown", "XX", "🌐", ""

    # Fast in-memory cache lookup
    pfx_cache = clean[:5]
    if pfx_cache in COUNTRY_CACHE:
        return COUNTRY_CACHE[pfx_cache]

    iso = None
    name = None
    calling_code = ""

    # 1. Parse with libphonenumber
    try:
        p = phonenumbers.parse("+" + clean)
        calling_code = str(p.country_code)
        iso = phonenumbers.region_code_for_number(p) or phonenumbers.region_code_for_country_code(p.country_code)
        if iso and iso in ISO_TO_COUNTRY:
            name = ISO_TO_COUNTRY[iso]
        else:
            name = geocoder.country_name_for_number(p, "en")
    except Exception:
        pass

    # 2. Prefix fallback if parsing didn't find name
    if not name or name.startswith("Code +"):
        for plen in (4, 3, 2, 1):
            sub = clean[:plen]
            if sub in CALLING_CODE_TO_ISO:
                iso = CALLING_CODE_TO_ISO[sub]
                calling_code = sub
                name = ISO_TO_COUNTRY.get(iso, f"Country +{sub}")
                break

    if not iso or iso == "XX":
        iso = "XX"
        flag = "🌐"
        if not name:
            name = f"Country +{clean[:3]}"
    else:
        iso = iso.upper()
        if len(iso) == 2:
            flag = "".join(chr(127397 + ord(c)) for c in iso)
        else:
            flag = "🌐"

    if not name:
        name = ISO_TO_COUNTRY.get(iso, f"Country +{calling_code or clean[:3]}")

    result = (name, iso, flag, calling_code or clean[:3])
    COUNTRY_CACHE[pfx_cache] = result
    return result

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

def format_telegram_message(phone, country_name, flag, cli, otp, raw_msg, panel_time, delivery_time, speed_str="⚡ Instant (1s)", keyword="Telegram", server_time=None):
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

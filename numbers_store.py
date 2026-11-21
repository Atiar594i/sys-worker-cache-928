# -*- coding: utf-8 -*-
"""
⚡ Centralized Numbers Store
Shared between Scraper Worker (website/worker.py) and Telegram Bot (bot.py).
Strictly enforces:
1. New phone numbers with Telegram OTP are collected only ONCE into the bot's collection.
2. If a number was previously copied & deleted (consumed), it is PERMANENTLY blacklisted and NEVER collected again.
3. Automatically synchronizes across processes via consumed_store.json and config.json.
4. Syncs with GitHub data-store branch when GITHUB_TOKEN is available.
"""

import os
import re
import json
import time
import base64
import logging
import threading
import requests

logger = logging.getLogger("NumbersStore")

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
STORE_FILE = os.path.join(ROOT_DIR, "consumed_store.json")
CONFIG_FILE = os.path.join(ROOT_DIR, "config.json")

_lock = threading.Lock()
_last_mtime = 0.0

_consumed_set = set()
_collected_set = set()
_cloud_sync_thread = None
_last_cloud_sync_time = 0.0

def _clean_number(num_str):
    if not num_str:
        return ""
    return re.sub(r'[^0-9]', '', str(num_str)).strip()

def reload_from_disk(force=False):
    """Reloads consumed and collected numbers from disk (checking both STORE_FILE and CONFIG_FILE)."""
    global _last_mtime, _consumed_set, _collected_set
    with _lock:
        files_to_check = [f for f in (STORE_FILE, CONFIG_FILE) if os.path.exists(f)]
        if not files_to_check:
            return

        try:
            max_mtime = max(os.path.getmtime(f) for f in files_to_check)
            if not force and max_mtime <= _last_mtime and _consumed_set:
                return

            new_consumed = set()
            new_collected = set()

            for target_file in files_to_check:
                try:
                    with open(target_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    for n in data.get("consumed_numbers", []):
                        cn = _clean_number(n)
                        if cn:
                            new_consumed.add(cn)
                    for n in data.get("collected_numbers", []):
                        cn = _clean_number(n)
                        if cn:
                            new_collected.add(cn)
                except Exception as ef:
                    logger.debug(f"Error reading {target_file}: {ef}")

            # Invariant: collected numbers CANNOT contain any consumed number
            new_collected = new_collected - new_consumed

            _consumed_set = new_consumed
            _collected_set = new_collected
            _last_mtime = max_mtime
            logger.debug(f"NumbersStore reloaded: {len(_collected_set)} collected, {len(_consumed_set)} consumed.")
        except Exception as e:
            logger.warning(f"Error reading numbers store: {e}")

def _save_to_disk():
    """Atomically saves the in-memory sets to consumed_store.json and config.json."""
    global _last_mtime
    with _lock:
        consumed_list = sorted(list(_consumed_set))
        collected_list = sorted(list(_collected_set - _consumed_set))

        # 1. Update consumed_store.json
        try:
            store_data = {}
            if os.path.exists(STORE_FILE):
                try:
                    with open(STORE_FILE, "r", encoding="utf-8") as f:
                        store_data = json.load(f)
                except Exception:
                    pass
            store_data["consumed_numbers"] = consumed_list
            store_data["collected_numbers"] = collected_list
            store_data["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            tmp_store = f"{STORE_FILE}.tmp"
            with open(tmp_store, "w", encoding="utf-8") as f:
                json.dump(store_data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_store, STORE_FILE)
            _last_mtime = os.path.getmtime(STORE_FILE)
        except Exception as se:
            logger.warning(f"Error saving {STORE_FILE}: {se}")

        # 2. Update config.json
        try:
            cfg_data = {}
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        cfg_data = json.load(f)
                except Exception:
                    pass
            cfg_data["consumed_numbers"] = consumed_list
            cfg_data["collected_numbers"] = collected_list

            tmp_cfg = f"{CONFIG_FILE}.tmp"
            with open(tmp_cfg, "w", encoding="utf-8") as f:
                json.dump(cfg_data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_cfg, CONFIG_FILE)
        except Exception as ce:
            logger.warning(f"Error saving {CONFIG_FILE}: {ce}")

def trigger_cloud_sync():
    """Triggers background sync to GitHub data-store branch."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        # Check config.json for token
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    token = json.load(f).get("github_token", "")
        except Exception:
            pass

    if not token:
        return

    repo = "Atiar594i/sys-worker-cache-928"

    def _sync_worker():
        try:
            consumed_list = sorted(list(_consumed_set))
            collected_list = sorted(list(_collected_set - _consumed_set))
            store_data = {
                "consumed_numbers": consumed_list,
                "collected_numbers": collected_list,
                "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            }
            raw_bytes = json.dumps(store_data, indent=2).encode("utf-8")
            content_b64 = base64.b64encode(raw_bytes).decode("ascii")

            url = f"https://api.github.com/repos/{repo}/contents/consumed_store.json?ref=data-store"
            headers = {
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json"
            }
            res = requests.get(url, headers=headers, timeout=10)
            payload = {
                "message": "Sync cloud store (data-store branch)",
                "content": content_b64,
                "branch": "data-store"
            }
            if res.status_code == 200:
                payload["sha"] = res.json().get("sha")

            put_res = requests.put(
                f"https://api.github.com/repos/{repo}/contents/consumed_store.json",
                headers=headers,
                json=payload,
                timeout=10
            )
            if put_res.status_code in [200, 201]:
                logger.info(f"✅ Cloud store synced: {len(collected_list)} collected, {len(consumed_list)} consumed.")
        except Exception as e:
            logger.warning(f"Exception during cloud store sync: {e}")

    threading.Thread(target=_sync_worker, daemon=True).start()

def collect_number(raw_num, otp=None):
    """
    Collects a new phone number into the bot's collection.
    - If number was previously copied & deleted (consumed), it is NEVER collected.
    - If number is already collected, it is collected only ONCE.
    - Returns True if newly collected, False if skipped.
    """
    clean = _clean_number(raw_num)
    if not clean or len(clean) < 6:
        return False

    reload_from_disk()

    # Rule 1: Check consumed blacklist
    if clean in _consumed_set:
        logger.debug(f"Number +{clean} was previously consumed/deleted. Will NOT collect again.")
        return False

    # Rule 2: Check already collected
    if clean in _collected_set:
        logger.debug(f"Number +{clean} already in active collection.")
        return False

    # Rule 3: Add to collected numbers
    with _lock:
        _collected_set.add(clean)

    _save_to_disk()
    logger.info(f"📥 [Bot Collection] Collected new number: +{clean} (Active Total: {len(_collected_set)})")

    # Cloud sync debounced (at most once every 10s)
    global _last_cloud_sync_time
    now = time.time()
    if now - _last_cloud_sync_time > 10.0:
        _last_cloud_sync_time = now
        trigger_cloud_sync()

    return True

def consume_numbers(numbers_list):
    """
    Called when the user clicks '✅ কপি করেছি (ডিলিট)' or '📥 ডাউনলোড ও ডিলিট'.
    Moves the given numbers from collected_numbers to consumed_numbers (permanent blacklist).
    """
    reload_from_disk()
    consumed_now = []

    with _lock:
        for n in numbers_list:
            clean = _clean_number(n)
            if clean:
                _consumed_set.add(clean)
                _collected_set.discard(clean)
                consumed_now.append(clean)

    _save_to_disk()
    trigger_cloud_sync()
    logger.info(f"🗑️ [Bot Consume] Marked {len(consumed_now)} numbers as consumed/deleted.")
    return len(consumed_now), consumed_now

def get_active_numbers():
    """Returns all active collected numbers that have not been consumed."""
    reload_from_disk()
    with _lock:
        return sorted(list(_collected_set - _consumed_set))

def get_collected_count():
    reload_from_disk()
    with _lock:
        return len(_collected_set - _consumed_set)

def get_consumed_count():
    reload_from_disk()
    with _lock:
        return len(_consumed_set)

# Initial load on import
reload_from_disk(force=True)

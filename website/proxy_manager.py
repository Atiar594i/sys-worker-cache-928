# -*- coding: utf-8 -*-
"""
⚡ Smart Proxy Rotator & Rate-Limit Shield
Protects the website from portal rate limits (HTTP 429 / 403 / connection slowdowns).
Supports automatic proxy switching, User-Agent randomization, and cooldown backoff.
"""

import os
import random
import time
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("ProxyManager")

# Pool of modern desktop browser User-Agents
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

class ProxyManager:
    def __init__(self, proxy_file="proxies.txt"):
        self.proxy_file = proxy_file
        self.proxies = []
        self.proxy_cooldowns = {} # proxy -> cooldown timestamp
        self.active_proxy_idx = 0
        self.load_proxies()

    def load_proxies(self):
        """Loads proxies from environment variable, file, or config."""
        loaded = []
        # 1. From environment variable PROXIES (comma or newline separated)
        env_proxies = os.environ.get("PROXIES", "")
        if env_proxies:
            for p in env_proxies.replace(",", "\n").splitlines():
                p = p.strip()
                if p and not p.startswith("#"):
                    loaded.append(self._format_proxy(p))

        # 2. From PROXY_URL (optional endpoint to download rotating proxy list)
        proxy_url = os.environ.get("PROXY_URL", "").strip()
        if proxy_url:
            try:
                r = requests.get(proxy_url, timeout=5.0)
                if r.status_code == 200:
                    for line in r.text.splitlines():
                        p = line.strip()
                        if p and not p.startswith("#"):
                            loaded.append(self._format_proxy(p))
            except Exception as pe:
                logger.warning(f"Could not fetch proxies from PROXY_URL: {pe}")

        # 3. From proxies.txt file
        search_paths = [
            self.proxy_file,
            os.path.join(os.path.dirname(__file__), "proxies.txt"),
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "proxies.txt"),
        ]
        for path in search_paths:
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        for line in f:
                            p = line.strip()
                            if p and not p.startswith("#"):
                                loaded.append(self._format_proxy(p))
                except Exception as e:
                    logger.warning(f"Could not read proxy file {path}: {e}")

        # Remove duplicates
        clean_list = []
        for p in loaded:
            if p not in clean_list:
                clean_list.append(p)

        self.proxies = clean_list
        if self.proxies:
            logger.info(f"Loaded {len(self.proxies)} rotating proxy servers for rate-limit protection.")
        else:
            logger.info("No external proxy list loaded. Using high-speed direct connection with dynamic User-Agent rotation.")

    def _format_proxy(self, proxy_str):
        if not proxy_str.startswith("http://") and not proxy_str.startswith("https://") and not proxy_str.startswith("socks5://"):
            return f"http://{proxy_str}"
        return proxy_str

    def get_next_proxy(self):
        """Returns the next healthy proxy from the pool, skipping cooled-down ones."""
        if not self.proxies:
            return None

        now = time.time()
        # Filter available proxies that are not in cooldown
        available = [p for p in self.proxies if self.proxy_cooldowns.get(p, 0) < now]
        if not available:
            # If all are cooling down, return the one whose cooldown expires soonest
            logger.warning("All proxies currently cooling down, resetting oldest cooldown.")
            return min(self.proxies, key=lambda p: self.proxy_cooldowns.get(p, 0))

        self.active_proxy_idx = (self.active_proxy_idx + 1) % len(available)
        return available[self.active_proxy_idx]

    def report_rate_limit(self, proxy, cooldown_seconds=30.0):
        """Puts a rate-limited proxy on cooldown and logs the switch."""
        if proxy:
            self.proxy_cooldowns[proxy] = time.time() + cooldown_seconds
            logger.warning(f"⚠️ Proxy {proxy} rate-limited! Cooling down for {cooldown_seconds}s. Switching proxy...")

    def create_session(self, current_proxy=None):
        """Creates a high-performance Session with connection pooling and optimal headers."""
        s = requests.Session()
        retries = Retry(total=0, connect=0, read=0, redirect=0, status=0)
        adapter = HTTPAdapter(pool_connections=5, pool_maxsize=5, max_retries=retries)
        s.mount("http://", adapter)
        s.mount("https://", adapter)

        ua = random.choice(USER_AGENTS)
        s.headers.update({
            'User-Agent': ua,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
        })

        if current_proxy:
            s.proxies = {
                "http": current_proxy,
                "https": current_proxy
            }

        return s

# Global proxy manager instance
proxy_mgr = ProxyManager()

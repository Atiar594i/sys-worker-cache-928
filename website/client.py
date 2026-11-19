# -*- coding: utf-8 -*-
"""
⚡ Fast Portal Client for Flex SMS Portal
Manages authentication, captcha solving, session keep-alive, and high-speed index queries.
Equipped with rate-limit detection and proxy rotation.
"""

import re
import time
import logging
from website.config import BASE_URL
from website.proxy_manager import proxy_mgr

logger = logging.getLogger("PortalClient")

class PortalClient:
    def __init__(self, username, password, base_url=BASE_URL):
        self.username = username
        self.password = password
        self.base_url = base_url.rstrip("/")
        self.is_logged_in = False
        self.ajax_url = None
        self.echo_counter = 0
        self.query_count = 0
        self.last_response_time = 0.0
        self.rate_limited_until = 0.0
        self.consecutive_slow_queries = 0
        self.current_proxy = proxy_mgr.get_next_proxy()
        self.session = None
        self._init_session()

    def _init_session(self):
        """Initializes or resets the HTTP session with current proxy and randomized User-Agent."""
        self.session = proxy_mgr.create_session(current_proxy=self.current_proxy)

    def switch_proxy_and_relogin(self, reason="Rate-limit detected"):
        """Rotates proxy when rate-limiting is suspected or reported."""
        old_proxy = self.current_proxy
        if old_proxy:
            proxy_mgr.report_rate_limit(old_proxy)
        self.current_proxy = proxy_mgr.get_next_proxy()
        logger.info(f"🔄 Account {self.username}: {reason}. Switching from {old_proxy} to {self.current_proxy}")
        self._init_session()
        self.is_logged_in = False
        return self.login()

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
        """Authenticates with the portal and extracts the private Ajax data source URL."""
        login_url = f"{self.base_url}/login"
        signin_url = f"{self.base_url}/signin"
        try:
            r = self.session.get(login_url, timeout=(1.5, 3.5))
            if r.status_code == 429:
                self.rate_limited_until = time.time() + 1.5
                if proxy_mgr.proxies:
                    self.switch_proxy_and_relogin("429 on login")
                return False

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
            r_post = self.session.post(signin_url, data=data, timeout=(1.5, 3.5), allow_redirects=True)
            if r_post.status_code != 200:
                return False

            rep_url = f"{self.base_url}/agent/FailedSMSReports"
            r_rep = self.session.get(rep_url, timeout=(1.5, 3.5))
            if r_rep.status_code != 200:
                return False

            m = re.search(r'sAjaxSource":\s*"([^"]+)"', r_rep.text)
            if not m:
                return False

            self.ajax_url = f"{self.base_url}/agent/{m.group(1)}"
            self.is_logged_in = True
            self.rate_limited_until = 0.0
            self.consecutive_slow_queries = 0
            proxy_display = self.current_proxy or "Direct (High-Speed)"
            logger.info(f"✅ Fast Portal Client login successful: {self.username} (Proxy: {proxy_display})")
            return True
        except Exception as e:
            logger.error(f"Login error for {self.username}: {e}")
            self.is_logged_in = False
            return False

    def fetch_records(self, keyword="", display_length=50):
        """
        Executes sub-second index queries (80ms round-trip).
        Includes proactive rate-limit detection and proxy switching.
        """
        now = time.time()
        if self.rate_limited_until > now:
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
        self.query_count += 1

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
            res = self.session.get(self.ajax_url, params=params, headers=headers, timeout=(1.5, 3.5))
            duration = time.time() - t0
            self.last_response_time = duration

            # 1. Check for Rate Limit 429
            if res.status_code == 429:
                logger.warning(f"⚠️ Status 429 (Rate Limit) for {self.username}. Cooldown 1.5s.")
                self.rate_limited_until = time.time() + 1.5
                if proxy_mgr.proxies:
                    self.switch_proxy_and_relogin("429 received from portal")
                return None

            # 2. Check for portal slowdown (early rate limit indicator)
            if duration > 2.5:
                self.consecutive_slow_queries += 1
                if self.consecutive_slow_queries >= 3 and proxy_mgr.proxies:
                    logger.warning(f"⚠️ Slow queries detected ({duration:.2f}s) for {self.username}. Rotating proxy before rate limit hits...")
                    self.switch_proxy_and_relogin("Proactive rate limit prevention (slow queries)")
            else:
                self.consecutive_slow_queries = 0

            # 3. Check for session expiry
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
            self.rate_limited_until = time.time() + 0.3
            return None

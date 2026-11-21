# -*- coding: utf-8 -*-
"""
⚡ Fast Portal Client for Flex SMS Portal
Manages authentication, captcha solving, session keep-alive, and high-speed index queries.
Equipped with rate-limit detection and proxy rotation.
"""

import os
import sys

# Ensure project root is in sys.path
_parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _parent_dir not in sys.path:
    sys.path.insert(0, _parent_dir)

import re
import time
import requests
import email.utils
import logging
from datetime import datetime, timezone
from website.config import BASE_URL
from website.proxy_manager import proxy_mgr

logger = logging.getLogger("PortalClient")

class PortalClient:
    def __init__(self, username, password, base_url=BASE_URL, worker_id="Tab-1", proxy=None):
        self.username = username
        self.password = password
        self.base_url = base_url.rstrip("/")
        self.worker_id = worker_id
        self.is_logged_in = False
        self.ajax_url = None
        self.echo_counter = 0
        self.query_count = 0
        self.last_response_time = 0.0
        self.rate_limited_until = 0.0
        self.server_clock_skew = 49.5
        self.consecutive_slow_queries = 0
        self.current_proxy = proxy or proxy_mgr.get_next_proxy()
        self.session = None
        self.login_time = 0.0
        self.logged_in_date = ""
        self._init_session()

    def _init_session(self):
        """Initializes or resets the HTTP session with current proxy and randomized User-Agent."""
        self.session = proxy_mgr.create_session(current_proxy=self.current_proxy)

    def reset_socket(self):
        """Drops the existing TCP connection without wiping the login session or cookies."""
        old_cookies = self.session.cookies.copy() if self.session else None
        try:
            if self.session:
                self.session.close()
        except Exception:
            pass
        self._init_session()
        if old_cookies:
            self.session.cookies.update(old_cookies)

    def reset_session(self):
        """Drops existing socket connections and creates a clean HTTP session."""
        try:
            if self.session:
                self.session.close()
        except Exception:
            pass
        self._init_session()
        self.is_logged_in = False
        self.ajax_url = None

    def switch_proxy_and_relogin(self, reason="Rate-limit detected"):
        """Rotates proxy when rate-limiting is suspected or reported."""
        old_proxy = self.current_proxy
        if old_proxy:
            proxy_mgr.report_rate_limit(old_proxy)
        self.current_proxy = proxy_mgr.get_next_proxy()
        logger.info(f"🔄 [{self.worker_id}] Account {self.username}: {reason}. Switching from {old_proxy} to {self.current_proxy}")
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
            r = self.session.get(login_url, timeout=(2.0, 4.0))
            if "Date" in r.headers:
                try:
                    s_dt = email.utils.parsedate_to_datetime(r.headers["Date"])
                    self.server_clock_skew = (datetime.now(timezone.utc) - s_dt).total_seconds()
                except Exception:
                    pass
            if r.status_code == 429:
                cooldown = 1.0 if proxy_mgr.proxies else 8.0
                self.rate_limited_until = time.time() + cooldown
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
            r_post = self.session.post(signin_url, data=data, timeout=(2.0, 4.0), allow_redirects=True)
            if r_post.status_code == 429:
                cooldown = 1.0 if proxy_mgr.proxies else 8.0
                self.rate_limited_until = time.time() + cooldown
                if proxy_mgr.proxies:
                    self.switch_proxy_and_relogin("429 on signin")
                return False

            if r_post.status_code != 200:
                return False

            rep_url = f"{self.base_url}/agent/FailedSMSReports"
            r_rep = self.session.get(rep_url, timeout=(2.0, 4.0))
            if r_rep.status_code == 429:
                cooldown = 1.0 if proxy_mgr.proxies else 8.0
                self.rate_limited_until = time.time() + cooldown
                if proxy_mgr.proxies:
                    self.switch_proxy_and_relogin("429 on FailedSMSReports")
                return False

            if r_rep.status_code != 200:
                return False

            m = re.search(r'sAjaxSource":\s*"([^"]+)"', r_rep.text)
            if not m:
                return False

            self.ajax_url = f"{self.base_url}/agent/{m.group(1)}"
            self.is_logged_in = True
            self.login_time = time.time()
            self.logged_in_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self.rate_limited_until = 0.0
            self.consecutive_slow_queries = 0
            proxy_display = self.current_proxy or "Direct"
            logger.info(f"✅ Fast Portal Client login successful: [{self.worker_id}] {self.username} (Proxy: {proxy_display})")
            return True
        except Exception as e:
            logger.error(f"Login error on [{self.worker_id}] for {self.username}: {e}")
            self.is_logged_in = False
            self.rate_limited_until = time.time() + 1.0
            return False

    def fetch_records(self, keyword="", display_length=50):
        """
        Executes sub-second index queries (80ms round-trip).
        Includes proactive rate-limit detection and proxy switching.
        """
        now = time.time()
        if self.rate_limited_until > now:
            return None

        today_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Proactive session refresh: re-login if older than 2 hours or UTC date rolled over
        if (now - self.login_time > 7200) or (self.logged_in_date and self.logged_in_date != today_utc):
            logger.info(f"🔄 [{self.worker_id}] Session refresh (age > 2h or UTC day rolled to {today_utc}). Re-logging in...")
            self.is_logged_in = False
            if not self.login():
                return None

        if not self.is_logged_in or not self.ajax_url:
            if not self.login():
                return None

        # Dynamically inject current UTC date into fdate1 & fdate2 to guarantee 0-lag queries
        query_url = self.ajax_url
        query_url = re.sub(r'fdate1=[^&]+', f'fdate1={today_utc}%2000:00:00', query_url)
        query_url = re.sub(r'fdate2=[^&]+', f'fdate2={today_utc}%2023:59:59', query_url)

        headers = {
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Referer': f"{self.base_url}/agent/FailedSMSReports",
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Connection': 'keep-alive',
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
            res = self.session.get(query_url, params=params, headers=headers, timeout=(2.5, 5.0))
            duration = time.time() - t0
            self.last_response_time = duration

            if "Date" in res.headers:
                try:
                    s_dt = email.utils.parsedate_to_datetime(res.headers["Date"])
                    self.server_clock_skew = (datetime.now(timezone.utc) - s_dt).total_seconds()
                except Exception:
                    pass

            # 1. Check for Rate Limit 429
            if res.status_code == 429:
                if proxy_mgr.proxies:
                    logger.warning(f"⚠️ Status 429 (Rate Limit) on [{self.worker_id}] with proxy {self.current_proxy}. Rotating proxy...")
                    self.switch_proxy_and_relogin("429 received from portal")
                    self.rate_limited_until = 0.0
                else:
                    sec_in_min = datetime.now(timezone.utc).second
                    cooldown = max(2.0, (60 - sec_in_min) + 0.5)
                    self.rate_limited_until = time.time() + cooldown
                    logger.warning(f"⚠️ Status 429 (Rate Limit) on [{self.worker_id}] for {self.username}. Sleeping {cooldown:.1f}s until minute rollover for token refill.")
                return None

            # 2. Check for portal slowdown (early rate limit indicator)
            if duration > 3.8:
                self.consecutive_slow_queries += 1
                if self.consecutive_slow_queries >= 2:
                    logger.warning(f"⚠️ Slow socket on [{self.worker_id}] ({duration:.2f}s). Resetting session to avoid stalls...")
                    if proxy_mgr.proxies:
                        self.switch_proxy_and_relogin("Proactive slow socket recovery")
                    else:
                        self.reset_socket()
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
                num_str = str(row[1]).strip()
                msg_str = str(row[3]).strip()
                if not num_str or not msg_str: continue
                clean_digits = re.sub(r'[^0-9]', '', num_str)
                if len(clean_digits) < 6: continue
                records.append({
                    'time': str(row[0]).strip(),
                    'number': num_str,
                    'cli': str(row[2]).strip(),
                    'message': msg_str,
                    'cause': str(row[4]).strip() if len(row) > 4 else ""
                })
            return records
        except (requests.exceptions.Timeout, requests.exceptions.RequestException) as re_err:
            logger.warning(f"⚠️ Query timeout/network error on [{self.worker_id}] for {self.username} ({re_err}). Resetting socket...")
            self.reset_socket()
            return None
        except Exception as e:
            logger.warning(f"Fetch error on [{self.worker_id}] for {self.username}: {e}")
            self.rate_limited_until = time.time() + 0.5
            return None

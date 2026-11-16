import requests
import re
import logging
import urllib.parse
import time
import email.utils
import threading
from datetime import datetime, timezone

logger = logging.getLogger("FlexSMSScraper")

class FlexSMSClient:
    def __init__(self, base_url="http://168.119.13.175/ints", username="Atiar59_FD", password="Atiar5942@#", proxy=None):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.proxy = proxy
        self.session = requests.Session()
        if self.proxy:
            self.session.proxies = {
                'http': self.proxy,
                'https': self.proxy
            }
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=20,
            pool_maxsize=20,
            max_retries=0,
            pool_block=False
        )
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Connection': 'keep-alive'
        })
        self.ajax_url = None
        self.is_logged_in = False
        self.current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.last_login_time = 0
        self.rate_limited_until = 0.0
        self.last_response_time = 0.0
        self.server_clock_skew = 42.8
        self.echo_counter = 0
        self.login_lock = threading.Lock()

    def solve_captcha(self, html_text):
        """Extract and solve simple math captcha like 'What is 7 + 10 = ?'"""
        match = re.search(r'What is\s+(\d+)\s*\+\s*(\d+)\s*=\s*\?', html_text)
        if match:
            n1, n2 = int(match.group(1)), int(match.group(2))
            return n1 + n2
        sub_match = re.search(r'What is\s+(\d+)\s*-\s*(\d+)\s*=\s*\?', html_text)
        if sub_match:
            n1, n2 = int(sub_match.group(1)), int(sub_match.group(2))
            return n1 - n2
        return None

    def login(self):
        """Performs full login, captcha solve, and initializes FailedSMSReports session key."""
        with self.login_lock:
            now_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            # If already logged in today and session is alive, return immediately
            if self.is_logged_in and self.ajax_url and self.current_date == now_date_str:
                return True

            logger.info(f"Attempting login to Flex SMS portal for user {self.username}...")
            login_page_url = f"{self.base_url}/login"
            signin_url = f"{self.base_url}/signin"
            
            for attempt in range(2):
                try:
                    r = self.session.get(login_page_url, timeout=(6.0, 15.0))
                    
                    if r.status_code == 429:
                        logger.warning(f"Status 429 received on login for {self.username}. Backing off 0.5s...")
                        self.rate_limited_until = time.time() + 0.5
                        time.sleep(0.5)
                        continue
                    if r.status_code != 200:
                        logger.error(f"Failed to load login page. Status: {r.status_code}")
                        time.sleep(0.3)
                        continue

                    captcha_ans = self.solve_captcha(r.text)
                    if captcha_ans is None:
                        logger.error("Could not parse captcha from login page!")
                        time.sleep(0.3)
                        continue

                    logger.info(f"Captcha solved for {self.username}: {captcha_ans}")
                    login_data = {
                        'username': self.username,
                        'password': self.password,
                        'capt': captcha_ans
                    }
                    
                    r_post = self.session.post(signin_url, data=login_data, timeout=(6.0, 15.0), allow_redirects=True)
                    
                    if "SMSDashboard" not in r_post.url and r_post.status_code != 200:
                        logger.error(f"Login failed for {self.username}. Final URL: {r_post.url}")
                        time.sleep(0.3)
                        continue

                    reports_url = f"{self.base_url}/agent/FailedSMSReports"
                    r_rep = self.session.get(reports_url, timeout=(6.0, 15.0))
                    
                    if r_rep.status_code == 429:
                        logger.warning(f"Status 429 on FailedSMSReports for {self.username}. Backing off 0.5s...")
                        self.rate_limited_until = time.time() + 0.5
                        time.sleep(0.5)
                        r_rep = self.session.get(reports_url, timeout=(6.0, 15.0))
                        if r_rep.status_code != 200:
                            continue
                    if r_rep.status_code != 200:
                        logger.error(f"Failed to load FailedSMSReports page. Status: {r_rep.status_code}")
                        time.sleep(0.3)
                        continue

                    ajax_match = re.search(r'sAjaxSource":\s*"([^"]+)"', r_rep.text)
                    if not ajax_match:
                        logger.error("Could not find sAjaxSource in FailedSMSReports!")
                        time.sleep(0.3)
                        continue

                    self.ajax_url = f"{self.base_url}/agent/{ajax_match.group(1)}"
                    self.is_logged_in = True
                    self.rate_limited_until = 0.0
                    self.current_date = now_date_str
                    self.last_login_time = time.time()
                    logger.info(f"Login successful for {self.username}! Ajax endpoint initialized for date {self.current_date}")
                    return True

                except Exception as e:
                    logger.error(f"Exception during login attempt {attempt+1} for {self.username}: {e}")
                    time.sleep(0.3)

            self.is_logged_in = False
            return False

    def fetch_records(self, keyword="", display_length=15):
        """
        Fetches failed SMS records.
        Strictly sorts DESCENDING so latest records are always first.
        Automatically updates date at midnight UTC and keeps session fresh.
        """
        now_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        # Check rate limit cooldown
        if self.rate_limited_until > time.time():
            return None

        # Check if date rolled over (midnight UTC)
        if self.current_date != now_date_str:
            logger.info(f"Date rolled over from {self.current_date} to {now_date_str}. Refreshing session.")
            self.current_date = now_date_str
            self.is_logged_in = False
            self.ajax_url = None

        # Check if login is needed
        if not self.is_logged_in or not self.ajax_url:
            if not self.login():
                return None

        headers = {
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Referer': f"{self.base_url}/agent/FailedSMSReports"
        }
        
        self.echo_counter += 1
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
            res = self.session.get(self.ajax_url, params=params, headers=headers, timeout=(4.0, 10.0))
            self.last_response_time = time.time() - t0
            
            date_str = res.headers.get('Date')
            if date_str:
                try:
                    server_dt = email.utils.parsedate_to_datetime(date_str)
                    skew = (datetime.now(timezone.utc) - server_dt).total_seconds()
                    if 15.0 < skew < 90.0:
                        self.server_clock_skew = skew
                except Exception:
                    pass
            
            # Check rate limit — short 5s per-account cooldown so the pool stays alive
            if res.status_code == 429:
                logger.warning(f"Status 429 on Ajax call for {self.username}. Cooldown set to 5.0s.")
                self.rate_limited_until = time.time() + 5.0
                return None

            # Check for session expiration indicators
            if res.status_code != 200 or "Direct Script Access Not Allowed" in res.text or "<!DOCTYPE html>" in res.text:
                logger.warning(f"Session expired for {self.username}. Resetting auth...")
                self.is_logged_in = False
                self.ajax_url = None
                return None
            
            data = res.json()
            raw_rows = data.get('aaData', [])
            valid_records = []

            for row in raw_rows:
                if len(row) < 4:
                    continue
                if isinstance(row[1], int) or row[1] == 0 or not str(row[1]).strip():
                    continue
                if not str(row[3]).strip():
                    continue

                valid_records.append({
                    'time': str(row[0]).strip(),
                    'number': str(row[1]).strip(),
                    'cli': str(row[2]).strip(),
                    'message': str(row[3]).strip(),
                    'cause': str(row[4]).strip() if len(row) > 4 else ""
                })

            return valid_records

        except Exception as e:
            logger.error(f"Error fetching records for {self.username}: {e}")
            return None


class FlexSMSClientPool:
    """
    Dedicated high-speed multi-account client manager for Flex SMS portal.
    Rotates accounts in Round-Robin mode across Atiar59_FD, Smswork21_FD, and any added accounts.
    Provides instant failover: if one account gets rate-limited or drops, 
    the other account immediately takes over, delivering 0.4s - 0.8s ultra-low latency!
    """
    def __init__(self, base_url="http://168.119.13.175/ints", accounts=None):
        self.base_url = base_url.rstrip("/")
        self.clients = []
        self.current_idx = 0
        self.pool_rate_limited_until = 0.0
        self.lock = threading.Lock()
        if accounts:
            self.update_accounts(accounts)

    def update_accounts(self, accounts):
        with self.lock:
            if not accounts:
                accounts = [
                    {"username": "Atiar59_FD", "password": "Atiar5942@#"},
                    {"username": "Smswork21_FD", "password": "Smswork21_FD"}
                ]
            
            existing_map = {c.username.lower(): c for c in self.clients}
            new_clients = []
            
            for acc in accounts:
                u = acc.get("username", "").strip()
                p = acc.get("password", "").strip()
                prx = acc.get("proxy", "").strip() or None
                if not u or not p:
                    continue
                if u.lower() in existing_map:
                    c = existing_map[u.lower()]
                    needs_reauth = False
                    if c.password != p:
                        c.password = p
                        needs_reauth = True
                    if getattr(c, "proxy", None) != prx:
                        c.proxy = prx
                        if prx:
                            c.session.proxies = {'http': prx, 'https': prx}
                        else:
                            c.session.proxies = {}
                        needs_reauth = True
                    if needs_reauth:
                        c.is_logged_in = False
                        c.ajax_url = None
                    new_clients.append(c)
                else:
                    logger.info(f"Adding new client to pool: {u} (proxy: {'Yes' if prx else 'Direct'})")
                    new_clients.append(FlexSMSClient(self.base_url, u, p, proxy=prx))
            
            if not new_clients:
                new_clients.append(FlexSMSClient(self.base_url, "Atiar59_FD", "Atiar5942@#"))
            
            self.clients = new_clients
            self.current_idx = 0

    def get_accounts_status(self):
        with self.lock:
            clients = list(self.clients)
            
        statuses = []
        now = time.time()
        for c in clients:
            prx_badge = " 🇩🇪 DE" if (c.proxy and "_area-DE" in c.proxy) else (" 🌐 Prx" if c.proxy else " ⚡ Direct")
            if c.rate_limited_until > now:
                remaining = int(c.rate_limited_until - now)
                st = f"⏳ রেট-লিমিট ({remaining}s){prx_badge}"
            elif c.is_logged_in:
                resp = f" ({c.last_response_time:.2f}s)" if c.last_response_time > 0 else ""
                st = f"🟢 সক্রিয়{resp}{prx_badge}"
            else:
                st = f"🔴 লগইন হয়নি{prx_badge}"
            statuses.append({
                "username": c.username,
                "status": st,
                "is_logged_in": c.is_logged_in,
                "is_rate_limited": c.rate_limited_until > now,
                "response_time": c.last_response_time,
                "proxy": c.proxy
            })
        return statuses

    def login_account(self, username=None):
        with self.lock:
            target = None
            if username:
                for c in self.clients:
                    if c.username.lower() == username.lower():
                        target = c
                        break
            elif self.clients:
                target = self.clients[0]
                
        if target:
            return target.login()
        return False

    def login_all(self):
        with self.lock:
            clients = list(self.clients)
            
        results = {}
        for c in clients:
            ok = c.login()
            results[c.username] = ok
        return results

    def fetch_records(self, keyword="", display_length=15):
        with self.lock:
            if not self.clients:
                return None
            now = time.time()
            n = len(self.clients)
            chosen = None
            # Always try every client in round-robin: skip only if rate-limited
            for i in range(n):
                idx = (self.current_idx + i) % n
                c = self.clients[idx]
                if c.rate_limited_until <= now:
                    chosen = c
                    self.current_idx = (idx + 1) % n
                    break

            if not chosen:
                # All clients rate-limited — pick the one whose cooldown expires soonest
                soonest = min(self.clients, key=lambda c: c.rate_limited_until)
                wait = max(0.0, soonest.rate_limited_until - now)
                if wait > 0:
                    logger.warning(f"All accounts rate-limited. Waiting {wait:.1f}s for {soonest.username}...")
                    time.sleep(wait)
                chosen = soonest
                with self.lock:
                    self.current_idx = (self.clients.index(chosen) + 1) % n

        recs = chosen.fetch_records(keyword=keyword, display_length=display_length)
        if recs is not None:
            return recs
        
        # If rate limited, try the next available client immediately (don't halt the pool):
        if chosen.rate_limited_until > time.time():
            with self.lock:
                clients = list(self.clients)
            now = time.time()
            # Staggered failover: wait a tiny offset so both accounts never hammer simultaneously
            time.sleep(0.1)
            for c in clients:
                if c is not chosen and c.rate_limited_until <= now:
                    logger.info(f"Failover: switching from {chosen.username} to {c.username}")
                    recs2 = c.fetch_records(keyword=keyword, display_length=display_length)
                    if recs2 is not None:
                        return recs2
            return None

        # Quick re-auth if session dropped:
        if not chosen.is_logged_in:
            logger.info(f"Session expired for {chosen.username}. Quick re-auth...")
            if chosen.login():
                return chosen.fetch_records(keyword=keyword, display_length=display_length)

        return None

    @property
    def server_clock_skew(self):
        with self.lock:
            for c in self.clients:
                if getattr(c, 'server_clock_skew', None) is not None:
                    return c.server_clock_skew
        return 42.8

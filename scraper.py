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
    def __init__(self, base_url="http://168.119.13.175/ints", username="Atiar59_FD", password="Atiar5942@#"):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=10,
            max_retries=1,
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
        self.server_clock_offset = 41.5
        self.current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.last_login_time = 0
        self.rate_limited_until = 0.0
        self.last_response_time = 0.0

    def sync_server_clock(self, headers):
        """Calibrates clock difference between server HTTP Date header and local machine UTC."""
        try:
            date_str = headers.get('Date')
            if date_str:
                server_dt = email.utils.parsedate_to_datetime(date_str)
                now_utc = datetime.now(timezone.utc)
                offset = (now_utc - server_dt).total_seconds()
                if 20.0 < offset < 80.0:
                    self.server_clock_offset = offset
        except Exception:
            pass

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
        logger.info(f"Attempting login to Flex SMS portal for user {self.username}...")
        login_page_url = f"{self.base_url}/login"
        signin_url = f"{self.base_url}/signin"
        
        for attempt in range(3):
            try:
                r = self.session.get(login_page_url, timeout=15)
                self.sync_server_clock(r.headers)
                
                if r.status_code == 429:
                    logger.warning(f"Status 429 received on login for {self.username}. Backing off 2s...")
                    self.rate_limited_until = time.time() + 2.5
                    time.sleep(2)
                    continue
                if r.status_code != 200:
                    logger.error(f"Failed to load login page. Status: {r.status_code}")
                    time.sleep(2)
                    continue

                captcha_ans = self.solve_captcha(r.text)
                if captcha_ans is None:
                    logger.error("Could not parse captcha from login page!")
                    time.sleep(2)
                    continue

                logger.info(f"Captcha solved for {self.username}: {captcha_ans}")
                login_data = {
                    'username': self.username,
                    'password': self.password,
                    'capt': captcha_ans
                }
                
                r_post = self.session.post(signin_url, data=login_data, timeout=15, allow_redirects=True)
                self.sync_server_clock(r_post.headers)
                
                if "SMSDashboard" not in r_post.url and r_post.status_code != 200:
                    logger.error(f"Login failed for {self.username}. Final URL: {r_post.url}")
                    time.sleep(2)
                    continue

                # Add 0.5s pause before hitting FailedSMSReports so server doesn't hit 429 rate limit
                time.sleep(0.5)
                reports_url = f"{self.base_url}/agent/FailedSMSReports"
                r_rep = self.session.get(reports_url, timeout=15)
                self.sync_server_clock(r_rep.headers)
                
                if r_rep.status_code == 429:
                    logger.warning(f"Status 429 on FailedSMSReports for {self.username}. Backing off 2s...")
                    self.rate_limited_until = time.time() + 2.5
                    time.sleep(2)
                    r_rep = self.session.get(reports_url, timeout=15)
                    self.sync_server_clock(r_rep.headers)
                    if r_rep.status_code != 200:
                        continue
                if r_rep.status_code != 200:
                    logger.error(f"Failed to load FailedSMSReports page. Status: {r_rep.status_code}")
                    time.sleep(2)
                    continue

                ajax_match = re.search(r'sAjaxSource":\s*"([^"]+)"', r_rep.text)
                if not ajax_match:
                    logger.error("Could not find sAjaxSource in FailedSMSReports!")
                    time.sleep(2)
                    continue

                self.ajax_url = f"{self.base_url}/agent/{ajax_match.group(1)}"
                self.is_logged_in = True
                self.rate_limited_until = 0.0
                self.current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                self.last_login_time = time.time()
                logger.info(f"Login successful for {self.username}! Ajax endpoint initialized for date {self.current_date}")
                return True

            except Exception as e:
                logger.error(f"Exception during login attempt {attempt+1} for {self.username}: {e}")
                time.sleep(2)

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
            logger.info(f"User {self.username} is in rate-limit cooldown. Skipping...")
            return None

        # Check if login is needed
        if not self.is_logged_in or not self.ajax_url:
            logger.info(f"Login triggered for user {self.username}.")
            if not self.login():
                return None

        # Check if date rolled over (midnight UTC)
        if self.current_date != now_date_str:
            logger.info(f"Date rolled over from {self.current_date} to {now_date_str}. Performing fresh login for new date...")
            self.current_date = now_date_str
            self.is_logged_in = False
            if not self.login():
                return None

        headers = {
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': f"{self.base_url}/agent/FailedSMSReports"
        }
        
        params = {
            'sEcho': 1,
            'iDisplayStart': 0,
            'iDisplayLength': display_length,
            'iSortingCols': 1,
            'iSortCol_0': 0,
            'sSortDir_0': 'desc',
            'bSortable_0': 'true',
            'bSearchable_0': 'true',
            'bSearchable_1': 'true',
            'bSearchable_2': 'true',
            'bSearchable_3': 'true',
            'bSearchable_4': 'true',
            'sSearch': keyword.strip() if keyword else ''
        }

        try:
            t0 = time.time()
            res = self.session.get(self.ajax_url, params=params, headers=headers, timeout=12)
            self.last_response_time = time.time() - t0
            self.sync_server_clock(res.headers)
            
            # Check rate limit
            if res.status_code == 429:
                logger.warning(f"Status 429 on Ajax call for {self.username}. Cooldown set to 2.5s.")
                self.rate_limited_until = time.time() + 2.5
                return None

            # Check for session expiration indicators
            if res.status_code != 200 or "Direct Script Access Not Allowed" in res.text or "<!DOCTYPE html>" in res.text:
                logger.warning(f"Session expired for {self.username}. Re-logging in...")
                if self.login():
                    res = self.session.get(self.ajax_url, params=params, headers=headers, timeout=12)
                else:
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
    Manages multiple Flex SMS accounts with Round-Robin polling,
    automatic failover, and rate-limit prevention.
    """
    def __init__(self, base_url="http://168.119.13.175/ints", accounts=None):
        self.base_url = base_url.rstrip("/")
        self.clients = {}  # username -> FlexSMSClient
        self.current_idx = 0
        self.lock = threading.Lock()
        if accounts:
            self.update_accounts(accounts)

    def update_accounts(self, accounts):
        with self.lock:
            existing_usernames = set(self.clients.keys())
            new_usernames = set(a.get("username") for a in accounts if a.get("username"))

            for u in list(self.clients.keys()):
                if u not in new_usernames:
                    logger.info(f"Removing account {u} from pool")
                    del self.clients[u]

            for acc in accounts:
                u = acc.get("username")
                p = acc.get("password")
                if not u or not p:
                    continue
                if u not in self.clients:
                    logger.info(f"Adding account {u} to pool")
                    self.clients[u] = FlexSMSClient(self.base_url, u, p)
                elif self.clients[u].password != p:
                    logger.info(f"Updating password for {u} in pool")
                    self.clients[u] = FlexSMSClient(self.base_url, u, p)

    def get_accounts_status(self):
        with self.lock:
            statuses = []
            now = time.time()
            for u, c in self.clients.items():
                if c.rate_limited_until > now:
                    remaining = int(c.rate_limited_until - now)
                    st = f"⏳ রেট-লিমিট ({remaining}s)"
                elif c.is_logged_in:
                    resp = f" ({c.last_response_time:.1f}s)" if c.last_response_time > 0 else ""
                    st = f"🟢 লগইন সক্রিয়{resp}"
                else:
                    st = "🔴 লগইন হয়নি"
                statuses.append({
                    "username": u,
                    "status": st,
                    "is_logged_in": c.is_logged_in,
                    "is_rate_limited": c.rate_limited_until > now,
                    "response_time": c.last_response_time
                })
            return statuses

    def login_account(self, username):
        with self.lock:
            client = self.clients.get(username)
        if client:
            return client.login()
        return False

    def login_all(self):
        with self.lock:
            client_items = list(self.clients.items())
        results = {}
        for u, client in client_items:
            results[u] = client.login()
        return results

    def get_available_client(self, min_rest=0.8):
        with self.lock:
            now = time.time()
            available = [
                c for c in self.clients.values()
                if not getattr(c, 'is_busy', False)
                and c.rate_limited_until <= now
                and (now - getattr(c, 'last_finish', 0.0)) >= min_rest
            ]
            if not available:
                return None
            best = min(available, key=lambda c: getattr(c, 'last_finish', 0.0))
            best.is_busy = True
            return best

    def release_client(self, client):
        with self.lock:
            client.is_busy = False
            client.last_finish = time.time()

    def fetch_records(self, keyword="", display_length=15):
        with self.lock:
            if not self.clients:
                return None
            
            now = time.time()
            available = [c for c in self.clients.values() if c.rate_limited_until <= now]
            if not available:
                available = list(self.clients.values())

            self.current_idx = (self.current_idx + 1) % len(available)
            chosen_client = available[self.current_idx]

        return chosen_client.fetch_records(keyword=keyword, display_length=display_length)

    @property
    def server_clock_offset(self):
        with self.lock:
            for c in self.clients.values():
                if c.is_logged_in:
                    return c.server_clock_offset
            return 41.5

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
                    r = self.session.get(login_page_url, timeout=(3.0, 4.0))
                    
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
                    
                    r_post = self.session.post(signin_url, data=login_data, timeout=(3.0, 4.0), allow_redirects=True)
                    
                    if "SMSDashboard" not in r_post.url and r_post.status_code != 200:
                        logger.error(f"Login failed for {self.username}. Final URL: {r_post.url}")
                        time.sleep(0.3)
                        continue

                    reports_url = f"{self.base_url}/agent/FailedSMSReports"
                    r_rep = self.session.get(reports_url, timeout=(3.0, 4.0))
                    
                    if r_rep.status_code == 429:
                        logger.warning(f"Status 429 on FailedSMSReports for {self.username}. Backing off 0.5s...")
                        self.rate_limited_until = time.time() + 0.5
                        time.sleep(0.5)
                        r_rep = self.session.get(reports_url, timeout=(3.0, 4.0))
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
            res = self.session.get(self.ajax_url, params=params, headers=headers, timeout=(2.0, 3.5))
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
            
            # Check rate limit
            if res.status_code == 429:
                logger.warning(f"Status 429 on Ajax call for {self.username}. Cooldown set to 2.0s.")
                self.rate_limited_until = time.time() + 2.0
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
    Dedicated high-speed client manager for Flex SMS portal.
    Operates with a single dedicated account (Atiar59_FD) for maximum reliability,
    instant 1-2s delivery, and zero session conflict.
    """
    def __init__(self, base_url="http://168.119.13.175/ints", accounts=None):
        self.base_url = base_url.rstrip("/")
        self.client = None
        self.lock = threading.Lock()
        if accounts:
            self.update_accounts(accounts)

    def update_accounts(self, accounts):
        with self.lock:
            target_acc = None
            if accounts:
                for acc in accounts:
                    if acc.get("username") == "Atiar59_FD":
                        target_acc = acc
                        break
                if not target_acc:
                    target_acc = accounts[0]

            u = target_acc.get("username", "Atiar59_FD") if target_acc else "Atiar59_FD"
            p = target_acc.get("password", "Atiar5942@#") if target_acc else "Atiar5942@#"
            
            if not self.client or self.client.username != u or self.client.password != p:
                logger.info(f"Configuring single dedicated account: {u}")
                self.client = FlexSMSClient(self.base_url, u, p)

    def get_accounts_status(self):
        with self.lock:
            if not self.client:
                return []
            c = self.client
            now = time.time()
            if c.rate_limited_until > now:
                remaining = int(c.rate_limited_until - now)
                st = f"⏳ রেট-লিমিট ({remaining}s)"
            elif c.is_logged_in:
                resp = f" ({c.last_response_time:.1f}s)" if c.last_response_time > 0 else ""
                st = f"🟢 লগইন সক্রিয়{resp}"
            else:
                st = "🔴 লগইন হয়নি"
            return [{
                "username": c.username,
                "status": st,
                "is_logged_in": c.is_logged_in,
                "is_rate_limited": c.rate_limited_until > now,
                "response_time": c.last_response_time
            }]

    def login_account(self, username=None):
        with self.lock:
            client = self.client
        if client:
            return client.login()
        return False

    def login_all(self):
        with self.lock:
            client = self.client
        if client:
            ok = client.login()
            return {client.username: ok}
        return {}

    def fetch_records(self, keyword="", display_length=15):
        with self.lock:
            client = self.client

        if not client:
            return None

        recs = client.fetch_records(keyword=keyword, display_length=display_length)
        if recs is None and not client.is_logged_in:
            logger.info("Session expired mid-run. Performing instant re-authentication...")
            if client.login():
                recs = client.fetch_records(keyword=keyword, display_length=display_length)

        return recs

    @property
    def server_clock_skew(self):
        with self.lock:
            if self.client and getattr(self.client, 'server_clock_skew', None) is not None:
                return self.client.server_clock_skew
        return 42.8

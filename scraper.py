import requests
import re
import logging
import urllib.parse
import time
import email.utils
from datetime import datetime, timezone

logger = logging.getLogger("FlexSMSScraper")

class FlexSMSClient:
    def __init__(self, base_url="http://168.119.13.175/ints", username="Atiar59_FD", password="Atiar5942@#"):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        self.ajax_url = None
        self.is_logged_in = False
        self.server_clock_offset = 41.5
        self.current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.last_login_time = 0

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
        logger.info("Attempting login to Flex SMS portal...")
        login_page_url = f"{self.base_url}/login"
        signin_url = f"{self.base_url}/signin"
        
        for attempt in range(3):
            try:
                r = self.session.get(login_page_url, timeout=15)
                self.sync_server_clock(r.headers)
                
                if r.status_code == 429:
                    logger.warning("Status 429 received on login. Backing off 3s...")
                    time.sleep(3)
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

                logger.info(f"Captcha solved: {captcha_ans}")
                login_data = {
                    'username': self.username,
                    'password': self.password,
                    'capt': captcha_ans
                }
                
                r_post = self.session.post(signin_url, data=login_data, timeout=15, allow_redirects=True)
                self.sync_server_clock(r_post.headers)
                
                if "SMSDashboard" not in r_post.url and r_post.status_code != 200:
                    logger.error(f"Login failed. Final URL: {r_post.url}")
                    time.sleep(2)
                    continue

                # Add 0.5s pause before hitting FailedSMSReports so server doesn't hit 429 rate limit
                time.sleep(0.5)
                reports_url = f"{self.base_url}/agent/FailedSMSReports"
                r_rep = self.session.get(reports_url, timeout=15)
                self.sync_server_clock(r_rep.headers)
                
                if r_rep.status_code == 429:
                    logger.warning("Status 429 on FailedSMSReports. Backing off 3s...")
                    time.sleep(3)
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
                self.current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                self.last_login_time = time.time()
                logger.info(f"Login successful! Ajax endpoint initialized for date {self.current_date}: {self.ajax_url} (Clock skew: {self.server_clock_offset:.1f}s)")
                return True

            except Exception as e:
                logger.error(f"Exception during login attempt {attempt+1}: {e}")
                time.sleep(2)

        self.is_logged_in = False
        return False

    def fetch_records(self, keyword="", display_length=25):
        """
        Fetches failed SMS records.
        Strictly sorts DESCENDING so latest records are always first.
        Automatically updates date at midnight UTC and keeps session fresh.
        """
        now_date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        # Check if login is needed
        if not self.is_logged_in or not self.ajax_url:
            logger.info("Initial login triggered.")
            if not self.login():
                return None

        # Check if date rolled over (midnight UTC)
        if self.current_date != now_date_str:
            logger.info(f"Date rolled over from {self.current_date} to {now_date_str}. Updating endpoint dates...")
            self.current_date = now_date_str
            if self.ajax_url:
                self.ajax_url = re.sub(r'fdate1=\d{4}-\d{2}-\d{2}', f'fdate1={now_date_str}', self.ajax_url)
                self.ajax_url = re.sub(r'fdate2=\d{4}-\d{2}-\d{2}', f'fdate2={now_date_str}', self.ajax_url)

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

        for attempt in range(2):
            try:
                res = self.session.get(self.ajax_url, params=params, headers=headers, timeout=15)
                self.sync_server_clock(res.headers)
                
                # Check rate limit
                if res.status_code == 429:
                    logger.warning("Status 429 on Ajax call. Backing off 2s...")
                    time.sleep(2)
                    continue

                # Check for session expiration indicators
                if res.status_code != 200 or "Direct Script Access Not Allowed" in res.text or "<!DOCTYPE html>" in res.text:
                    logger.warning("Session expired or rejected by server. Re-logging in...")
                    if self.login():
                        continue
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
                logger.error(f"Error fetching records (attempt {attempt+1}): {e}")
                if attempt == 0:
                    logger.info("Attempting re-login and retry...")
                    self.login()

        return None

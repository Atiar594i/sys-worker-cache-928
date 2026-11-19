#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Main entry point for the Dedicated Telegram OTP Web Hub.
Runs the standalone website engine located in the dedicated 'website/' folder.
"""
import sys
import subprocess
import time
import os

print("=" * 65)
print("🚀 Launching Dedicated Telegram OTP Website Hub from 'website/'...")
print("   - Directory: website/")
print("   - Dual Accounts: Atiar59_FD & Smswork21_FD (Round-Robin)")
print("   - Auto-Refresh (1s) & Instant 0ms SSE Pushes")
print("   - Smart Proxy Rotator & Rate-Limit Shield Active")
print("=" * 65)

website_script = os.path.join(os.path.dirname(__file__), "website", "app.py")

while True:
    try:
        p = subprocess.Popen([sys.executable, website_script])
        p.wait()
        print("⚠️ Website process exited! Restarting in 1s...")
        time.sleep(1.0)
    except KeyboardInterrupt:
        break
    except Exception as e:
        print(f"Error running website: {e}")
        time.sleep(2.0)

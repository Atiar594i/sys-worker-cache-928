#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dual-Engine Main Entry Point:
1. Dedicated Web Hub (website/app.py) on Railway's $PORT:
   - High-speed round-robin scraper for Atiar59_FD & Smswork21_FD
   - Instant 0ms SSE pushes to Web Dashboard
   - Instant non-blocking dispatches to Telegram Group
2. Telegram Bot Interface (bot.py):
   - Command handlers (/start, /menu, /status, /numbers, buttons)
   - Decoupled from scraping to prevent session collisions
"""

import sys
import subprocess
import time
import os

print("=" * 65)
print("🚀 Launching Dual High-Speed Engines:")
print("   1. Dedicated Telegram OTP Web Hub (website/app.py)")
print("   2. Telegram User Interface Bot (bot.py)")
print("=" * 65)

root_dir = os.path.dirname(os.path.abspath(__file__))
env_web = os.environ.copy()
env_web["PYTHONPATH"] = root_dir

env_bot = os.environ.copy()
env_bot["PYTHONPATH"] = root_dir
env_bot["SEPARATE_WEB_SERVER"] = "1"
env_bot["BOT_STANDALONE_COMMANDS"] = "1"
env_bot["BOT_DISABLED"] = "false"

# 1. Start Web Server Process
p_web = subprocess.Popen([sys.executable, "-m", "website.app"], cwd=root_dir, env=env_web)
print("✅ Web Server process started (PID: {})".format(p_web.pid))

time.sleep(1.0)

# 2. Start Telegram Bot Process
p_bot = subprocess.Popen([sys.executable, "bot.py"], cwd=root_dir, env=env_bot)
print("✅ Telegram Bot process started (PID: {})".format(p_bot.pid))

try:
    while True:
        # Check Web Server health
        if p_web.poll() is not None:
            print("⚠️ Web Server exited! Restarting in 1s...")
            p_web = subprocess.Popen([sys.executable, "-m", "website.app"], cwd=root_dir, env=env_web)

        # Check Bot health
        if p_bot.poll() is not None:
            print("⚠️ Telegram Bot exited! Restarting in 1s...")
            p_bot = subprocess.Popen([sys.executable, "bot.py"], cwd=root_dir, env=env_bot)

        time.sleep(2.0)
except KeyboardInterrupt:
    print("Terminating processes...")
    try:
        p_web.terminate()
    except Exception:
        pass
    try:
        p_bot.terminate()
    except Exception:
        pass

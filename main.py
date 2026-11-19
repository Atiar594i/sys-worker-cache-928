#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import subprocess
import time

print("=" * 65)
print("🚀 Launching Dual-Engine System:")
print("   1. Dedicated Real-Time OTP Web Engine (web_server.py)")
print("   2. Telegram User Interface Bot (bot.py)")
print("=" * 65)

env = os.environ.copy()
env["SEPARATE_WEB_SERVER"] = "1"

# 1. Start dedicated Web Server on Railway's $PORT:
p_web = subprocess.Popen([sys.executable, "web_server.py"], env=env)

# Small pause to allow web server to bind to port cleanly:
time.sleep(1.0)

# 2. Start Telegram Bot:
p_bot = subprocess.Popen([sys.executable, "bot.py"], env=env)

try:
    while True:
        if p_web.poll() is not None:
            print("⚠️ Web server exited! Restarting...")
            p_web = subprocess.Popen([sys.executable, "web_server.py"], env=env)
        if p_bot.poll() is not None:
            print("⚠️ Bot exited! Restarting...")
            p_bot = subprocess.Popen([sys.executable, "bot.py"], env=env)
        time.sleep(3)
except KeyboardInterrupt:
    p_web.terminate()
    p_bot.terminate()

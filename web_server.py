#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⚡ Dedicated Telegram OTP Web Server
Redirects to the modular architecture in the 'website/' directory.
"""
import sys
import os
import asyncio

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from website.app import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

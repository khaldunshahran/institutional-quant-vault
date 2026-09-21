#!/usr/bin/env python3
"""
One-click launcher for the Polymarket BTC 5M Chrome Dashboard.
Starts the server and automatically opens Google Chrome or default browser.
"""

import os
import sys
import time
import webbrowser
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SERVER_SCRIPT = PROJECT_ROOT / "ui" / "server.py"
PORT = 5000
URL = f"http://localhost:{PORT}"


def main():
    python_exe = sys.executable
    print("=" * 60)
    print("  STARTING BTC 5M POLYMARKET CHROME DASHBOARD")
    print("=" * 60)
    print(f"Server URL: {URL}")
    print("Starting background server and opening browser...\n")

    # Start server as subprocess
    server_proc = subprocess.Popen(
        [python_exe, str(SERVER_SCRIPT), "--port", str(PORT)],
        cwd=str(PROJECT_ROOT),
    )

    # Wait 1.5s for server to bind port
    time.sleep(1.5)

    # Try opening Chrome specifically, fallback to default browser
    try:
        chrome_opened = False
        # Common Windows Chrome paths
        chrome_paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
        for p in chrome_paths:
            if os.path.exists(p):
                subprocess.Popen([p, URL])
                chrome_opened = True
                print(f"Opened Google Chrome at: {URL}")
                break

        if not chrome_opened:
            webbrowser.open(URL)
            print(f"Opened default browser at: {URL}")
    except Exception as e:
        print(f"Could not open browser automatically: {e}")
        print(f"Please open your browser manually and visit: {URL}")

    print("\nDashboard is active! Press Ctrl+C in this terminal to stop.")
    try:
        server_proc.wait()
    except KeyboardInterrupt:
        print("\nStopping dashboard server...")
        server_proc.terminate()
        server_proc.wait()
        print("Server stopped.")


if __name__ == "__main__":
    main()

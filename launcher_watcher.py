#!/usr/bin/env python3
"""Lightweight background watcher for official ArtCraft Launcher.

Detects when the official ArtCraft Launcher.app is opened.
If 14 days have passed since the last update check, displays a native
macOS prompt offering to open the full auto-updater screen.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/Volumes/AdobeApps/ArtCraft")
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
AGENT_LABEL = "ai.storyteller.artcraft.watcher"
AGENT_PLIST_PATH = LAUNCH_AGENTS_DIR / f"{AGENT_LABEL}.plist"

sys.path.insert(0, str(ROOT))
import artcraft_updater


def is_official_launcher_running() -> bool:
    try:
        res = subprocess.run(["/bin/ps", "-axo", "command="], capture_output=True, text=True, check=False)
        for line in res.stdout.splitlines():
            if "ArtCraft Launcher.app/Contents/MacOS" in line or "/Applications/ArtCraft Launcher" in line:
                return True
        return False
    except Exception:
        return False


def prompt_user_for_update() -> bool:
    """Show native macOS dialog asking if user wants to open the updater screen."""
    script = (
        'display dialog "前回の確認から2週間が経過しました。\\n\\n'
        'GitHubリポジトリをリサーチし、ArtCraftアプリ（12本）の新バージョンを差分インストールしますか？" '
        'with title "ArtCraft 定期アップデート確認" '
        'buttons {"あとで", "更新画面を開く"} default button "更新画面を開く" with icon caution'
    )
    res = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True)
    return "更新画面を開く" in res.stdout


def open_updater_screen() -> None:
    # Ensure launcher_server is running
    server_running = False
    try:
        res = subprocess.run(["/bin/ps", "-axo", "command="], capture_output=True, text=True, check=False)
        server_running = "launcher_server.py" in res.stdout
    except Exception:
        pass

    if not server_running:
        subprocess.Popen([sys.executable, str(ROOT / "launcher_server.py")])
        time.sleep(1)

    # Open the SSD Launcher in default browser (which will auto-trigger the update prompt)
    subprocess.run(["/usr/bin/open", "http://127.0.0.1:58820/"], check=False)


def setup_watcher_agent() -> None:
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    python_bin = sys.executable
    script_path = ROOT / "launcher_watcher.py"

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{AGENT_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_bin}</string>
        <string>{script_path}</string>
        <string>--daemon</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/artcraft_watcher.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/artcraft_watcher_err.log</string>
</dict>
</plist>
"""
    AGENT_PLIST_PATH.write_text(plist_content, encoding="utf-8")
    subprocess.run(["/bin/launchctl", "unload", str(AGENT_PLIST_PATH)], capture_output=True)
    subprocess.run(["/bin/launchctl", "load", str(AGENT_PLIST_PATH)], capture_output=True)
    print(f"✅ 公式ランチャー監視エージェントを登録しました: {AGENT_PLIST_PATH}")


def remove_watcher_agent() -> None:
    if AGENT_PLIST_PATH.is_file():
        subprocess.run(["/bin/launchctl", "unload", str(AGENT_PLIST_PATH)], capture_output=True)
        AGENT_PLIST_PATH.unlink()
        print(f"🗑️ 監視エージェントを解除しました: {AGENT_PLIST_PATH}")
    else:
        print("ℹ️ 監視エージェントは登録されていません")


def watch_loop() -> None:
    """Loop that checks if official launcher opened while 2-week schedule is due."""
    was_running = False
    print("ArtCraft Launcher Watcher running...")
    while True:
        is_running = is_official_launcher_running()
        # Trigger when transitioned from not running -> running
        if is_running and not was_running:
            if artcraft_updater.is_schedule_due():
                accepted = prompt_user_for_update()
                if accepted:
                    open_updater_screen()
                else:
                    # Snooze for 1 day
                    artcraft_updater.snooze_schedule(1)
        was_running = is_running
        time.sleep(3)


def main() -> int:
    if "--install-agent" in sys.argv:
        setup_watcher_agent()
        return 0
    if "--uninstall-agent" in sys.argv:
        remove_watcher_agent()
        return 0
    if "--check-once" in sys.argv:
        if artcraft_updater.is_schedule_due():
            print("2週間が経過しています。ポップアップを表示します...")
            if prompt_user_for_update():
                open_updater_screen()
        else:
            print("2週間は経過していません。")
        return 0

    watch_loop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

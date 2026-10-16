#!/usr/bin/env python3
"""Local backend server for ArtCraft SSD Launcher UI."""

from __future__ import annotations

import http.server
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import artcraft_updater

ROOT = Path("/Volumes/AdobeApps/ArtCraft")
APPS_DIR = ROOT / "Apps"
ASSETS_DIR = ROOT / ".launcher-assets"
VOLUME = Path("/Volumes/AdobeApps")

PIPELINE_STATE: dict[str, Any] = {
    "running": False,
    "events": [],
    "summary": None,
    "error": None,
}
PIPELINE_LOCK = threading.Lock()

APP_METADATA = {
    "PhotoCraft": {"desc": "高度な写真編集・RAW現像・レタッチ", "cat": "画像・写真"},
    "VectorCraft": {"desc": "精密なベクターグラフィックス・イラスト作成", "cat": "デザイン"},
    "DesignCraft": {"desc": "UI/UX・ページレイアウト・プロトタイピング", "cat": "デザイン"},
    "CADCraft": {"desc": "高精度な3Dモデリング・CAD設計", "cat": "エンジニアリング"},
    "FilmCraft": {"desc": "マルチトラック動画編集・カラーグレーディング", "cat": "映像・動画"},
    "EffectCraft": {"desc": "モーショングラフィックス・VFX・特殊効果", "cat": "映像・動画"},
    "SoundCraft": {"desc": "プロフェッショナルDAW・音楽制作・音声編集", "cat": "音楽・オーディオ"},
    "GridCraft": {"desc": "高機能スプレッドシート・データ集計・分析", "cat": "オフィス・データ"},
    "WordCraft": {"desc": "高品質ドキュメント・文書作成・タイポグラフィ", "cat": "オフィス・ドキュメント"},
    "PdfCraft": {"desc": "PDFの閲覧・注釈・結合・セキュリティ編集", "cat": "オフィス・ユーティリティ"},
    "LightCraft": {"desc": "高速RAW写真現像・ライティング調整", "cat": "画像・写真"},
    "DeckCraft": {"desc": "直感的なプレゼンテーション・スライド作成", "cat": "オフィス・プレゼン"},
}


def running_processes() -> set[str]:
    try:
        res = subprocess.run(["/bin/ps", "-axo", "command="], capture_output=True, text=True, check=False)
        running = set()
        for line in res.stdout.splitlines():
            for name in APP_METADATA:
                if f"{name}.app/Contents/MacOS" in line:
                    running.add(name)
        return running
    except Exception:
        return set()


def get_disk_info() -> dict:
    try:
        usage = shutil.disk_usage(VOLUME)
        total_gb = usage.total / 1_000_000_000
        free_gb = usage.free / 1_000_000_000
        used_gb = usage.used / 1_000_000_000
        percent = (usage.used / usage.total) * 100
        return {
            "total_gb": f"{total_gb:.1f}",
            "free_gb": f"{free_gb:.1f}",
            "used_gb": f"{used_gb:.1f}",
            "percent": f"{percent:.1f}",
        }
    except Exception:
        return {"total_gb": "-", "free_gb": "-", "used_gb": "-", "percent": "0"}


def get_apps_data() -> list[dict]:
    active = running_processes()
    items = []
    for name, meta in APP_METADATA.items():
        app_path = APPS_DIR / f"{name}.app"
        icon_path = ASSETS_DIR / f"{name}.png"
        installed = app_path.is_dir()
        version = "-"
        if installed:
            plist_path = app_path / "Contents" / "Info.plist"
            try:
                import plistlib
                with plist_path.open("rb") as f:
                    pl = plistlib.load(f)
                    version = pl.get("CFBundleShortVersionString", "-")
            except Exception:
                version = "installed"

        items.append({
            "name": name,
            "version": version,
            "desc": meta["desc"],
            "category": meta["cat"],
            "installed": installed,
            "running": name in active,
            "icon": f"/assets/{name}.png" if icon_path.exists() else "",
        })
    return items


class LauncherHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass  # Quiet logging

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            html_file = ROOT / ".launcher-assets" / "launcher_ui.html"
            if html_file.exists():
                content = html_file.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            else:
                self.send_error(404, "UI not found")
                return

        if path.startswith("/assets/"):
            asset_name = path[len("/assets/"):]
            asset_file = ASSETS_DIR / asset_name
            if asset_file.is_file() and not asset_name.startswith("."):
                content = asset_file.read_bytes()
                self.send_response(200)
                content_type = "image/png" if asset_name.endswith(".png") else "application/octet-stream"
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

        if path == "/api/status":
            data = {
                "apps": get_apps_data(),
                "disk": get_disk_info(),
            }
            self.send_json(data)
            return

        if path == "/api/launch":
            app_name = query.get("app", [""])[0]
            if app_name in APP_METADATA:
                app_path = APPS_DIR / f"{app_name}.app"
                if app_path.exists():
                    subprocess.run(["/usr/bin/open", "-n", str(app_path)], check=False)
                    self.send_json({"ok": True, "message": f"{app_name} を起動しました"})
                    return
            self.send_json({"ok": False, "error": "App not found"})
            return

        if path == "/api/reveal":
            target = query.get("target", ["apps"])[0]
            folder = APPS_DIR if target == "apps" else ROOT / "Installers"
            if folder.exists():
                subprocess.run(["/usr/bin/open", str(folder)], check=False)
                self.send_json({"ok": True})
                return
            self.send_json({"ok": False})
            return

        if path == "/api/sync":
            action = query.get("action", ["status"])[0]
            script = ROOT / "sync_official_launcher.py"
            if script.exists():
                flag = f"--{action}"
                res = subprocess.run([sys.executable, str(script), flag], capture_output=True, text=True, check=False)
                self.send_json({"ok": res.returncode == 0, "output": res.stdout + res.stderr})
                return
            self.send_json({"ok": False, "error": "Script missing"})
            return

        if path == "/api/updates/check":
            updater_script = ROOT / "artcraft_updater.py"
            if updater_script.exists():
                cmd = [sys.executable, str(updater_script), "--check", "--json"]
                if "no_cache" in query:
                    cmd.append("--no-cache")
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                try:
                    payload = json.loads(res.stdout)
                    self.send_json({"ok": True, **payload})
                except Exception as e:
                    self.send_json({"ok": False, "error": f"JSON解析失敗: {e}", "raw": res.stdout + res.stderr})
                return
            self.send_json({"ok": False, "error": "Updater script missing"})
            return

        if path == "/api/updates/apply":
            updater_script = ROOT / "artcraft_updater.py"
            app_id = query.get("app", [""])[0]
            if updater_script.exists():
                cmd = [sys.executable, str(updater_script), "--update", "--notify"]
                if app_id:
                    cmd.extend(["--app", app_id])
                res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                self.send_json({
                    "ok": res.returncode == 0,
                    "output": res.stdout + res.stderr,
                })
                return
            self.send_json({"ok": False, "error": "Updater script missing"})
            return

        if path == "/api/schedule/status":
            state = artcraft_updater.get_schedule_state()
            due = artcraft_updater.is_schedule_due()
            self.send_json({"ok": True, "is_due": due, "state": state})
            return

        if path == "/api/schedule/snooze":
            days = int(query.get("days", [1])[0])
            new_state = artcraft_updater.snooze_schedule(days)
            self.send_json({"ok": True, "state": new_state})
            return

        if path == "/api/schedule/test-trigger":
            new_state = artcraft_updater.test_trigger_schedule()
            self.send_json({"ok": True, "state": new_state, "is_due": True})
            return

        if path == "/api/pipeline/start":
            global PIPELINE_STATE
            with PIPELINE_LOCK:
                if PIPELINE_STATE["running"]:
                    self.send_json({"ok": False, "error": "すでにリサーチ・更新パイプラインが実行中です"})
                    return
                PIPELINE_STATE = {
                    "running": True,
                    "events": [],
                    "summary": None,
                    "error": None,
                }

            def run_worker() -> None:
                global PIPELINE_STATE
                def on_event(ev: dict) -> None:
                    with PIPELINE_LOCK:
                        PIPELINE_STATE["events"].append(ev)
                try:
                    summary = artcraft_updater.run_full_cycle(callback=on_event, force_refresh=True)
                    with PIPELINE_LOCK:
                        PIPELINE_STATE["summary"] = summary
                        PIPELINE_STATE["running"] = False
                except Exception as exc:
                    with PIPELINE_LOCK:
                        PIPELINE_STATE["error"] = str(exc)
                        PIPELINE_STATE["running"] = False

            worker = threading.Thread(target=run_worker, daemon=True)
            worker.start()
            self.send_json({"ok": True, "message": "リサーチ＆更新パイプラインを開始しました"})
            return

        if path == "/api/pipeline/progress":
            since = int(query.get("since", [0])[0])
            with PIPELINE_LOCK:
                events_slice = PIPELINE_STATE["events"][since:]
                self.send_json({
                    "ok": True,
                    "running": PIPELINE_STATE["running"],
                    "events": events_slice,
                    "total_events": len(PIPELINE_STATE["events"]),
                    "summary": PIPELINE_STATE["summary"],
                    "error": PIPELINE_STATE["error"],
                })
            return

        self.send_error(404, "Not Found")

    def send_json(self, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    port = 58820
    for p in range(58820, 58830):
        try:
            server = socketserver.TCPServer(("127.0.0.1", p), LauncherHandler)
            port = p
            break
        except OSError:
            continue
    else:
        print("ポートの確保に失敗しました", file=sys.stderr)
        sys.exit(1)

    url = f"http://127.0.0.1:{port}/"
    print(f"ArtCraft SSD Launcher Server running at: {url}")

    # Open in default browser if requested
    if "--open" in sys.argv:
        subprocess.run(["/usr/bin/open", url], check=False)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""ArtCraft Differential Auto-Updater and GitHub Release Monitor.

Safely checks GitHub Releases for new versions of ArtCraft apps,
sends macOS desktop notifications, and performs zero-trust delta upgrades.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import artcraft_ssd_installer as installer

ROOT = Path("/Volumes/AdobeApps/ArtCraft")
APPS_DIR = ROOT / "Apps"
INSTALLERS_DIR = ROOT / "Installers"
BACKUP_DIR = ROOT / "Apps_backup"
TEMP_ROOT = ROOT / ".install-tmp"
CACHE_PATH = ROOT / ".updater_cache.json"
STATE_PATH = ROOT / ".updater_state.json"
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
AGENT_LABEL = "ai.storyteller.artcraft.updater"
AGENT_PLIST_PATH = LAUNCH_AGENTS_DIR / f"{AGENT_LABEL}.plist"

CACHE_TTL_SECONDS = 3600  # 1 hour cache to respect GitHub API limits
SCHEDULE_INTERVAL_DAYS = 14  # 2 weeks default reminder interval


def get_schedule_state() -> dict[str, Any]:
    """Read schedule state or initialize default (due immediately on fresh install)."""
    if STATE_PATH.is_file():
        try:
            with STATE_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data
        except Exception:
            pass

    # Default initial state
    now_ts = time.time()
    return {
        "last_checked_at": None,
        "next_remind_at": dt.datetime.fromtimestamp(now_ts).isoformat(),
        "interval_days": SCHEDULE_INTERVAL_DAYS,
        "snoozed": False,
    }


def save_schedule_state(state: dict[str, Any]) -> None:
    try:
        with STATE_PATH.open("w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def is_schedule_due() -> bool:
    """Return True if 2 weeks have passed since last completed check."""
    state = get_schedule_state()
    next_remind_str = state.get("next_remind_at")
    if not next_remind_str:
        return True
    try:
        next_remind_dt = dt.datetime.fromisoformat(next_remind_str)
        return dt.datetime.now() >= next_remind_dt
    except Exception:
        return True


def record_check_completed() -> dict[str, Any]:
    """Record that a research/update cycle has completed, scheduling the next for +14 days."""
    now = dt.datetime.now()
    next_remind = now + dt.timedelta(days=SCHEDULE_INTERVAL_DAYS)
    state = {
        "last_checked_at": now.isoformat(),
        "next_remind_at": next_remind.isoformat(),
        "interval_days": SCHEDULE_INTERVAL_DAYS,
        "snoozed": False,
    }
    save_schedule_state(state)
    return state


def snooze_schedule(days: int = 1) -> dict[str, Any]:
    """Snooze notification for a number of days."""
    now = dt.datetime.now()
    next_remind = now + dt.timedelta(days=days)
    state = get_schedule_state()
    state["next_remind_at"] = next_remind.isoformat()
    state["snoozed"] = True
    save_schedule_state(state)
    return state


def test_trigger_schedule() -> dict[str, Any]:
    """Force schedule to be immediately due for test/preview."""
    now = dt.datetime.now()
    state = get_schedule_state()
    state["next_remind_at"] = (now - dt.timedelta(minutes=1)).isoformat()
    state["snoozed"] = False
    save_schedule_state(state)
    return state


APP_DEFINITIONS = [
    {"app_id": "photocraft", "name": "PhotoCraft", "bundle_id": "ai.storyteller.photocraft"},
    {"app_id": "cadcraft", "name": "CADCraft", "bundle_id": "ai.storyteller.cadcraft"},
    {"app_id": "deckcraft", "name": "DeckCraft", "bundle_id": "ai.storyteller.deckcraft"},
    {"app_id": "designcraft", "name": "DesignCraft", "bundle_id": "ai.storyteller.designcraft"},
    {"app_id": "effectcraft", "name": "EffectCraft", "bundle_id": "ai.storyteller.effectcraft"},
    {"app_id": "filmcraft", "name": "FilmCraft", "bundle_id": "ai.storyteller.filmcraft"},
    {"app_id": "gridcraft", "name": "GridCraft", "bundle_id": "ai.storyteller.gridcraft"},
    {"app_id": "lightcraft", "name": "LightCraft", "bundle_id": "ai.storyteller.lightcraft"},
    {"app_id": "pdfcraft", "name": "PdfCraft", "bundle_id": "ai.storyteller.pdfcraft"},
    {"app_id": "soundcraft", "name": "SoundCraft", "bundle_id": "ai.storyteller.soundcraft"},
    {"app_id": "vectorcraft", "name": "VectorCraft", "bundle_id": "ai.storyteller.vectorcraft"},
    {"app_id": "wordcraft", "name": "WordCraft", "bundle_id": "ai.storyteller.wordcraft"},
]


@dataclass
class UpdateStatus:
    app_id: str
    name: str
    bundle_id: str
    local_version: str | None
    latest_version: str | None
    has_update: bool
    dmg_url: str | None
    dmg_name: str | None
    dmg_size: int | None
    sums_url: str | None
    release_notes: str | None
    checked_at: str
    error: str | None = None


def notify_macos(title: str, subtitle: str, message: str, sound: str = "Glass") -> bool:
    """Send a native macOS desktop notification via osascript."""
    safe_title = title.replace('"', '\\"')
    safe_sub = subtitle.replace('"', '\\"')
    safe_msg = message.replace('"', '\\"')
    script = (
        f'display notification "{safe_msg}" with title "{safe_title}" '
        f'subtitle "{safe_sub}" sound name "{sound}"'
    )
    res = subprocess.run(["/usr/bin/osascript", "-e", script], capture_output=True, text=True)
    return res.returncode == 0


def get_local_version(app_name: str) -> str | None:
    """Read the CFBundleShortVersionString from the external SSD app bundle."""
    plist_path = APPS_DIR / f"{app_name}.app" / "Contents" / "Info.plist"
    if not plist_path.is_file():
        return None
    try:
        with plist_path.open("rb") as f:
            data = plistlib.load(f)
            return str(data.get("CFBundleShortVersionString"))
    except Exception:
        return None


def clean_version(tag: str) -> str:
    """Normalize 'v1.2.3' to '1.2.3'."""
    tag = tag.strip()
    return tag[1:] if tag.startswith("v") or tag.startswith("V") else tag


def load_cache() -> dict[str, Any]:
    if not CACHE_PATH.is_file():
        return {}
    try:
        with CACHE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(cache: dict[str, Any]) -> None:
    try:
        with CACHE_PATH.open("w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def fetch_github_release(app_id: str, use_cache: bool = True) -> tuple[dict[str, Any] | None, str | None]:
    """Fetch latest release info from GitHub Releases API with caching and ETag support."""
    cache = load_cache() if use_cache else {}
    cached_entry = cache.get(app_id)
    now = time.time()

    if use_cache and cached_entry:
        cached_time = cached_entry.get("cached_at", 0)
        if now - cached_time < CACHE_TTL_SECONDS:
            return cached_entry.get("data"), None

    url = f"https://api.github.com/repos/storytold/{app_id}/releases/latest"
    headers = {
        "User-Agent": "ArtCraft-AutoUpdater/1.0",
        "Accept": "application/vnd.github.v3+json",
    }
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"token {github_token}"

    if cached_entry and cached_entry.get("etag"):
        headers["If-None-Match"] = cached_entry["etag"]

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            etag = resp.headers.get("ETag")
            cache[app_id] = {
                "cached_at": now,
                "etag": etag,
                "data": data,
            }
            save_cache(cache)
            return data, None
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and cached_entry:
            cached_entry["cached_at"] = now
            save_cache(cache)
            return cached_entry.get("data"), None
        elif exc.code == 403:
            return None, "GitHub APIレートリミットに達しました（時間をおいて再試行してください）"
        elif exc.code == 404:
            return None, "リポジトリまたはリリースが見つかりません"
        return None, f"GitHub HTTPエラー: {exc.code} {exc.reason}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        if cached_entry and "data" in cached_entry:
            # Fallback to cache on network failure
            return cached_entry["data"], None
        return None, f"ネットワーク接続エラー: {exc}"


def check_app_update(app_def: dict[str, str], use_cache: bool = True) -> UpdateStatus:
    """Check update status for a single app."""
    app_id = app_def["app_id"]
    name = app_def["name"]
    bundle_id = app_def["bundle_id"]

    local_ver = get_local_version(name)
    release_data, err = fetch_github_release(app_id, use_cache=use_cache)

    checked_at = dt.datetime.now().isoformat()
    if err or not release_data:
        return UpdateStatus(
            app_id=app_id,
            name=name,
            bundle_id=bundle_id,
            local_version=local_ver,
            latest_version=None,
            has_update=False,
            dmg_url=None,
            dmg_name=None,
            dmg_size=None,
            sums_url=None,
            release_notes=None,
            checked_at=checked_at,
            error=err or "リリース情報を取得できませんでした",
        )

    raw_tag = release_data.get("tag_name") or release_data.get("name") or ""
    latest_ver = clean_version(raw_tag)

    # Find DMG and SHA256SUMS asset
    dmg_url = None
    dmg_name = None
    dmg_size = None
    sums_url = None

    expected_dmg_name = f"{app_id}-{latest_ver}-macos-universal.dmg"
    for asset in release_data.get("assets", []):
        asset_name = asset.get("name", "")
        if asset_name == expected_dmg_name or asset_name.endswith("-macos-universal.dmg"):
            dmg_url = asset.get("browser_download_url")
            dmg_name = asset_name
            dmg_size = asset.get("size")
        elif asset_name == "SHA256SUMS.txt":
            sums_url = asset.get("browser_download_url")

    # Compare versions
    has_update = False
    if local_ver and latest_ver:
        cmp = installer.compare_versions(local_ver, latest_ver)
        has_update = cmp is not None and cmp < 0
    elif not local_ver and latest_ver:
        # App not installed on SSD yet
        has_update = True

    return UpdateStatus(
        app_id=app_id,
        name=name,
        bundle_id=bundle_id,
        local_version=local_ver,
        latest_version=latest_ver,
        has_update=has_update,
        dmg_url=dmg_url,
        dmg_name=dmg_name,
        dmg_size=dmg_size,
        sums_url=sums_url,
        release_notes=(release_data.get("body") or "")[:500],
        checked_at=checked_at,
        error=None,
    )


def check_all_updates(
    app_ids: list[str] | None = None,
    use_cache: bool = True,
) -> list[UpdateStatus]:
    """Check updates for all apps or a specified subset."""
    results: list[UpdateStatus] = []
    targets = APP_DEFINITIONS
    if app_ids:
        targets = [d for d in targets if d["app_id"] in app_ids]

    for app_def in targets:
        status = check_app_update(app_def, use_cache=use_cache)
        results.append(status)
    return results


def download_file(
    url: str,
    dest: Path,
    expected_size: int | None = None,
    on_progress: Any = None,
) -> None:
    """Download a file with progress display and atomicity."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dest = dest.with_suffix(dest.suffix + f".tmp.{os.getpid()}")
    headers = {"User-Agent": "ArtCraft-AutoUpdater/1.0"}
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=60) as resp, tmp_dest.open("wb") as out:
            total = int(resp.headers.get("Content-Length", 0)) or expected_size or 0
            downloaded = 0
            start_t = time.monotonic()
            last_report = 0.0
            while True:
                chunk = resp.read(1024 * 512)
                if not chunk:
                    break
                out.write(chunk)
                downloaded += len(chunk)
                now_m = time.monotonic()
                if total > 0 and (now_m - last_report > 0.2 or downloaded == total):
                    percent = downloaded / total * 100
                    elapsed = now_m - start_t
                    speed_mb = (downloaded / (1024 * 1024)) / (elapsed if elapsed > 0 else 1)
                    if on_progress:
                        on_progress(percent, downloaded / (1024 * 1024), total / (1024 * 1024), speed_mb)
                    if sys.stdout.isatty():
                        sys.stdout.write(
                            f"\r    ダウンロード中: {percent:.1f}% ({downloaded / (1024*1024):.1f}/{total / (1024*1024):.1f} MB) - {speed_mb:.1f} MB/s"
                        )
                        sys.stdout.flush()
                    last_report = now_m
            if sys.stdout.isatty():
                sys.stdout.write("\n")
        tmp_dest.replace(dest)
    except Exception as exc:
        if tmp_dest.is_file():
            tmp_dest.unlink(missing_ok=True)
        raise installer.InstallerError(f"ダウンロードに失敗しました: {exc}") from exc


def fetch_checksum_for_dmg(sums_url: str, dmg_name: str) -> str:
    """Download SHA256SUMS.txt and extract expected hash for dmg_name."""
    headers = {"User-Agent": "ArtCraft-AutoUpdater/1.0"}
    req = urllib.request.Request(sums_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            text = resp.read().decode("utf-8")
    except Exception as exc:
        raise installer.InstallerError(f"チェックサムファイルの取得に失敗しました: {exc}") from exc

    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line.strip())
        if match and match.group(2) == dmg_name:
            return match.group(1).lower()

    raise installer.InstallerError(f"SHA256SUMS.txt内に {dmg_name} のハッシュ値が見つかりません")


def perform_delta_update(status: UpdateStatus, callback: Any = None) -> dict[str, Any]:
    """Perform a zero-trust safe update of a single app from status metadata."""
    if not status.has_update or not status.latest_version:
        return {"app": status.name, "status": "skipped", "reason": "更新の必要はありません"}

    if not status.dmg_url or not status.dmg_name or not status.sums_url:
        return {
            "app": status.name,
            "status": "failed",
            "reason": "リリースアセット（DMGまたはSHA256SUMS.txt）が不足しています",
        }

    spec = installer.AppSpec(
        app_id=status.app_id,
        name=status.name,
        version=status.latest_version,
        bundle_id=status.bundle_id,
    )

    def emit(step: str, detail: str, percent: float | None = None) -> None:
        if callback:
            callback({
                "app": status.name,
                "app_id": status.app_id,
                "step": step,
                "detail": detail,
                "percent": percent,
            })

    print(f"\n📦 [{status.name}] アップデート開始: v{status.local_version or '未導入'} -> v{status.latest_version}")
    emit("start", f"v{status.local_version or '未導入'} から v{status.latest_version} への更新開始")

    # 1. Download DMG & SHA256SUMS if not already cached
    target_dmg = INSTALLERS_DIR / status.dmg_name
    if not target_dmg.is_file() or (status.dmg_size and target_dmg.stat().st_size != status.dmg_size):
        print(f"  ⬇️  DMGをダウンロード中: {status.dmg_name}")
        emit("download", f"DMGをダウンロード中 ({status.dmg_name})", 0.0)
        def on_dl(pct: float, cur: float, tot: float, spd: float) -> None:
            emit("download", f"{pct:.1f}% ({cur:.1f}/{tot:.1f} MB) - {spd:.1f} MB/s", pct)
        download_file(status.dmg_url, target_dmg, status.dmg_size, on_progress=on_dl)
    else:
        print(f"  ⚡ 既存のDMGキャッシュを使用: {status.dmg_name}")
        emit("download", "既存のDMGキャッシュを使用", 100.0)

    # 2. Verify SHA256 hash
    print("  🔍 SHA-256 チェックサム照合中...")
    emit("verify_hash", "SHA-256 チェックサム照合中...")
    expected_hash = fetch_checksum_for_dmg(status.sums_url, status.dmg_name)
    actual_hash = installer.sha256_file(target_dmg)
    if expected_hash != actual_hash:
        raise installer.InstallerError(
            f"ハッシュ不一致! 期待値: {expected_hash}, 実測値: {actual_hash}"
        )
    print("  ✅ SHA-256 ハッシュ照合合格")

    # 3. Mount DMG safely
    print("  💽 DMGをマウント中...")
    emit("mount", "DMGを読み取り専用で安全マウント中...")
    mount_point, dev_entry = installer.attach_dmg(spec)
    app_in_dmg = mount_point / spec.app_name
    backup_target = BACKUP_DIR / f"{status.name}_{status.local_version or 'old'}.app"
    staged_dir = TEMP_ROOT / f"update_stage_{status.app_id}"

    try:
        if not app_in_dmg.is_dir():
            raise installer.InstallerError(f"マウントされたDMG内に {spec.app_name} が見つかりません")

        # 4. Verify signature and Gatekeeper on DMG bundle
        print("  🔐 Apple開発者署名 & 公証 (Gatekeeper) 検証中...")
        emit("verify_signature", "Apple開発者署名 ＆ Gatekeeper公証チェック中...")
        inspection = installer.inspect_bundle(app_in_dmg, spec, allow_dmg_detritus=True)
        if not inspection["verified"]:
            raise installer.InstallerError("DMG内アプリの署名・公証・メタデータ検証に合格しませんでした")
        print("  ✅ コード署名・公証検証合格")

        # 5. Copy to staging area with ditto (stripping FinderInfo detritus)
        if staged_dir.exists():
            shutil.rmtree(staged_dir, ignore_errors=True)
        staged_dir.mkdir(parents=True, exist_ok=True)
        staged_app = staged_dir / spec.app_name

        print("  📋 クリーンコピー中 (ditto --noextattr)...")
        emit("copy", "外付けSSDへクリーンコピー中 (ditto --noextattr)...")
        copied = installer.run_command([
            "/usr/bin/ditto",
            "--norsrc",
            "--noextattr",
            "--noacl",
            str(app_in_dmg),
            str(staged_app),
        ])
        if not installer.command_ok(copied):
            raise installer.InstallerError(f"一時ステージへのコピーに失敗しました: {installer.compact_output(copied)}")

        # Verify staged app
        staged_check = installer.inspect_bundle(staged_app, spec)
        if not staged_check["verified"]:
            raise installer.InstallerError("ステージング済みアプリの検証に失敗しました")

        # 6. Safe destination replacement with rollback guarantee
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        if spec.destination.exists():
            print(f"  💾 既存バージョンを退避中: {backup_target.name}")
            emit("backup", f"旧バージョンを退避中 ({backup_target.name})...")
            if backup_target.exists():
                shutil.rmtree(backup_target, ignore_errors=True)
            spec.destination.rename(backup_target)

        try:
            print("  🚀 外付けSSDへ最新版を配置中...")
            emit("install", "外付けSSDへ正式配置中...")
            staged_app.rename(spec.destination)
        except Exception as exc:
            print("  ⚠️ 配置に失敗したため、旧バージョンへロールバックします...")
            if backup_target.exists():
                backup_target.rename(spec.destination)
            raise installer.InstallerError(f"アプリの配置に失敗しました: {exc}") from exc

        # 7. Verify final destination bundle
        final_check = installer.inspect_bundle(spec.destination, spec)
        if not final_check["verified"]:
            print("  ⚠️ 配置後検証に失敗したため、ロールバックします...")
            shutil.rmtree(spec.destination, ignore_errors=True)
            if backup_target.exists():
                backup_target.rename(spec.destination)
            raise installer.InstallerError("外付けSSD上の新バージョン検証に失敗しました")

        # 8. Verify /Applications symlink
        internal_link = Path("/Applications") / spec.app_name
        if internal_link.is_symlink():
            link_target = os.readlink(internal_link)
            if link_target != str(spec.destination):
                print(f"  🔗 内蔵シンボリックリンクを更新: {internal_link} -> {spec.destination}")
                internal_link.unlink()
                internal_link.symlink_to(spec.destination)

        # 9. Clean up old backup if update succeeded
        if backup_target.exists():
            shutil.rmtree(backup_target, ignore_errors=True)

        print(f"  🎉 [{status.name}] v{status.latest_version} への安全アップデート完了！")
        emit("complete", f"v{status.latest_version} への安全アップデート完了！", 100.0)
        return {
            "app": status.name,
            "status": "updated",
            "previous_version": status.local_version,
            "new_version": status.latest_version,
        }

    finally:
        # Always unmount and clean temp dirs
        installer.detach_dmg(mount_point)
        if staged_dir.exists():
            shutil.rmtree(staged_dir, ignore_errors=True)


def run_full_cycle(callback: Any = None, force_refresh: bool = False) -> dict[str, Any]:
    """Execute the complete end-to-end cycle:

    1. Research all 12 repos
    2. Determine differences
    3. Download & apply updates for diffs
    4. Record schedule (+14 days)
    5. Return full summary report
    """
    def emit_cycle(phase: str, message: str, data: Any = None) -> None:
        if callback:
            callback({"phase": phase, "message": message, "data": data, "timestamp": time.time()})

    emit_cycle("research_start", "GitHub Releases の全リポジトリ調査を開始します...")
    results: list[UpdateStatus] = []
    total = len(APP_DEFINITIONS)

    for idx, app_def in enumerate(APP_DEFINITIONS, 1):
        emit_cycle(
            "researching",
            f"[{idx}/{total}] {app_def['name']} のリポジトリをリサーチ中...",
            {"app_id": app_def["app_id"], "name": app_def["name"], "index": idx, "total": total},
        )
        status = check_app_update(app_def, use_cache=not force_refresh)
        results.append(status)
        emit_cycle(
            "researched",
            f"{app_def['name']}: {'🔔 更新あり (v' + (status.latest_version or '') + ')' if status.has_update else '✅ 最新 (v' + (status.local_version or '') + ')'}",
            asdict(status),
        )

    updates_needed = [r for r in results if r.has_update]
    emit_cycle(
        "research_summary",
        f"リサーチ完了: {len(updates_needed)} 本のアプリに更新があります",
        {"total": total, "updates_count": len(updates_needed)},
    )

    updated_count = 0
    failed_count = 0

    if updates_needed:
        emit_cycle("update_start", f"{len(updates_needed)} 本の差分アップデートを開始します...")
        for u in updates_needed:
            try:
                def on_app_step(step_data: dict) -> None:
                    emit_cycle("app_step", f"[{step_data.get('app')}] {step_data.get('detail')}", step_data)

                res = perform_delta_update(u, callback=on_app_step)
                if res.get("status") == "updated":
                    updated_count += 1
            except Exception as exc:
                failed_count += 1
                emit_cycle("app_error", f"[{u.name}] 更新失敗: {exc}", {"app": u.name, "error": str(exc)})
    else:
        emit_cycle("all_latest", "すべてのアプリがすでに最新バージョンです")

    # Record 14-day schedule completion
    schedule_state = record_check_completed()

    summary = {
        "success": failed_count == 0,
        "total_apps": total,
        "updates_count": len(updates_needed),
        "updated_count": updated_count,
        "failed_count": failed_count,
        "next_remind_at": schedule_state["next_remind_at"],
        "results": [asdict(r) for r in results],
    }
    emit_cycle("finished", "全工程が完了しました！", summary)
    return summary



def setup_launch_agent() -> None:
    """Register macOS LaunchAgent to automatically check updates daily."""
    LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    python_bin = sys.executable
    script_path = ROOT / "artcraft_updater.py"

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
        <string>--check</string>
        <string>--notify</string>
    </array>
    <key>StartInterval</key>
    <integer>86400</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/artcraft_updater.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/artcraft_updater_err.log</string>
</dict>
</plist>
"""
    AGENT_PLIST_PATH.write_text(plist_content, encoding="utf-8")
    subprocess.run(["/bin/launchctl", "unload", str(AGENT_PLIST_PATH)], capture_output=True)
    subprocess.run(["/bin/launchctl", "load", str(AGENT_PLIST_PATH)], capture_output=True)
    print(f"✅ LaunchAgent を登録しました: {AGENT_PLIST_PATH}")
    print("   毎日バックグラウンドで更新確認が行われ、新版があればデスクトップ通知が表示されます。")


def remove_launch_agent() -> None:
    """Unregister the background LaunchAgent."""
    if AGENT_PLIST_PATH.is_file():
        subprocess.run(["/bin/launchctl", "unload", str(AGENT_PLIST_PATH)], capture_output=True)
        AGENT_PLIST_PATH.unlink()
        print(f"🗑️ LaunchAgent を解除しました: {AGENT_PLIST_PATH}")
    else:
        print("ℹ️ LaunchAgent は登録されていません")


def main() -> int:
    parser = argparse.ArgumentParser(description="ArtCraft GitHub 差分自動更新マネージャー")
    parser.add_argument("--check", action="store_true", help="GitHub Releasesをチェックし、更新有無を表示")
    parser.add_argument("--update", action="store_true", help="更新があるアプリをダウンロードして安全インストール")
    parser.add_argument("--app", help="特定のアプリIDのみ対象にする (例: photocraft)")
    parser.add_argument("--no-cache", action="store_true", help="キャッシュを使用せずGitHub APIに直接問い合わせる")
    parser.add_argument("--notify", action="store_true", help="更新がある場合にmacOSデスクトップ通知を表示")
    parser.add_argument("--json", action="store_true", help="結果をJSON形式で出力 (ランチャーUI/API用)")
    parser.add_argument("--install-agent", action="store_true", help="日次自動チェック用LaunchAgentを登録")
    parser.add_argument("--uninstall-agent", action="store_true", help="日次自動チェック用LaunchAgentを解除")

    args = parser.parse_args()

    if args.install_agent:
        setup_launch_agent()
        return 0

    if args.uninstall_agent:
        remove_launch_agent()
        return 0

    mode = "update" if args.update else "check"
    app_ids = [args.app.lower()] if args.app else None

    # Check updates
    results = check_all_updates(app_ids=app_ids, use_cache=not args.no_cache)
    updates_available = [r for r in results if r.has_update]

    if args.json:
        payload = {
            "timestamp": dt.datetime.now().isoformat(),
            "updates_count": len(updates_available),
            "results": [asdict(r) for r in results],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print("=" * 65)
    print(" 🚀 ArtCraft アップデート確認マネージャー (GitHub Monitor)")
    print("=" * 65)
    print(f"{'アプリ名':<15} {'SSD現行版':<12} {'最新リリース':<12} {'ステータス'}")
    print("-" * 65)

    for r in results:
        local_s = f"v{r.local_version}" if r.local_version else "(未導入)"
        latest_s = f"v{r.latest_version}" if r.latest_version else "(取得失敗)"
        if r.error:
            status_s = f"❌ エラー: {r.error}"
        elif r.has_update:
            status_s = "🔔 アップデート可能！"
        else:
            status_s = "✅ 最新版です"
        print(f"{r.name:<15} {local_s:<12} {latest_s:<12} {status_s}")

    print("-" * 65)

    # Handle notifications
    if args.notify and updates_available:
        app_names = ", ".join(r.name for r in updates_available[:3])
        if len(updates_available) > 3:
            app_names += f" ほか計{len(updates_available)}本"
        notify_macos(
            title="ArtCraft アップデート通知",
            subtitle=f"{len(updates_available)}本の新バージョンがあります",
            message=f"更新対象: {app_names}\nランチャーまたは artcraft_updater から更新できます。",
            sound="Glass",
        )

    if mode == "check":
        if updates_available:
            print(f"\n💡 {len(updates_available)} 本のアプリに新しいバージョンが見つかりました！")
            print("   以下のコマンドで差分のみを一括安全更新できます:")
            print("   python3 artcraft_updater.py --update\n")
        else:
            print("\n✨ すべてのアプリが最新バージョンです！更新の必要はありません。\n")
        return 0

    # Perform update
    if not updates_available:
        print("\n✨ すべてのアプリが最新バージョンです。更新処理を終了します。\n")
        return 0

    print(f"\n⚡ {len(updates_available)} 本のアップデートを開始します...\n")
    success_count = 0
    fail_count = 0

    for item in updates_available:
        try:
            res = perform_delta_update(item)
            if res.get("status") == "updated":
                success_count += 1
        except Exception as exc:
            print(f"❌ [{item.name}] 更新失敗: {exc}")
            fail_count += 1

    print("\n" + "=" * 65)
    print(f"🏁 アップデート完了: 成功 {success_count} 本 / 失敗 {fail_count} 本")
    print("=" * 65)

    if args.notify:
        notify_macos(
            title="ArtCraft アップデート完了",
            subtitle=f"成功: {success_count} 本, 失敗: {fail_count} 本",
            message="外付けSSD上のアプリが安全に更新されました。",
            sound="Hero" if fail_count == 0 else "Basso",
        )

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

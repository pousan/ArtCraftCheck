#!/usr/bin/env python3
"""Safely inspect or install the twelve ArtCraft macOS apps on AdobeApps."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
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
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


ROOT = Path("/Volumes/AdobeApps/ArtCraft")
VOLUME = Path("/Volumes/AdobeApps")
INSTALLERS = ROOT / "Installers"
DESTINATION = ROOT / "Apps"
REPORTS = ROOT / "reports"
TEMP_ROOT = ROOT / ".install-tmp"
LOCK_PATH = ROOT / ".artcraft-ssd-installer.lock"
EXPECTED_TEAM_ID = "DJ6XS33FX8"
TIMEOUT_SECONDS = 300
LAUNCH_TIMEOUT_SECONDS = 20


@dataclass(frozen=True)
class AppSpec:
    app_id: str
    name: str
    version: str
    bundle_id: str

    @property
    def dmg_name(self) -> str:
        return f"{self.app_id}-{self.version}-macos-universal.dmg"

    @property
    def dmg_path(self) -> Path:
        return INSTALLERS / self.dmg_name

    @property
    def app_name(self) -> str:
        return f"{self.name}.app"

    @property
    def destination(self) -> Path:
        return DESTINATION / self.app_name

    @property
    def sums_url(self) -> str:
        return (
            f"https://github.com/storytold/{self.app_id}/releases/download/"
            f"v{self.version}/SHA256SUMS.txt"
        )


# PhotoCraft is intentionally first: it is the canary for an install run.
APPS: tuple[AppSpec, ...] = (
    AppSpec("photocraft", "PhotoCraft", "0.9.0", "ai.storyteller.photocraft"),
    AppSpec("cadcraft", "CADCraft", "0.7.0", "ai.storyteller.cadcraft"),
    AppSpec("deckcraft", "DeckCraft", "0.7.0", "ai.storyteller.deckcraft"),
    AppSpec("designcraft", "DesignCraft", "0.8.0", "ai.storyteller.designcraft"),
    AppSpec("effectcraft", "EffectCraft", "0.10.0", "ai.storyteller.effectcraft"),
    AppSpec("filmcraft", "FilmCraft", "0.8.0", "ai.storyteller.filmcraft"),
    AppSpec("gridcraft", "GridCraft", "0.7.0", "ai.storyteller.gridcraft"),
    AppSpec("lightcraft", "LightCraft", "0.8.0", "ai.storyteller.lightcraft"),
    AppSpec("pdfcraft", "PdfCraft", "0.8.0", "ai.storyteller.pdfcraft"),
    AppSpec("soundcraft", "SoundCraft", "0.7.0", "ai.storyteller.soundcraft"),
    AppSpec("vectorcraft", "VectorCraft", "0.11.0", "ai.storyteller.vectorcraft"),
    AppSpec("wordcraft", "WordCraft", "0.7.0", "ai.storyteller.wordcraft"),
)


class InstallerError(RuntimeError):
    """A safe, user-facing stop for one app or the whole run."""


def run_command(args: list[str], timeout: int = TIMEOUT_SECONDS) -> dict[str, Any]:
    """Run a command without a shell and retain enough evidence for the report."""
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "args": args,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "args": args,
            "returncode": None,
            "stdout": (exc.stdout or "").strip() if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "").strip() if isinstance(exc.stderr, str) else "",
            "timed_out": True,
        }
    except OSError as exc:
        return {
            "args": args,
            "returncode": None,
            "stdout": "",
            "stderr": str(exc),
            "timed_out": False,
        }


def command_ok(result: dict[str, Any]) -> bool:
    return result["returncode"] == 0 and not result["timed_out"]


def compact_output(result: dict[str, Any], limit: int = 1500) -> str:
    combined = "\n".join(part for part in (result["stdout"], result["stderr"]) if part)
    return combined[-limit:]


def failure_kind(result: dict[str, Any]) -> str:
    """Separate tool/permission failures from an actual negative assessment."""
    text = compact_output(result).lower()
    if result["timed_out"]:
        return "tool_timeout"
    restricted_markers = (
        "operation not permitted",
        "permission denied",
        "not authorized",
        "unable to use the diskmanagement framework",
        "interaction not allowed",
    )
    if result["returncode"] is None or any(marker in text for marker in restricted_markers):
        return "tool_or_permission_error"
    return "negative_verification"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_published_checksum(spec: AppSpec) -> tuple[str, str]:
    local_sums = INSTALLERS / "SHA256SUMS.txt"
    if local_sums.is_file():
        text = local_sums.read_text(encoding="utf-8")
        source_label = str(local_sums)
    else:
        request = urllib.request.Request(
            spec.sums_url,
            headers={"User-Agent": "ArtCraft-SSD-Installer/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                final_host = urllib.parse.urlparse(response.geturl()).hostname or ""
                allowed_hosts = {
                    "github.com",
                    "objects.githubusercontent.com",
                    "release-assets.githubusercontent.com",
                }
                if final_host not in allowed_hosts:
                    raise InstallerError(f"チェックサム取得先がGitHub管理下ではありません: {final_host}")
                text = response.read(1024 * 1024).decode("utf-8")
            source_label = spec.sums_url
        except (urllib.error.URLError, TimeoutError, UnicodeError, OSError) as exc:
            raise InstallerError(f"公開チェックサムを取得できません: {exc}") from exc

    matches: list[str] = []
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line.strip())
        if match and match.group(2) == spec.dmg_name:
            matches.append(match.group(1).lower())
    if len(matches) != 1:
        raise InstallerError(
            f"SHA256SUMS.txtに{spec.dmg_name}の一意なチェックサムがありません"
        )
    return matches[0], source_label


def plist_value(path: Path, key: str) -> str | None:
    try:
        with path.open("rb") as handle:
            value = plistlib.load(handle).get(key)
        return str(value) if value is not None else None
    except (OSError, plistlib.InvalidFileException, AttributeError):
        return None


def signature_details(bundle: Path) -> dict[str, Any]:
    display = run_command(["/usr/bin/codesign", "-dvvv", str(bundle)], timeout=120)
    display_text = compact_output(display, 8000)
    team_match = re.search(r"^TeamIdentifier=(.+)$", display_text, re.MULTILINE)
    team = team_match.group(1).strip() if team_match else None
    requirement = (
        '=anchor apple generic and certificate leaf[subject.OU] = '
        f'"{EXPECTED_TEAM_ID}"'
    )
    verify = run_command(
        [
            "/usr/bin/codesign",
            "--verify",
            "--deep",
            "--strict",
            "--verbose=4",
            "-R",
            requirement,
            str(bundle),
        ],
        timeout=120,
    )
    return {
        "team_id": team,
        "identity_output": display_text[-2000:],
        "codesign_ok": command_ok(verify),
        "codesign_failure_kind": None if command_ok(verify) else failure_kind(verify),
        "codesign_output": compact_output(verify),
    }


def gatekeeper_details(bundle: Path) -> dict[str, Any]:
    assess = run_command(
        ["/usr/sbin/spctl", "--assess", "--type", "execute", "-vvv", str(bundle)],
        timeout=120,
    )
    return {
        "spctl_ok": command_ok(assess),
        "spctl_failure_kind": None if command_ok(assess) else failure_kind(assess),
        "spctl_output": compact_output(assess),
    }


def inspect_bundle(bundle: Path, spec: AppSpec, allow_dmg_detritus: bool = False) -> dict[str, Any]:
    plist = bundle / "Contents" / "Info.plist"
    name = plist_value(plist, "CFBundleName") or plist_value(plist, "CFBundleDisplayName")
    executable = plist_value(plist, "CFBundleExecutable")
    bundle_id = plist_value(plist, "CFBundleIdentifier")
    version = plist_value(plist, "CFBundleShortVersionString")
    signature = signature_details(bundle)
    gatekeeper = gatekeeper_details(bundle)
    expected_name = name in {spec.name, None} and bundle.name == spec.app_name
    metadata_ok = (
        expected_name
        and bundle_id == spec.bundle_id
        and same_release(version, spec.version)
        and bool(executable)
    )
    codesign_ok = signature["codesign_ok"]
    if not codesign_ok and allow_dmg_detritus:
        output = signature.get("codesign_output", "")
        if "resource fork, Finder information, or similar detritus not allowed" in output:
            codesign_ok = True
    ok = (
        metadata_ok
        and signature["team_id"] == EXPECTED_TEAM_ID
        and codesign_ok
        and gatekeeper["spctl_ok"]
    )
    return {
        "path": str(bundle),
        "bundle_name": name,
        "bundle_id": bundle_id,
        "version": version,
        "executable": executable,
        "metadata_ok": metadata_ok,
        **signature,
        **gatekeeper,
        "verified": ok,
    }


def version_parts(version: str | None) -> tuple[int, int, int, tuple[str, ...]] | None:
    if version is None:
        return None
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+]([0-9A-Za-z.-]+))?", version)
    if not match:
        return None
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        tuple((match.group(4) or "").split(".")) if match.group(4) else (),
    )


def same_release(found: str | None, expected: str) -> bool:
    found_parts = version_parts(found)
    expected_parts = version_parts(expected)
    if found_parts is None or expected_parts is None:
        return False
    return found_parts[:3] == expected_parts[:3] and (
        not found_parts[3] or found_parts[3] == expected_parts[3]
    )


def compare_versions(left: str | None, right: str) -> int | None:
    left_parts = version_parts(left)
    right_parts = version_parts(right)
    if left_parts is None or right_parts is None:
        return None
    if left_parts[:3] != right_parts[:3]:
        return (left_parts[:3] > right_parts[:3]) - (left_parts[:3] < right_parts[:3])
    # A final release is newer than a pre-release with the same numeric triplet.
    left_pre, right_pre = left_parts[3], right_parts[3]
    left_key = (1, ()) if not left_pre else (0, left_pre)
    right_key = (1, ()) if not right_pre else (0, right_pre)
    return (left_key > right_key) - (left_key < right_key)


def destination_decision(
    exists: bool,
    version: str | None,
    verified: bool,
    expected_version: str,
) -> str:
    """Pure decision used by the installer and self-tests."""
    if not exists:
        return "install"
    if verified and same_release(version, expected_version):
        return "skip_verified"
    return "hold_existing_destination"


def internal_version_guard(spec: AppSpec) -> dict[str, Any]:
    internal = Path("/Applications") / spec.app_name
    if not internal.exists():
        return {"path": str(internal), "exists": False, "decision": "continue"}
    version = plist_value(internal / "Contents" / "Info.plist", "CFBundleShortVersionString")
    comparison = compare_versions(version, spec.version)
    if comparison is None:
        decision = "hold_unknown_internal_version"
    elif comparison > 0:
        decision = "hold_would_downgrade"
    else:
        decision = "continue"
    return {
        "path": str(internal),
        "exists": True,
        "version": version,
        "comparison_to_dmg": comparison,
        "decision": decision,
    }


def verified_mount() -> dict[str, Any]:
    mount_result = run_command(["/sbin/mount"])
    if not command_ok(mount_result):
        raise InstallerError(f"マウント一覧を取得できません: {compact_output(mount_result)}")
    prefix = f" on {VOLUME} ("
    matching = [line for line in mount_result["stdout"].splitlines() if prefix in line]
    if len(matching) != 1 or not VOLUME.is_dir():
        raise InstallerError(
            f"{VOLUME}が独立したマウントポイントとして確認できません。内蔵側には作成しません"
        )
    source = matching[0].split(" on ", 1)[0]
    if not source.startswith("/dev/"):
        raise InstallerError(f"SSDのデバイスが想定外です: {source}")
    return {"mount_line": matching[0], "device": source, "mount_point": str(VOLUME)}


def disk_usage_snapshot() -> dict[str, int]:
    usage = shutil.disk_usage(VOLUME)
    return {"total_bytes": usage.total, "used_bytes": usage.used, "free_bytes": usage.free}


def directory_size(path: Path) -> int | None:
    if not path.exists():
        return None
    result = run_command(["/usr/bin/du", "-sk", str(path)], timeout=180)
    if not command_ok(result):
        return None
    try:
        kib = int(result["stdout"].split()[0])
    except (ValueError, IndexError):
        return None
    return kib * 1024


def format_size(byte_count: int | None) -> str:
    if byte_count is None:
        return "未確認"
    return f"{byte_count / 1_000_000:.2f} MB / {byte_count / 1_000_000_000:.3f} GB"


def attach_dmg(spec: AppSpec) -> tuple[Path, str]:
    result = run_command(
        [
            "/usr/bin/hdiutil",
            "attach",
            "-nobrowse",
            "-readonly",
            "-noautoopen",
            "-noverify",
            "-plist",
            str(spec.dmg_path),
        ]
    )
    if not command_ok(result):
        raise InstallerError(f"DMGを読み取り専用でマウントできません: {compact_output(result)}")
    try:
        raw_stdout = (
            result["stdout"].encode("utf-8")
            if isinstance(result["stdout"], str)
            else result["stdout"]
        )
        data = plistlib.loads(raw_stdout)
    except Exception as exc:
        raise InstallerError(f"マウント情報の解析に失敗しました: {exc}")
    mount_point = None
    dev_entry = None
    for entity in data.get("system-entities", []):
        if "mount-point" in entity:
            mount_point = entity["mount-point"]
            dev_entry = entity.get("dev-entry")
            break
    if not mount_point:
        raise InstallerError("マウント先パスを取得できませんでした")
    return Path(mount_point), dev_entry or mount_point


def detach_dmg(target: str | Path) -> dict[str, Any]:
    target_str = str(target)
    normal = run_command(["/usr/bin/hdiutil", "detach", target_str, "-quiet"], timeout=60)
    if command_ok(normal):
        return {"detached": True, "forced": False, "output": compact_output(normal)}
    forced = run_command(
        ["/usr/bin/hdiutil", "detach", target_str, "-force", "-quiet"],
        timeout=60,
    )
    return {
        "detached": command_ok(forced),
        "forced": True,
        "normal_failure": compact_output(normal),
        "output": compact_output(forced),
    }


def process_rows() -> list[tuple[int, str]]:
    result = run_command(["/bin/ps", "-axo", "pid=,command="])
    if not command_ok(result):
        return []
    rows: list[tuple[int, str]] = []
    for line in result["stdout"].splitlines():
        match = re.match(r"\s*(\d+)\s+(.+)", line)
        if match:
            rows.append((int(match.group(1)), match.group(2)))
    return rows


def related_processes(spec: AppSpec, external: Path) -> dict[str, list[dict[str, Any]]]:
    external_prefix = str(external / "Contents" / "MacOS")
    internal_prefix = str(Path("/Applications") / spec.app_name / "Contents" / "MacOS")
    executable = plist_value(external / "Contents" / "Info.plist", "CFBundleExecutable")
    groups: dict[str, list[dict[str, Any]]] = {"external": [], "internal": [], "unknown": []}
    for pid, command in process_rows():
        row = {"pid": pid, "command": command}
        if external_prefix in command:
            groups["external"].append(row)
        elif internal_prefix in command:
            groups["internal"].append(row)
        elif executable and re.search(rf"(?:^|/){re.escape(executable)}(?:\s|$)", command):
            groups["unknown"].append(row)
    return groups


def screen_evidence(pid: int) -> dict[str, Any]:
    script = (
        'tell application "System Events" to tell first process whose unix id is '
        f"{pid} to return ((visible as string) & \"|\" & (count of windows as string))"
    )
    result = run_command(["/usr/bin/osascript", "-e", script], timeout=20)
    if not command_ok(result):
        return {
            "confirmed": False,
            "reason": failure_kind(result),
            "output": compact_output(result),
        }
    parts = result["stdout"].strip().split("|", 1)
    return {
        "confirmed": True,
        "visible": parts[0].lower() == "true",
        "window_count": int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else None,
    }


def launch_check(spec: AppSpec) -> dict[str, Any]:
    before = related_processes(spec, spec.destination)
    if before["internal"] or before["unknown"]:
        return {
            "status": "held_existing_process",
            "reason": "内蔵版または場所を特定できない同名プロセスが実行中",
            "processes": before,
        }
    if before["external"]:
        pid = before["external"][0]["pid"]
        return {
            "status": "verified_already_running",
            "process_path_confirmed": True,
            "processes": before["external"],
            "screen": screen_evidence(pid),
        }

    opened = run_command(["/usr/bin/open", "-n", str(spec.destination)], timeout=30)
    if not command_ok(opened):
        return {
            "status": "launch_request_failed",
            "reason": compact_output(opened),
            "failure_kind": failure_kind(opened),
        }
    deadline = time.monotonic() + LAUNCH_TIMEOUT_SECONDS
    after: dict[str, list[dict[str, Any]]] = {"external": [], "internal": [], "unknown": []}
    while time.monotonic() < deadline:
        after = related_processes(spec, spec.destination)
        if after["external"]:
            break
        time.sleep(1)
    if not after["external"]:
        return {
            "status": "unconfirmed_after_request",
            "launch_request_succeeded": True,
            "processes": after,
        }
    pid = after["external"][0]["pid"]
    return {
        "status": "verified_running_external",
        "launch_request_succeeded": True,
        "process_path_confirmed": True,
        "processes": after["external"],
        "screen": screen_evidence(pid),
    }


def install_or_inspect_app(
    spec: AppSpec,
    mode: str,
    run_temp: Path,
    do_launch_check: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "app": spec.name,
        "app_id": spec.app_id,
        "expected_version": spec.version,
        "expected_bundle_id": spec.bundle_id,
        "expected_team_id": EXPECTED_TEAM_ID,
        "dmg": str(spec.dmg_path),
        "destination": str(spec.destination),
        "status": "failed",
        "launch": {"status": "not_requested"},
    }
    mountpoint: Path | None = None
    detach_target: str | None = None
    attached = False
    staged: Path | None = None
    try:
        guard = internal_version_guard(spec)
        result["internal_copy"] = guard
        if guard["decision"] != "continue":
            result["status"] = "held"
            result["reason"] = guard["decision"]
            return result

        if not spec.dmg_path.is_file():
            raise InstallerError(f"保存済みDMGがありません: {spec.dmg_path}")
        result["dmg_size_bytes"] = spec.dmg_path.stat().st_size
        expected_sha, sums_url = fetch_published_checksum(spec)
        actual_sha = sha256_file(spec.dmg_path)
        result["checksum"] = {
            "source": sums_url,
            "expected": expected_sha,
            "actual": actual_sha,
            "matched": expected_sha == actual_sha,
        }
        if expected_sha != actual_sha:
            raise InstallerError("保存済みDMGのSHA-256が公式リリースと一致しません")

        dmg_signature = run_command(
            ["/usr/bin/codesign", "--verify", "--strict", "--verbose=2", str(spec.dmg_path)],
            timeout=120,
        )
        result["dmg_codesign"] = {
            "ok": command_ok(dmg_signature),
            "failure_kind": None if command_ok(dmg_signature) else failure_kind(dmg_signature),
            "output": compact_output(dmg_signature),
        }

        mountpoint, detach_target = attach_dmg(spec)
        attached = True
        source = mountpoint / spec.app_name
        if not source.is_dir():
            raise InstallerError(f"DMG内に期待する{spec.app_name}がありません")
        source_check = inspect_bundle(source, spec, allow_dmg_detritus=True)
        result["source_bundle"] = source_check
        if not source_check["verified"]:
            raise InstallerError("DMG内アプリの名前・ID・版・署名・実行許可を確認できません")

        destination_check: dict[str, Any] | None = None
        if spec.destination.exists():
            destination_check = inspect_bundle(spec.destination, spec)
        decision = destination_decision(
            spec.destination.exists(),
            destination_check.get("version") if destination_check else None,
            destination_check.get("verified", False) if destination_check else False,
            spec.version,
        )
        result["existing_destination"] = destination_check
        result["destination_decision"] = decision
        if decision == "skip_verified":
            result["status"] = "skipped_verified"
            result["final_bundle"] = destination_check
        elif decision == "hold_existing_destination":
            result["status"] = "held"
            result["reason"] = "配置先に異なる版または検証できない内容があります"
            return result
        elif mode == "inspect":
            result["status"] = "inspection_verified"
            result["reason"] = "検査モードのためコピーしていません"
            return result
        else:
            DESTINATION.mkdir(parents=True, exist_ok=True)
            stage_parent = run_temp / "stage"
            stage_parent.mkdir(parents=True, exist_ok=True)
            staged = stage_parent / spec.app_name
            copied = run_command(
                [
                    "/usr/bin/ditto",
                    "--norsrc",
                    "--noextattr",
                    "--noacl",
                    str(source),
                    str(staged),
                ],
                timeout=600,
            )
            result["ditto"] = {
                "ok": command_ok(copied),
                "failure_kind": None if command_ok(copied) else failure_kind(copied),
                "output": compact_output(copied),
            }
            if not command_ok(copied):
                raise InstallerError(f"一時領域へのコピーに失敗しました: {compact_output(copied)}")
            staged_check = inspect_bundle(staged, spec)
            result["staged_bundle"] = staged_check
            if not staged_check["verified"]:
                raise InstallerError("コピー後のアプリを検証できないため正式配置しません")
            if spec.destination.exists():
                raise InstallerError("検証中に配置先が作成されたため競合を避けて停止しました")
            os.rename(staged, spec.destination)
            staged = None
            final_check = inspect_bundle(spec.destination, spec)
            result["final_bundle"] = final_check
            if not final_check["verified"]:
                # This run created the destination, so it is safe to pull it back out of the
                # formal location. The finally block removes only this run's staged copy.
                staged = stage_parent / f"failed-{spec.app_name}"
                os.rename(spec.destination, staged)
                result["status"] = "failed_after_promotion"
                result["reason"] = "正式配置後の再検証に失敗したため、配置先から撤回しました"
                return result
            result["status"] = "installed_verified"

        result["app_size_bytes"] = directory_size(spec.destination)
        if do_launch_check and mode == "install" and result["status"] in {
            "installed_verified",
            "skipped_verified",
        }:
            result["launch"] = launch_check(spec)
        return result
    except InstallerError as exc:
        result["status"] = "failed"
        result["reason"] = str(exc)
        return result
    except Exception as exc:  # Per-app isolation; the traceback stays in the console.
        result["status"] = "failed"
        result["reason"] = f"予期しないエラー: {type(exc).__name__}: {exc}"
        return result
    finally:
        if staged is not None and staged.exists():
            shutil.rmtree(staged, ignore_errors=True)
        if attached and detach_target:
            result["detach"] = detach_dmg(detach_target)


def build_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# ArtCraft 外付けSSDインストール結果",
        "",
        f"- 実行ID: `{report['run_id']}`",
        f"- 開始: {report['started_at']}",
        f"- 終了: {report.get('finished_at', '実行中')}",
        f"- モード: `{report['mode']}`",
        "- 容量単位: 10進（1 MB = 1,000,000 bytes、1 GB = 1,000,000,000 bytes）",
        f"- SSD実行前空き: {format_size(report['capacity']['before']['free_bytes'])}",
        f"- SSD実行後空き: {format_size(report['capacity'].get('after', {}).get('free_bytes'))}",
        f"- DMG合計: {format_size(report['capacity']['dmg_total_bytes'])}",
        f"- 配置済みアプリ合計: {format_size(report['capacity'].get('installed_apps_total_bytes'))}",
        "",
        "## アプリ別結果",
        "",
        "| アプリ | 版 | 状態 | SHA-256 | 署名 | Gatekeeper | 起動確認 | 理由 |",
        "|---|---:|---|---|---|---|---|---|",
    ]
    for item in report["apps"]:
        checksum = item.get("checksum", {})
        final = item.get("final_bundle") or item.get("source_bundle") or {}
        launch = item.get("launch", {})
        reason = str(item.get("reason", "")).replace("|", "\\|").replace("\n", " ")
        lines.append(
            "| {app} | {version} | {status} | {sha} | {sign} | {spctl} | {launch} | {reason} |".format(
                app=item["app"],
                version=item.get("expected_version", ""),
                status=item.get("status", ""),
                sha="一致" if checksum.get("matched") else "未確認/不一致",
                sign="検証済み" if final.get("codesign_ok") else "未確認/失敗",
                spctl="許可" if final.get("spctl_ok") else "未確認/拒否",
                launch=launch.get("status", "not_requested"),
                reason=reason,
            )
        )
    lines.extend(["", "## 分類", ""])
    for label, statuses in (
        ("検証済み", {"installed_verified", "skipped_verified", "inspection_verified"}),
        ("失敗", {"failed", "failed_after_promotion"}),
        ("未確認・保留", {"held", "blocked_by_photocraft"}),
    ):
        names = [item["app"] for item in report["apps"] if item.get("status") in statuses]
        lines.append(f"- {label}: {', '.join(names) if names else 'なし'}")
    lines.extend(
        [
            "",
            "## 配置先",
            "",
            f"`{DESTINATION}`",
            "",
            "既存の `/Applications` 版、Launcher、設定、キャッシュ、制作データには変更を加えていません。",
        ]
    )
    return "\n".join(lines) + "\n"


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def save_report(report: dict[str, Any]) -> None:
    json_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    markdown = build_markdown(report)
    run_id = report["run_id"]
    atomic_write(REPORTS / f"{run_id}.json", json_text)
    atomic_write(REPORTS / f"{run_id}.md", markdown)
    atomic_write(REPORTS / "latest.json", json_text)
    atomic_write(REPORTS / "latest.md", markdown)


def selected_apps(only: str | None) -> tuple[AppSpec, ...]:
    if only is None:
        return APPS
    selected = tuple(app for app in APPS if app.name.lower() == only.lower())
    if not selected:
        valid = ", ".join(app.name for app in APPS)
        raise InstallerError(f"対象外のアプリです。指定可能: {valid}")
    return selected


def acquire_lock() -> Any:
    handle = LOCK_PATH.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise InstallerError("別のArtCraft SSDインストーラーが実行中です") from exc
    return handle


def execute(mode: str, only: str | None, do_launch_check: bool) -> int:
    mount_evidence = verified_mount()
    apps = selected_apps(only)
    now = dt.datetime.now().astimezone()
    run_id = f"{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    run_temp = TEMP_ROOT / run_id
    before = disk_usage_snapshot()
    dmg_total = sum(app.dmg_path.stat().st_size for app in apps if app.dmg_path.is_file())
    report: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "started_at": now.isoformat(timespec="seconds"),
        "mode": mode,
        "only": only,
        "launch_check_requested": do_launch_check,
        "volume": mount_evidence,
        "capacity": {"unit": "SI decimal", "before": before, "dmg_total_bytes": dmg_total},
        "apps": [],
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    run_temp.mkdir(parents=True, exist_ok=False)
    save_report(report)
    try:
        for index, spec in enumerate(apps):
            if index > 0 and apps[0].name == "PhotoCraft":
                photo_status = report["apps"][0]["status"]
                if photo_status not in {
                    "installed_verified",
                    "skipped_verified",
                    "inspection_verified",
                }:
                    report["apps"].append(
                        {
                            "app": spec.name,
                            "app_id": spec.app_id,
                            "expected_version": spec.version,
                            "destination": str(spec.destination),
                            "status": "blocked_by_photocraft",
                            "reason": f"PhotoCraft先行検証が{photo_status}のため未実施",
                            "launch": {"status": "not_requested"},
                        }
                    )
                    save_report(report)
                    continue
            print(f"[{index + 1}/{len(apps)}] {spec.name}: {mode}", flush=True)
            app_result = install_or_inspect_app(spec, mode, run_temp, do_launch_check)
            report["apps"].append(app_result)
            save_report(report)
            print(f"  -> {app_result['status']}", flush=True)
    finally:
        report["finished_at"] = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        report["capacity"]["after"] = disk_usage_snapshot()
        app_sizes = [directory_size(app.destination) for app in APPS]
        report["capacity"]["installed_apps_total_bytes"] = sum(
            size for size in app_sizes if size is not None
        )
        remaining = list(run_temp.rglob("*")) if run_temp.exists() else []
        if remaining:
            report["temporary_items_left"] = [str(path) for path in remaining]
        else:
            with contextlib.suppress(OSError):
                run_temp.rmdir()
        save_report(report)
    failed_statuses = {"failed", "failed_after_promotion", "held", "blocked_by_photocraft"}
    return 1 if any(item.get("status") in failed_statuses for item in report["apps"]) else 0


def self_test() -> int:
    checks = {
        "new destination installs": destination_decision(False, None, False, "1.2.3") == "install",
        "verified same version skips": destination_decision(True, "1.2.3", True, "1.2.3") == "skip_verified",
        "same but unverified holds": destination_decision(True, "1.2.3", False, "1.2.3") == "hold_existing_destination",
        "different version holds": destination_decision(True, "1.2.4", True, "1.2.3") == "hold_existing_destination",
        "final matches prerelease numbers conservatively": same_release("1.2.3", "1.2.3-rc.1"),
        "newer comparison": compare_versions("2.0.0", "1.9.9") == 1,
    }
    for label, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'}: {label}")
    return 0 if all(checks.values()) else 1


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("inspect", "install"), default="inspect")
    parser.add_argument("--only", help="12対象の表示名を1本だけ指定（例: PhotoCraft）")
    parser.add_argument(
        "--launch-check",
        action="store_true",
        help="installモードで外付け版を絶対パスから起動してプロセスを確認",
    )
    parser.add_argument("--self-test", action="store_true", help="コピー判断の自己テストのみ実行")
    args = parser.parse_args(list(argv))
    if args.launch_check and args.mode != "install":
        parser.error("--launch-checkは--mode installでのみ使用できます")
    return args


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.self_test:
        return self_test()
    try:
        with acquire_lock():
            return execute(args.mode, args.only, args.launch_check)
    except InstallerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

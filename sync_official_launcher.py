#!/usr/bin/env python3
"""Sync or restore ArtCraft applications in /Applications with external SSD versions."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

EXTERNAL_ROOT = Path("/Volumes/AdobeApps/ArtCraft")
EXTERNAL_APPS = EXTERNAL_ROOT / "Apps"
INTERNAL_APPS = Path("/Applications")
BACKUP_DIR = EXTERNAL_ROOT / "internal_backup"

TARGET_APPS = (
    "CADCraft.app",
    "DeckCraft.app",
    "DesignCraft.app",
    "EffectCraft.app",
    "FilmCraft.app",
    "GridCraft.app",
    "LightCraft.app",
    "PdfCraft.app",
    "PhotoCraft.app",
    "SoundCraft.app",
    "VectorCraft.app",
    "WordCraft.app",
)


def get_status() -> dict[str, dict[str, str]]:
    status: dict[str, dict[str, str]] = {}
    for app_name in TARGET_APPS:
        internal_path = INTERNAL_APPS / app_name
        external_path = EXTERNAL_APPS / app_name
        backup_path = BACKUP_DIR / app_name

        ext_exists = external_path.exists()
        int_exists = internal_path.exists() or internal_path.is_symlink()
        is_link = internal_path.is_symlink()
        link_target = str(internal_path.resolve()) if is_link else None

        if is_link:
            if link_target == str(external_path):
                state = "linked_to_ssd"
            else:
                state = "linked_other"
        elif int_exists:
            state = "internal_regular"
        else:
            state = "missing"

        status[app_name] = {
            "state": state,
            "internal_path": str(internal_path),
            "external_exists": str(ext_exists),
            "backup_exists": str(backup_path.exists()),
            "link_target": str(link_target or ""),
        }
    return status


def print_status() -> None:
    print("=== ArtCraft /Applications 状態 ===")
    status = get_status()
    for app_name, info in status.items():
        state = info["state"]
        if state == "linked_to_ssd":
            desc = "-> [外付けSSDリンク] (公式Launcher利用可能)"
        elif state == "internal_regular":
            desc = "-> [内蔵実体] (内蔵ストレージ消費中)"
        elif state == "missing":
            desc = "-> [未配置]"
        else:
            desc = f"-> [別リンク: {info['link_target']}]"
        print(f"  {app_name:<16}: {desc}")


def link_all() -> int:
    if not EXTERNAL_APPS.is_dir():
        print(f"エラー: 外付けSSDのAppsフォルダが見つかりません: {EXTERNAL_APPS}", file=sys.stderr)
        return 1

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    print("内蔵 /Applications の実体をバックアップし、外付けSSDへのリンクを作成します...")

    for app_name in TARGET_APPS:
        internal_path = INTERNAL_APPS / app_name
        external_path = EXTERNAL_APPS / app_name
        backup_path = BACKUP_DIR / app_name

        if not external_path.is_dir():
            print(f"  スキップ: 外付けSSDに {app_name} がありません")
            continue

        if internal_path.is_symlink():
            current_target = internal_path.resolve()
            if current_target == external_path:
                print(f"  確認済み: {app_name} はすでに外付けSSDにリンクされています")
                continue
            internal_path.unlink()

        elif internal_path.is_dir():
            if not backup_path.exists():
                print(f"  退避中: {app_name} -> {backup_path}")
                shutil.move(str(internal_path), str(backup_path))
            else:
                print(f"  既存バックアップあり: {app_name} を退避領域に上書きせず削除します")
                shutil.rmtree(internal_path)

        # Create symlink
        os.symlink(external_path, internal_path)
        print(f"  リンク完了: {internal_path} -> {external_path}")

    print("\n完了: 公式 ArtCraft Launcher から外付けSSDのアプリを直接利用できるようになりました！")
    return 0


def restore_all() -> int:
    print("外付けSSDへのリンクを解除し、内蔵実体（バックアップ）を復元します...")
    for app_name in TARGET_APPS:
        internal_path = INTERNAL_APPS / app_name
        backup_path = BACKUP_DIR / app_name

        if internal_path.is_symlink():
            internal_path.unlink()
            print(f"  リンク削除: {internal_path}")

        if backup_path.is_dir() and not internal_path.exists():
            shutil.move(str(backup_path), str(internal_path))
            print(f"  復元完了: {backup_path} -> {internal_path}")
        elif not internal_path.exists():
            print(f"  警告: {app_name} のバックアップがありません")

    print("\n復元完了: 内蔵実体に戻りました。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--link", action="store_true", help="内蔵アプリを外付けSSDへのシンボリックリンクに切り替える")
    group.add_argument("--restore", action="store_true", help="外付けリンクを解除し、内蔵実体に復元する")
    group.add_argument("--status", action="store_true", help="現在のリンク状態を表示する")
    args = parser.parse_args()

    if args.link:
        return link_all()
    elif args.restore:
        return restore_all()
    else:
        print_status()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

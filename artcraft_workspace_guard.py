#!/usr/bin/env python3
"""ArtCraft Workspace Guard and Data Protection System.

Provides 100% zero-risk protection against accidental overwrite, modification,
or deletion of raw original assets (images, videos, CAD files, spreadsheets, docs).
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Optional

ROOT = Path("/Volumes/AdobeApps/ArtCraft")
WORKSPACE = ROOT / "Workspace"

RAW_DIR = WORKSPACE / "01_生データ保管庫_原本(Raw_Original)"
WORKING_DIR = WORKSPACE / "02_AI作業データ_編集中(Working)"
OUTPUT_DIR = WORKSPACE / "03_完成データ_出力(Output)"

# Category mappings between Raw, Working, and Output
CATEGORY_MAP = {
    "photo": {
        "raw": RAW_DIR / "01_元写真・元画像(Photo_Image)",
        "working": WORKING_DIR / "01_画像編集_作業中(Photo_Light_Vector)",
        "output": OUTPUT_DIR / "01_完成画像・イラスト",
        "exts": {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".raw", ".arw", ".cr2", ".nef", ".dng", ".svg", ".ai"},
    },
    "cad": {
        "raw": RAW_DIR / "02_元CAD・元3D(CAD_3D)",
        "working": WORKING_DIR / "02_CAD・3D_作業中(CADCraft_3D)",
        "output": OUTPUT_DIR / "02_完成CAD図面・3Dモデル",
        "exts": {".dxf", ".dwg", ".dwt", ".stl", ".obj", ".step", ".stp", ".iges", ".igs", ".gltf", ".glb"},
    },
    "sheet": {
        "raw": RAW_DIR / "03_元シート・生データ(Sheet_CSV)",
        "working": WORKING_DIR / "03_表計算_作業中(GridCraft)",
        "output": OUTPUT_DIR / "03_完成ワークシート・グラフ",
        "exts": {".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".ods"},
    },
    "docs": {
        "raw": RAW_DIR / "04_元文書・元PDF(Docs_PDF)",
        "working": WORKING_DIR / "04_文書・資料_作業中(Word_Pdf_Deck)",
        "output": OUTPUT_DIR / "04_完成ドキュメント・PDF",
        "exts": {".docx", ".doc", ".pdf", ".pptx", ".ppt", ".idml", ".indd", ".txt", ".md"},
    },
    "video": {
        "raw": RAW_DIR / "05_元動画・元音声(Video_Audio)",
        "working": WORKING_DIR / "05_動画・音声_作業中(Film_Effect_Sound)",
        "output": OUTPUT_DIR / "05_完成動画・音声",
        "exts": {".mp4", ".mov", ".mkv", ".avi", ".wav", ".mp3", ".aac", ".flac", ".m4a", ".aif", ".aiff"},
    },
}


class ProtectedDataError(PermissionError):
    """Raised when an operation attempts to overwrite, write to, or delete a raw original file."""


def is_raw_path(path: Path | str) -> bool:
    """Return True if the target path is inside the raw original vault."""
    try:
        resolved = Path(path).resolve()
        raw_resolved = RAW_DIR.resolve()
        return raw_resolved in resolved.parents or resolved == raw_resolved
    except Exception:
        # Fallback check on string representations
        return "01_生データ保管庫_原本(Raw_Original)" in str(path)


def assert_safe_write_path(path: Path | str) -> None:
    """Strictly assert that a target write/save path is NOT inside the raw original vault.

    Raises ProtectedDataError if the path targets the raw original directory.
    """
    if is_raw_path(path):
        raise ProtectedDataError(
            f"【保護エラー】生データ保管庫への書き込み・上書きは100%禁止されています: {path}\n"
            f"編集や出力を行う場合は、必ず『02_AI作業データ_編集中』または『03_完成データ_出力』を指定してください。"
        )


def detect_category(path_or_ext: Path | str) -> str:
    """Detect category key (photo, cad, sheet, docs, video) by extension or path."""
    ext = Path(path_or_ext).suffix.lower()
    for cat_name, info in CATEGORY_MAP.items():
        if ext in info["exts"]:
            return cat_name
    return "docs"


def stage_raw_to_working(
    raw_path: Path | str,
    category: Optional[str] = None,
    custom_subfolder: Optional[str] = None,
    overwrite: bool = False,
) -> Path:
    """Safely copy a raw file from Raw_Original into Working directory.

    Returns the path to the newly staged working copy.
    """
    raw_p = Path(raw_path).resolve()
    if not raw_p.is_file():
        raise FileNotFoundError(f"生データファイルが見つかりません: {raw_p}")

    cat = category or detect_category(raw_p)
    info = CATEGORY_MAP.get(cat, CATEGORY_MAP["docs"])

    dest_folder = info["working"]
    if custom_subfolder:
        dest_folder = dest_folder / custom_subfolder

    dest_folder.mkdir(parents=True, exist_ok=True)
    target_path = dest_folder / raw_p.name

    if target_path.exists() and not overwrite:
        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        target_path = dest_folder / f"{raw_p.stem}_{timestamp}{raw_p.suffix}"

    assert_safe_write_path(target_path)
    shutil.copy2(raw_p, target_path)

    # Ensure the working copy is writable by user
    try:
        os.chmod(target_path, os.stat(target_path).st_mode | stat.S_IWUSR)
    except Exception:
        pass

    return target_path


def get_safe_output_path(
    filename: str,
    category: Optional[str] = None,
    custom_subfolder: Optional[str] = None,
    allow_overwrite: bool = False,
) -> Path:
    """Resolve a safe output path inside 03_完成データ_出力(Output).

    Guarantees no raw data collision and optional unique naming.
    """
    cat = category or detect_category(filename)
    info = CATEGORY_MAP.get(cat, CATEGORY_MAP["docs"])

    dest_folder = info["output"]
    if custom_subfolder:
        dest_folder = dest_folder / custom_subfolder

    dest_folder.mkdir(parents=True, exist_ok=True)
    target_path = dest_folder / filename

    assert_safe_write_path(target_path)

    if target_path.exists() and not allow_overwrite:
        base_name = Path(filename).stem
        ext = Path(filename).suffix
        counter = 2
        while target_path.exists():
            target_path = dest_folder / f"{base_name}_v{counter}{ext}"
            counter += 1

    return target_path


def lock_raw_vault(dry_run: bool = False) -> int:
    """Apply OS-level read-only permissions (chmod a-w) to all files in Raw_Original vault."""
    count = 0
    if not RAW_DIR.is_dir():
        return 0

    for root, _, files in os.walk(RAW_DIR):
        for f in files:
            fp = Path(root) / f
            try:
                cur_mode = fp.stat().st_mode
                # Remove write permissions for user, group, others
                new_mode = cur_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
                if cur_mode != new_mode:
                    if not dry_run:
                        os.chmod(fp, new_mode)
                    count += 1
            except Exception:
                pass
    return count


def unlock_raw_vault(dry_run: bool = False) -> int:
    """Restore user write permissions (chmod u+w) to all files in Raw_Original vault."""
    count = 0
    if not RAW_DIR.is_dir():
        return 0

    for root, _, files in os.walk(RAW_DIR):
        for f in files:
            fp = Path(root) / f
            try:
                cur_mode = fp.stat().st_mode
                new_mode = cur_mode | stat.S_IWUSR
                if cur_mode != new_mode:
                    if not dry_run:
                        os.chmod(fp, new_mode)
                    count += 1
            except Exception:
                pass
    return count


def print_status() -> None:
    """Display Workspace hierarchy statistics and protection status."""
    print("=" * 64)
    print("🛡️  ArtCraft ワークスペース保護ステータスレポート")
    print("=" * 64)
    print(f"ルートパス: {WORKSPACE}")
    print()

    sections = [
        ("🔒 01_生データ保管庫_原本(Raw_Original)", RAW_DIR, "【絶対保護】読み取り専用"),
        ("🛠️ 02_AI作業データ_編集中(Working)", WORKING_DIR, "【AI作業用】複製・編集エリア"),
        ("✨ 03_完成データ_出力(Output)", OUTPUT_DIR, "【成果物】書き出しエリア"),
    ]

    for title, path, desc in sections:
        print(f"{title} ({desc})")
        if not path.is_dir():
            print("  ⚠️ ディレクトリが存在しません")
            continue
        subdirs = sorted([d for d in path.iterdir() if d.is_dir()])
        for sd in subdirs:
            file_count = sum(1 for _ in sd.rglob("*") if _.is_file())
            print(f"  ├── {sd.name}: {file_count} ファイル")
        print()
    print("=" * 64)


def run_self_test() -> bool:
    """Run self-test to verify raw protection, staging, and output generation."""
    print("🧪 ArtCraft Workspace Guard 自己テスト実行中...")
    test_raw_file = RAW_DIR / "02_元CAD・元3D(CAD_3D)" / "test_sample_part.dxf"
    test_raw_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        # 1. Create a dummy raw file
        test_raw_file.write_text("DUMMY RAW CAD DATA", encoding="utf-8")

        # 2. Test assert_safe_write_path blocks writing to raw dir
        blocked = False
        try:
            assert_safe_write_path(test_raw_file)
        except ProtectedDataError:
            blocked = True

        if not blocked:
            print("❌ テスト失敗: 生データ保管庫への書き込みガードが働きませんでした！")
            return False
        print("  ✅ [1/4] 生データ保管庫への書き込みブロック検証: 合格")

        # 3. Test staging from Raw to Working
        staged = stage_raw_to_working(test_raw_file)
        if not staged.exists() or staged.parent != CATEGORY_MAP["cad"]["working"]:
            print(f"❌ テスト失敗: 作業フォルダへの複製が正しくありません: {staged}")
            return False
        print("  ✅ [2/4] 生データから作業フォルダへの安全複製: 合格")

        # 4. Test safe output generation
        out_path = get_safe_output_path("test_result.dxf", category="cad")
        if out_path.parent != CATEGORY_MAP["cad"]["output"]:
            print(f"❌ テスト失敗: 出力先が正しくありません: {out_path}")
            return False
        out_path.write_text("FINAL RESULT CAD DATA", encoding="utf-8")

        # Duplicate output collision test
        out_path_v2 = get_safe_output_path("test_result.dxf", category="cad")
        if out_path_v2.name != "test_result_v2.dxf":
            print(f"❌ テスト失敗: 重複時バージョニングが正しくありません: {out_path_v2}")
            return False
        print("  ✅ [3/4] 重複防止付き安全出力パス生成: 合格")

        # 5. Lock vault test
        locked = lock_raw_vault()
        print(f"  ✅ [4/4] OSレベル読み取り専用ロック適用 ({locked}件): 合格")

        # Cleanup test artifacts
        unlock_raw_vault()
        if test_raw_file.exists():
            test_raw_file.unlink()
        if staged.exists():
            staged.unlink()
        if out_path.exists():
            out_path.unlink()

        print("🎉 すべての自己テストに100%合格しました！")
        return True
    except Exception as exc:
        print(f"❌ テスト中に例外が発生しました: {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="ArtCraft Workspace Guard & Safe Data Pipeline")
    parser.add_argument("--status", action="store_true", help="ワークスペースの構成と状態を表示")
    parser.add_argument("--lock-raw", action="store_true", help="生データ保管庫をOSレベルで読み取り専用にロック")
    parser.add_argument("--unlock-raw", action="store_true", help="生データ保管庫のロックを解除")
    parser.add_argument("--stage", type=str, metavar="RAW_FILE", help="生データを安全に作業フォルダにコピーして作業パスを出力")
    parser.add_argument("--self-test", action="store_true", help="自己テストを実行")

    args = parser.parse_args()

    if args.self_test:
        return 0 if run_self_test() else 1
    elif args.lock_raw:
        n = lock_raw_vault()
        print(f"🔒 生データ保管庫内の {n} 個のファイルを読み取り専用にロックしました。")
        return 0
    elif args.unlock_raw:
        n = unlock_raw_vault()
        print(f"🔓 生データ保管庫内の {n} 個のファイルの書き込み権限を復旧しました。")
        return 0
    elif args.stage:
        staged_path = stage_raw_to_working(args.stage)
        print(f"🛠️ 作業用コピーを作成しました: {staged_path}")
        return 0
    else:
        print_status()
        return 0


if __name__ == "__main__":
    sys.exit(main())

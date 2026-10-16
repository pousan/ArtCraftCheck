# ArtCraft 外付けSSD プロジェクト作業記録 & メモリ (memory.md)

本ドキュメントは、これまでの作業経緯、発生した課題・失敗とその根本原因、解決策、および次回作業をスムーズに再開するための引き継ぎ事項をまとめた記録です。

> **💡 AI・人間共通の読み込みルール**:
> 本ドキュメントが長大化してメモリやコンテキストを浪費するのを防ぐため、必ず以下の**目次**を参照し、必要なセクション（行範囲）のみを部分読み込みしてください。

---

## 📑 目次 (Table of Contents)

1. [プロジェクトの概要と目的](#1-プロジェクトの概要と目的)
2. [課題・失敗と知見（トラブルシューティング記録）](#2-今回発生した課題失敗と得られた知見トラブルシューティング記録)
   - [① hdiutil attach の権限エラー](#①-hdiutil-attach--mountpoint-の権限エラー)
   - [② DMG内コード署名検証エラー（FinderInfo detritus）](#②-dmg内での-codesign---strict-失敗finderinfo-detritus-エラー)
   - [③ 公式ランチャーが開かない問題（トレイ常駐）](#③-公式ランチャーがクリックしても開かない問題)
   - [④ Python 3.14 の Tkinter 未搭載](#④-homebrew-python-314-の-_tkinter-未搭載)
3. [現在のシステム構成とファイル配置](#3-現在のシステム構成とファイル配置)
   - [ディレクトリ構造](#ディレクトリ構造-volumesadobeappsartcraft)
   - [現在の連携状態](#現在の連携状態)
4. [よく使うコマンド集（チートシート）](#4-よく使うコマンド集)
5. [GitHub 自動更新システム（Auto-Updater）の実装と知見](#5-github-自動更新システムauto-updaterの実装と知見)
6. [2週間定期ポップアップと全自動ライブ進行画面（コマンドレス運用）](#6-2週間定期ポップアップと全自動ライブ進行画面コマンドレス運用)

---

## 1. プロジェクトの概要と目的

- **対象**: ArtCraft シリーズの macOS 向け 12 アプリ
  (`CADCraft`, `DeckCraft`, `DesignCraft`, `EffectCraft`, `FilmCraft`, `GridCraft`, `LightCraft`, `PdfCraft`, `PhotoCraft`, `SoundCraft`, `VectorCraft`, `WordCraft`)
- **目的**: 
  1. Mac 内蔵ストレージ容量（約 2GB）を解放するため、全アプリを外付け SSD（`/Volumes/AdobeApps/ArtCraft/Apps`）に配置する。
  2. macOS のセキュリティ検証（Apple 公証・コード署名）を 100% 通過させ、壊れや認証拒否を起こさずに安定動作させる。
  3. 公式ランチャー（`ArtCraft Launcher.app`）や外付け専用ランチャーからワンクリックで外付けアプリを直接起動できるようにする。

---

## 2. 今回発生した課題・失敗と得られた知見（トラブルシューティング記録）

### ① `hdiutil attach -mountpoint` の権限エラー
- **事象**: 
  スクリプト実行時、`hdiutil attach -nobrowse -readonly -mountpoint <一時パス> ...` で `hdiutil: attach failed - アクセス権がありません` が発生して停止した。
- **根本原因**: 
  macOS の近年のバージョンでは、一般ユーザー権限（非 root）で `mount(2)` 相当のカスタムマウントポイントを特定ディレクトリに強制指定することがセキュリティ上制限されている。
- **解決策**: 
  `-mountpoint` の指定をやめ、macOS 標準の自動マウント方式（`hdiutil attach -nobrowse -readonly -plist ...`）に変更。返される plist から `system-entities` の `mount-point`（`/Volumes/<ボリューム名>`）を取得してアクセス・アンマウントするように改修した。

---

### ② DMG内での `codesign --strict` 失敗（FinderInfo detritus エラー）
- **事象**: 
  DMG 内のアプリに対して厳格なコード署名検証（`codesign --verify --deep --strict`）を実行すると、以下のように失敗した。
  `resource fork, Finder information, or similar detritus not allowed`
  （※これが以前 Codex 等とのやり取りで「認証が取れない」と言われていた問題の正体）
- **根本原因**: 
  公式 DMG 作成時に macOS の Finder が付与した `com.apple.FinderInfo` などの拡張属性（xattr）が読み取り専用 DMG 内に残っており、`--strict` モードの detritus チェックに引っかかっていた。ただし、Apple の Gatekeeper（`spctl --assess`）は「accepted（合格）」であり、Team ID `DJ6XS33FX8` の公証も正常だった。
- **解決策**: 
  公式 Launcher と同様に `ditto --norsrc --noextattr --noacl` を用いて外付け SSD にコピーすることで、不要な `FinderInfo` を完全に除去。
  コピー後の実体で `codesign --strict` を再検証したところ、エラーなく 100% 合格（satisfies its Designated Requirement）することを確認・実証した。

---

### ③ 公式ランチャーが「クリックしても開かない」問題
- **事象**: 
  内蔵 `/Applications` の実体を外付けへのシンボリックリンクに切り替えた後、公式 `ArtCraft Launcher.app` をクリックしても画面が立ち上がらない状態になった。
- **根本原因**: 
  公式ランチャーの設定（`~/Library/Application Support/ai.storyteller.artcraft-launcher/app.ron`）で `close_to_tray: true`（メニューバー常駐）がデフォルトで有効になっていた。
  以前に起動された古いプロセス（PID `96031`）が画面上部のメニューバー／トレイに潜んだままバックグラウンドで生き残っており、多重起動防止によって新しい画面が開かなくなっていた。
- **解決策**: 
  1. 滞留していた古いランチャープロセスを `killall` で完全終了。
  2. `app.ron` の設定を `close_to_tray: false` に変更。
  3. これにより、ウィンドウを閉じればプロセスも綺麗に終了し、Dock や Finder からクリックした時に毎回確実にメイン画面が開くようになった。
  4. 起動後、WordCraft を起動したところ、外付け SSD の実体（`/Volumes/AdobeApps/ArtCraft/Apps/WordCraft.app`）から正常にプロセス（PID `6215`）が起動していることを確認。

---

### ④ Homebrew Python 3.14 の `_tkinter` 未搭載
- **事象**: 
  ネイティブ風 GUI ランチャーを作る際に `import tkinter` を試行したが、`ModuleNotFoundError: No module named '_tkinter'` となった。
- **根本原因**: 
  macOS の Homebrew 版 Python 3.14 には Tkinter が標準同梱されていない。
- **解決策**: 
  追加の Homebrew パッケージ導入をユーザーに強いるのを避け、Python 標準の HTTP サーバー＋モダンな HTML5/CSS3（グラスモーフィズムデザイン）のダッシュボード方式を採用。さらに macOS の `.app` バンドル（`ArtCraft Launcher (SSD).app`）としてパッケージ化した。

---

## 3. 現在のシステム構成とファイル配置

### ディレクトリ構造 (`/Volumes/AdobeApps/ArtCraft`)
```
/Volumes/AdobeApps/ArtCraft/
├── Apps/                             # 12本のアプリ実体（すべて検証・配置済み）
│   ├── CADCraft.app
│   ├── DeckCraft.app
│   ├── DesignCraft.app
│   ├── EffectCraft.app
│   ├── FilmCraft.app
│   ├── GridCraft.app
│   ├── LightCraft.app
│   ├── PdfCraft.app
│   ├── PhotoCraft.app
│   ├── SoundCraft.app
│   ├── VectorCraft.app
│   └── WordCraft.app
│
├── Installers/                       # 公式 universal DMG (全12本) & SHA256SUMS.txt
├── internal_backup/                  # 内蔵 /Applications から安全に退避した元実体
│
├── ArtCraft Launcher (SSD).app       # 外付け専用ダッシュボードランチャー
├── .launcher-assets/                 # ランチャー用 UI (HTML/アイコン画像等)
├── launcher_server.py                # ランチャー用軽量バックエンドサーバー
├── sync_official_launcher.py         # 公式Launcher連携・切り替えスクリプト
├── artcraft_ssd_installer.py         # 検証済みインストーラースクリプト
├── test_artcraft_ssd_installer.py    # 自己テストスイート
├── reports/                          # 実行詳細レポート (latest.md / latest.json)
├── 使い方.md                          # 全体マニュアル
├── 議事録_20261016.md                 # 2026-10-16 作業議事録
└── memory.md                         # 本作業記憶ファイル
```

### 現在の連携状態
- `/Applications/<アプリ名>.app` → `/Volumes/AdobeApps/ArtCraft/Apps/<アプリ名>.app` へのシンボリックリンクが成立。
- 公式 `ArtCraft Launcher.app` から 12 本すべてが「インストール済み」として認識され、起動可能。
- 内蔵実体は `internal_backup/` に保持されており、完全可逆（いつでも元に戻せる状態）。

---

## 4. よく使うコマンド集

```sh
cd /Volumes/AdobeApps/ArtCraft

# 公式ランチャー連携状態の確認
python3 sync_official_launcher.py --status

# 外付けSSDリンクへの切り替え（内蔵容量節約）
python3 sync_official_launcher.py --link

# 内蔵実体への完全復元
python3 sync_official_launcher.py --restore

# インストーラーの自己テスト実行
python3 artcraft_ssd_installer.py --self-test

# GitHubリリースの更新確認（キャッシュ利用・高速）
python3 artcraft_updater.py --check

# 更新があるアプリのみを安全自動アップデート（差分更新）
python3 artcraft_updater.py --update --notify

# 日次自動更新監視（LaunchAgent）の登録・解除
python3 artcraft_updater.py --install-agent
python3 artcraft_updater.py --uninstall-agent
```

---

## 5. GitHub 自動更新システム（Auto-Updater）の実装と知見

2026-10-16 に実装完了。

### ① GitHub API レートリミット（認証なし 60回/時）の回避
- **課題**: 12本すべてのリリースを毎回問い合わせると、短時間でGitHub APIの上限に達してエラーになるリスク。
- **解決策**:
  1. **ローカルキャッシュ（TTL 1時間）**: `.updater_cache.json` に最新リリース情報を保存し、2回目以降は即座に判定（0.5秒で完了）。
  2. **ETag（`If-None-Match`）サポート**: キャッシュ期限切れ時も、変更がなければ HTTP 304 Not Modified で帯域・リクエスト消費を最小化。
  3. **環境変数 `GITHUB_TOKEN`**: トークンが設定されている場合はヘッダーに自動付与し、上限5,000回/時まで拡張可能。

### ② 差分更新（Delta Upgrade）と安全ロールバック保証
- **課題**: アプリのダウンロードや配置の途中でネットワーク断やSSD切断が発生した場合のアプリ破損リスク。
- **解決策**:
  - 新バージョンがあるアプリのDMGのみをダウンロード。
  - `SHA256SUMS.txt` による事前照合 → 読み取り専用マウント → Apple署名＆公証チェック。
  - 配置直前に既存の旧アプリを `Apps_backup/` へ一時リネーム退避。
  - 新バージョンの配置または最終検証が失敗した場合は、即座に旧バージョンへ自動ロールバック。
  - 内蔵 `/Applications` へのシンボリックリンク整合性を自動検証・修復。

### ③ デスクトップ通知連携（macOS Notification）
- `osascript` 経由で macOS 標準の通知センターへバナー通知（`Glass` 音付き）。
- 更新完了時には成功/失敗件数とサウンド（`Hero` / `Basso`）でユーザーにフィードバック。

### ④ 外付けランチャーUI統合
- `ArtCraft Launcher (SSD).app`（HTML5ダッシュボード）に「🔄 アップデート確認」ボタンとモーダルを追加。
- 更新があるアプリのカードには黄色いバッジ（`▲ vX.X.X`）と「更新」ボタンを動的に表示。

---

## 6. 2週間定期ポップアップと全自動ライブ進行画面（コマンドレス運用）

2026-10-16 夜 実装完了。

### ① コマンドレス＆14日タイマー設計
- **状態管理**: `.updater_state.json` に `last_checked_at`, `next_remind_at`, `interval_days: 14` を記録。
- **起動時判定**: ランチャー画面読み込み時に `/api/schedule/status` を呼び出し、14日経過していればモーダル「🔔 定期アップデートのご案内」を自動ポップアップ。
- **スヌーズ対応**: 「あとで通知（次回起動時）」や「スキップ（2週間後）」もワンクリック。

### ② 全自動進行画面（リサーチ ➔ 差分特定 ➔ インストール ➔ 完了）
- **プログレスバー＆ライブグリッド**: スイッチONでバックグラウンドスレッドが起動。12本のリポジトリを1つずつリサーチする様子がアニメーション表示される。
- **差分のみ自動更新**: 更新が必要なアプリのみDMGダウンロード〜安全検証〜配置をシームレスに実行。
- **次回日の自動更新**: 全工程完了時に自動で次回リマインド日（＋14日後）を算出し、完了サマリーを表示。
- **公式ランチャー監視**: `launcher_watcher.py` により、内蔵の公式 `ArtCraft Launcher.app` を開いた際もネイティブダイアログで案内し、外付け更新画面へ誘導可能。



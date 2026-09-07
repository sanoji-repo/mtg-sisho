# 自分で立てる（SETUP.md）

Sisho を自分の PostgreSQL と Python で動かす手順。README から 2026-09-07 に移した（内容は移した時点のまま）。
読み取り専用の公開サーバーを別のマシンに作る手順は [PUBLIC_SERVER.md](./PUBLIC_SERVER.md)。

## 前提条件

- PostgreSQL 18（ローカル・既定ポート 5435）。拡張モジュール `pg_trgm`（曖昧名検索用）が必要。
- Python 3.12・`pip install -r requirements.txt`。MCP SDK（`mcp`）も requirements.txt に含まれる。
- 接続情報は `.env`（`env.example` をコピーして作成）。パスワードはコード内に直書きしない。
- サーバー本体のパスはリポジトリ相対で解決される。一部の更新・バッチスクリプト内に作者環境のパス（`/mnt/new_hdd/...`・`/mnt/mtg_rag`）が残っている箇所があるが、環境変数で上書き可能（各スクリプト冒頭のコメントを参照）。

## DB を構築する

| 順序 | スクリプト | 実行内容 |
| --- | --- | --- |
| 1 | `src/sync_oracle_cards.py --apply` | Scryfall の oracle_cards バルクからカード本体を取り込み・更新（新カード INSERT＋既存 UPDATE）。テーブル定義は DATA_MODEL.md 参照。※前身の初回インポート処理はベクトル埋め込みに依存していたため除外 |
| 2 | `src/add_face_cmcs.py`・`src/add_face_types.py` | 両面・分割カードの「唱えられる面」に関する導出列を計算・追加 |
| 3 | `src/extract_japanese.py` | all_cards バルクから日本語名・日本語本文を抽出（公式翻訳のみ・非公式訳は除外） |
| 4 | `src/enrich_*.py` | 印刷情報・表面キーワード・マナ加速/除去/ドロー/サーチの導出列を付与（すべて冪等） |
| 5 | `src/import_rules.py`・`src/import_rulings.py` | 総合ルールと公式裁定をインポート |
| 6 | `src/import_decks.py`・`src/scrape_mtgtop8.py`・`src/scrape_mtgo.py`・`src/scrape_moxfield.py` | 実デッキデータを取得。取得済みデータは自動スキップ（同一コマンドで差分更新可能） |
| 7 | `src/fix_deck_links.py` → `src/recompute_card_format_strength.py` → `src/build_edh_cooccurrence.py` | デッキのカード名を card_id に紐付け → フォーマット別採用率を計算 → 共起データを生成 |

新セット発売時の一括更新バッチの雛形として `sh/convoy_20260821.sh`（退避 → 取得 → 搬入 → 導出 → 日本語 → 検収）を用意している。
※ `mtg_probability` などの確率計算を利用する場合は、DB 構築後に `sql/prob_functions.sql` を PostgreSQL に投入する必要がある。

## 定期運用・更新

| スクリプト | 実行周期 | 処理内容 |
| --- | --- | --- |
| `sh/nightly_cron_driver.sh` | 毎日 03:00〜10:00 | MTGTop8・MTGO・Moxfield の差分取得を並行実行し、完了後に共起データを全件再集計 |
| `sh/mtgo_backfill_cron.sh` | 4 時間おき（毎時 30 分） | MTGO 公式デッキリストの過去分を 1 便に 1 か月分ずつ遡って取得（キューファイルを 1 行ずつ消化） |
| `sh/weekly_pgdump.sh` | 毎週日曜 00:00 | PostgreSQL の論理バックアップを取得（4世代保持） |
| `src/check_freshness.py` | 手動 | Scryfall・総合ルール・公式裁定のデータ鮮度を確認 |

## MCP サーバーの登録

標準入出力（stdio）で利用する場合の設定例（Claude Code の `.mcp.json` など）:

```json
{
  "mcpServers": {
    "sisho": {
      "command": "/path/to/venv/bin/python",
      "args": ["/path/to/mtg-sisho/src/mcp_server.py"]
    }
  }
}
```

HTTP 経由で常駐させる場合は `deploy/mtg-rag-mcp.service`（systemd の user unit・`127.0.0.1:8765`）を使用する。
エンドポイントの待ち受けパスは環境変数 `MCP_HTTP_PATH` で変更可能（公開ホスト名は証明書の透明性ログ（CTログ）等で露出するため、パスを秘匿して簡易的なアクセス制御としている。認証機能は未実装）。

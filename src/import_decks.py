"""
import_decks.py — MTGJSON AllDeckFiles を PostgreSQL に取り込む
=============================================================
テーブル構成:
  deck_list  ... デッキのメタ情報
  deck_cards ... デッキ内のカード（mainBoard / sideBoard / commander）

source カラムで将来の大会データと区別できる:
  'mtgjson_precon' ... 今回取り込むプリコンデッキ
  'mtgtop8'        ... 将来取り込む大会入賞デッキ（同じテーブルに追加可能）

使い方:
  python import_decks.py            # 全件取り込み
  python import_decks.py --status   # 取り込み状況確認
  （共起は src/build_cooccurrence.py・夜間ジョブで作る）
"""

import argparse
import json
import os
from pathlib import Path

import psycopg2
import psycopg2.extras
from tqdm import tqdm

from db_config import DATA_DIR, DB_CONFIG, ddl_cursor

DECK_DIR = Path(os.environ.get("MTG_DECK_DIR", os.path.join(DATA_DIR, "AllDecks", "AllDeckFiles")))
SOURCE   = "mtgjson_precon"


# ─── テーブル作成 ─────────────────────────────────────────────

def create_tables(conn):
    # DDL は lock_timeout つき（取れなければ自分が失敗して行列を作らない）
    with ddl_cursor(conn) as cur:
        # デッキ情報テーブル
        cur.execute("""
            CREATE TABLE IF NOT EXISTS deck_list (
                id         SERIAL PRIMARY KEY,
                deck_name  TEXT NOT NULL UNIQUE,
                set_code   TEXT,
                source     TEXT NOT NULL DEFAULT 'mtgjson_precon',
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)

        # デッキ内カードテーブル
        cur.execute("""
            CREATE TABLE IF NOT EXISTS deck_cards (
                id        SERIAL PRIMARY KEY,
                deck_id   INTEGER REFERENCES deck_list(id) ON DELETE CASCADE,
                card_name TEXT NOT NULL,
                count     INTEGER NOT NULL DEFAULT 1,
                board     TEXT NOT NULL DEFAULT 'main'
            );
        """)

        # 検索用インデックス
        cur.execute("""
            CREATE INDEX IF NOT EXISTS deck_cards_card_name_idx
            ON deck_cards (card_name);
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS deck_cards_deck_id_idx
            ON deck_cards (deck_id);
        """)


    conn.commit()
    print("テーブル作成完了")


# ─── デッキ取り込み ───────────────────────────────────────────

def extract_cards(board: list, board_name: str) -> list[tuple[str, int, str]]:
    """ボードからカード情報を抽出する"""
    results = []
    for card in board:
        name  = card.get("name", "").strip()
        count = card.get("count", 1)
        if name:
            results.append((name, count, board_name))
    return results


def import_decks(conn):
    deck_files = sorted(DECK_DIR.glob("*.json"))
    total      = len(deck_files)
    print(f"デッキファイル数: {total}")

    imported = 0
    skipped  = 0
    errors   = 0

    for deck_file in tqdm(deck_files, desc="デッキ取り込み", mininterval=5):
        deck_name = deck_file.stem  # ファイル名から拡張子を除いたもの

        try:
            with open(deck_file, "r", encoding="utf-8") as f:
                data = json.load(f).get("data", {})

            set_code = data.get("code", "")

            # カードリスト抽出
            cards: list[tuple[str, int, str]] = []
            cards += extract_cards(data.get("mainBoard", []),      "main")
            cards += extract_cards(data.get("sideBoard", []),      "side")
            cards += extract_cards(data.get("commander", []),      "commander")
            cards += extract_cards(data.get("displayCommander", []), "commander")

            if not cards:
                skipped += 1
                continue

            with conn.cursor() as cur:
                # デッキを INSERT（重複はスキップ）
                cur.execute("""
                    INSERT INTO deck_list (deck_name, set_code, source)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (deck_name) DO NOTHING
                    RETURNING id;
                """, (deck_name, set_code, SOURCE))
                result = cur.fetchone()

                if result is None:
                    skipped += 1
                    continue

                deck_id = result[0]

                # カードを一括 INSERT
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO deck_cards (deck_id, card_name, count, board)
                    VALUES %s
                    """,
                    [(deck_id, name, count, board) for name, count, board in cards],
                )

            conn.commit()
            imported += 1

        except Exception as e:
            conn.rollback()
            errors += 1
            if errors <= 5:
                print(f"  エラー: {deck_file.name} — {e}")

    print(f"\n完了: {imported} 件取り込み / {skipped} 件スキップ / {errors} 件エラー")


# ─── 状況確認 ─────────────────────────────────────────────────

def check_status(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM deck_list")
        deck_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(*) FROM deck_cards")
        card_count = cur.fetchone()[0]

        cur.execute("""
            SELECT source, COUNT(*) FROM deck_list GROUP BY source
        """)
        by_source = cur.fetchall()


    print(f"デッキ数:     {deck_count:,}")
    print(f"カード総行数: {card_count:,}")
    print(f"\nソース別:")
    for source, count in by_source:
        print(f"  {source}: {count}")



# ─── エントリーポイント ───────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--status",  action="store_true", help="取り込み状況確認")
    args = parser.parse_args()

    conn = psycopg2.connect(**DB_CONFIG)
    create_tables(conn)

    if args.status:
        check_status(conn)
    else:
        import_decks(conn)

    conn.close()

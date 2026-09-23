"""
scrape_mtgtop8.py — MTGTop8 大会デッキスクレイパー
====================================================
礼儀正しいスクレイピング:
  - リクエスト間隔: 2秒
  - User-Agent を明示
  - 中断・再開対応（取得済みイベントはスキップ）

取得フロー:
  1. /format?f={FORMAT}&meta={META} → イベントID一覧
  2. /event?e={EVENT_ID}            → デッキID一覧
  3. /dec?d={DECK_ID}               → カードリスト（.dec形式）

テーブル構成:
  deck_list  （既存・source='mtgtop8' で追加）
  deck_cards （既存・同上）

使い方:
  # モダン 2024年分を取得
  python scrape_mtgtop8.py --format MO --meta 276 --year 2024

  # スタンダード 2024年分
  python scrape_mtgtop8.py --format ST --meta 276 --year 2024

  # 取得状況確認
  python scrape_mtgtop8.py --status

フォーマットコード:
  ST=Standard, PI=Pioneer, MO=Modern, LE=Legacy, VI=Vintage
  PAU=Pauper, EDH=Duel Commander

メタコード（モダン）:
  276=2024, 315=2025, 246=2023, 236=2022

メタコード（Duel Commander）:
  343=2026, 310=2025, 283=2024, 252=2023

EDH（Duel Commander）の特記:
  - .dec に統率者専用マーカーは無く、統率者は「SB:」行で出力される
    （Duel Commander にサイドボードは無いため SB: 行＝統率者。共闘は2行）。
    そのため EDH では SB: 行を board='commander' として保存する。
  - source は 'mtgtop8_edh' に自動分離（既存4フォーマットの 'mtgtop8' とは別系統）。
"""

import argparse
import html as html_lib
import re
import time
import psycopg2
import psycopg2.extras
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm

from db_config import DB_CONFIG, connect_scrape, read_cursor

BASE_URL       = "https://www.mtgtop8.com"
REQUEST_INTERVAL = 2.75  # 秒（礼儀正しいスクレイピング・夜間実行では 2.5〜3.0 秒を指定）
SOURCE         = "mtgtop8"

HEADERS = {
    "User-Agent": "MTG-RAG-Research-Bot/1.0 (educational project; contact via GitHub)",
    "Accept-Language": "en-US,en;q=0.9",
}

FORMAT_NAMES = {
    "ST": "Standard", "PI": "Pioneer", "MO": "Modern",
    "LE": "Legacy",   "VI": "Vintage", "PAU": "Pauper",
    "EDH": "Duel Commander",
}


# ─── HTTP ヘルパー ────────────────────────────────────────────

def fetch(url: str, retries: int = 2) -> str | None:
    """礼儀正しい HTTP GET（インターバル付き・リトライあり）"""
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            time.sleep(REQUEST_INTERVAL)
            if resp.status_code == 200:
                return resp.text
            print(f"  HTTP {resp.status_code}: {url}")
            return None
        except requests.RequestException as e:
            print(f"  通信エラー（{attempt+1}/{retries}）: {e}")
            time.sleep(REQUEST_INTERVAL * 2)
    return None


# ─── スクレイピング ───────────────────────────────────────────

def get_event_ids(format_code: str, meta: int) -> list[int]:
    """イベント一覧ページからイベントIDを取得する"""
    url  = f"{BASE_URL}/format?f={format_code}&meta={meta}&a="
    html = fetch(url)
    if not html:
        return []

    # href=event?e=XXXX の数字を抽出
    event_ids = re.findall(r'href=["\']?event\?e=(\d+)', html)
    unique_ids = list(dict.fromkeys(int(e) for e in event_ids))
    return unique_ids


def get_deck_ids(event_id: int) -> tuple[str, str | None, list[tuple[int, str]]]:
    """
    イベントページからイベント名・大会日・デッキIDを取得する。
    戻り値: (event_name, event_date_iso_or_None, [(deck_id, deck_name), ...])

    プレイヤー名は取らない（下の設計判断の注記を参照）。
    """
    url  = f"{BASE_URL}/event?e={event_id}"
    html = fetch(url)
    if not html:
        return f"Event {event_id}", None, []

    soup = BeautifulSoup(html, "html.parser")

    # イベント名を取得
    title_div = soup.find("div", class_="event_title")
    event_name = title_div.get_text(strip=True) if title_div else f"Event {event_id}"

    # 大会日を取得（"465 players - 30/12/24" 形式・MTGTop8開設以降のため 20xx 固定で安全）
    event_date = None
    date_m = re.search(r'\d+\s+players?\s*-\s*(\d{2})/(\d{2})/(\d{2})', html)
    if not date_m:
        # 参加者数が出ないイベント（小規模大会など）は "players" 行が無い。
        # その場合は "Source" より前に現れる最初の日付を拾う（後ろには引用元の日付が混ざる）。
        head = html.split('Source', 1)[0]
        date_m = re.search(r'(\d{2})/(\d{2})/(\d{2})\b', head)
    if date_m:
        dd, mm, yy = date_m.groups()
        event_date = f"20{yy}-{mm}-{dd}"

    # デッキIDとデッキ名を取得
    # パターン: href=?e=1&d=101680&f=VI または href=event?e=1&d=101680&f=VI
    deck_links = re.findall(
        r'href=["\']?\?e=\d+&d=(\d+)&f=[A-Z]+["\']?[^>]*>([^<]+)<',
        html
    )

    results = []
    for deck_id, deck_name in deck_links:
        # リンクテキストは HTML 実体参照のことがある（&rarr; ＝未分類の矢印表示・
        # 1,356 本が番兵値として archetype に混入していた虫）。
        # 実体を解いた上で、矢印だけの「名無し」は空にする＝下流の or None で NULL 化。
        name = html_lib.unescape(deck_name).strip()
        if name in {"→", "←"}:
            name = ""
        results.append((int(deck_id), name))

    return event_name, event_date, results


def parse_dec(dec_text: str, sb_board: str = "side") -> tuple[str, str, list[tuple[str, int, str]]]:
    """
    .dec テキストをパースしてカードリストを返す。
    戻り値: (deck_name, format_name, [(card_name, count, board), ...])
    sb_board: SB: 行に割り当てる board 値。EDH では統率者が SB: 行で
              出力されるため 'commander' を渡す（既定は 'side' で従来どおり）。
    """
    deck_name   = ""
    format_name = ""
    cards       = []

    for line in dec_text.splitlines():
        line = line.strip()
        if not line:
            continue

        # コメント行からメタ情報を抽出
        if line.startswith("//"):
            if "NAME" in line:
                deck_name = line.split(":", 1)[-1].strip()
            elif "FORMAT" in line:
                format_name = line.split(":", 1)[-1].strip()
            continue

        # サイドボード（EDH では統率者行）
        if line.upper().startswith("SB:"):
            line  = line[3:].strip()
            board = sb_board
        else:
            board = "main"

        # "4 [MR] Counterspell" または "4 Counterspell"
        # 注意: セット記号は空括弧 "[]" のこともある（クライアントの MCP 検分が発見）。
        # 旧正規表現は [^\]]+ （1 文字以上）だったため "4 [] Counterspell" の空括弧を
        # 読み飛ばせず「[] Counterspell」として 20 万行/1 万デッキが汚染された。* に修正。
        m = re.match(r'^(\d+)\s+(?:\[[^\]]*\]\s+)?(.+)$', line)
        if m:
            count     = int(m.group(1))
            card_name = m.group(2).strip()
            # 分割カード "Fire // Ice" → "Fire // Ice" のままで OK
            cards.append((card_name, count, board))

    return deck_name, format_name, cards


def get_deck_cards(deck_id: int, sb_board: str = "side") -> list[tuple[str, int, str]]:
    """デッキIDから .dec を取得してカードリストを返す"""
    url      = f"{BASE_URL}/dec?d={deck_id}"
    dec_text = fetch(url)
    if not dec_text:
        return []
    _, _, cards = parse_dec(dec_text, sb_board=sb_board)
    return cards


# ─── DB 操作 ──────────────────────────────────────────────────

def get_scraped_event_ids(conn, source: str = SOURCE) -> set[int]:
    """既に取り込み済みのイベントIDを取得（再開用）"""
    # 読んだら閉じる（この後 HTTP を叩いている間ロックを握らない）
    with read_cursor(conn) as cur:
        cur.execute("""
            SELECT DISTINCT tournament_event_id FROM deck_list
            WHERE source = %s AND tournament_event_id IS NOT NULL
        """, (source,))
        return {row[0] for row in cur.fetchall()}


# 設計判断: この取り込みはプレイヤー名を持たない。deck_list に
# player_name 列は無く、名前を隔離する players 表は開発側だけに置く。
# 集めてから捨てるのではなく、最初から取らない。
_REQUIRED_COLUMNS = ("tournament_name", "tournament_date", "placement",
                     "format_name", "source_url", "tournament_event_id")
# (source, tournament_event_id) は重複検出とバックフィルの JOIN/GROUP BY で頻繁に使う組
# （大会名調査より）。無くても答えは同じなので、欠けは警告だけにする。
_REQUIRED_INDEX = "deck_list_tournament_event_id_idx"
_DDL = """
            ALTER TABLE deck_list
            ADD COLUMN IF NOT EXISTS tournament_name  TEXT,
            ADD COLUMN IF NOT EXISTS tournament_date  DATE,
            ADD COLUMN IF NOT EXISTS placement        INTEGER,
            ADD COLUMN IF NOT EXISTS format_name      TEXT,
            ADD COLUMN IF NOT EXISTS source_url       TEXT,
            ADD COLUMN IF NOT EXISTS tournament_event_id INTEGER;
"""


def verify_schema(conn):
    """deck_list に必要な列と索引が在るか確かめる（足りなければ止める）。

    まではここで毎回 ALTER TABLE ADD COLUMN IF NOT EXISTS と
    CREATE INDEX IF NOT EXISTS を打っていた。列が既に在っても DDL は対象表の
    ACCESS EXCLUSIVE を要求するので、別の取り込みが読みのトランザクションを開けたまま
    HTTP を叩いている間ずっと待つ。ロック待ちは先着順なので、待っている DDL の後ろに
    来た INSERT や SELECT まで並ぶ（実測: 取り込みを 3 本並行で動かすと
    一晩の待ち合計が 77.6 分）。列を作るのは移行の仕事なので、通常運転では在るか
    確かめるだけにする。作ってよいときだけ --migrate を付ける。
    """
    from db_config import migrate_requested, require_columns, require_indexes
    require_columns(conn, "deck_list", _REQUIRED_COLUMNS, _DDL,
                    migrate=migrate_requested(), label="ALTER")
    require_indexes(conn, "deck_list", (_REQUIRED_INDEX,),
                    """
            CREATE INDEX IF NOT EXISTS deck_list_tournament_event_id_idx
            ON deck_list (source, tournament_event_id);
        """, migrate=migrate_requested())


def save_deck(conn, event_id: int, event_name: str, event_date: str | None,
              deck_id: int, deck_name: str,
              format_code: str, cards: list[tuple[str, int, str]],
              archetype: str = "", source: str = SOURCE) -> bool:
    """デッキを DB に保存する。重複の場合は False を返す"""
    unique_name = f"mtgtop8_{event_id}_{deck_id}"
    source_url  = f"{BASE_URL}/event?e={event_id}&d={deck_id}&f={format_code}"

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO deck_list
                (deck_name, set_code, source, tournament_name, tournament_date,
                 format_name, source_url, tournament_event_id,
                 archetype)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (deck_name) DO NOTHING
            RETURNING id;
        """, (unique_name, format_code, source, event_name, event_date,
              FORMAT_NAMES.get(format_code, format_code),
              source_url, event_id, archetype or None))
        result = cur.fetchone()

    if result is None:
        # 重複＝書く物は無いが、INSERT で開いたトランザクションは閉じて返す。
        # 開けたまま返すと、呼び出し側が次のデッキを HTTP で取っている間ずっと
        # ロックを握り、connect_scrape の網（60 秒）に切られる
        conn.rollback()
        return False

    deck_db_id = result[0]

    if cards:
        psycopg2.extras.execute_values(
            conn.cursor(),
            """
            INSERT INTO deck_cards (deck_id, card_name, count, board)
            VALUES %s
            """,
            [(deck_db_id, name, count, board) for name, count, board in cards],
        )

    conn.commit()
    return True


# ─── メイン処理 ───────────────────────────────────────────────

def scrape(format_code: str, meta: int, year: int):
    conn = connect_scrape()
    verify_schema(conn)

    # EDH（Duel Commander）は source を分離し、SB: 行を統率者として扱う
    if format_code == "EDH":
        source   = "mtgtop8_edh"
        sb_board = "commander"
    elif format_code == "VI":
        source   = "mtgtop8_vintage"
        sb_board = "side"
    elif format_code == "PAU":
        source   = "mtgtop8_pauper"
        sb_board = "side"
    else:
        source   = SOURCE
        sb_board = "side"

    # 取得済みイベントをスキップ
    scraped = get_scraped_event_ids(conn, source=source)

    print(f"フォーマット: {FORMAT_NAMES.get(format_code, format_code)} "
          f"(meta={meta}, year={year}, source={source})")
    print(f"イベントID取得中...")

    event_ids = get_event_ids(format_code, meta)
    print(f"イベント数: {len(event_ids)}  取得済み: {len(scraped)}")

    new_events  = [e for e in event_ids if e not in scraped]
    print(f"新規取得対象: {len(new_events)} イベント")

    total_decks  = 0
    total_cards  = 0

    n_skip_mtgo = 0
    for event_id in tqdm(new_events, desc="イベント処理"):
        event_name, event_date, deck_infos = get_deck_ids(event_id)
        # MTGO の大会は MTGO 公式の取り込みを正とし、こちら側の転載は数えない（二重計上を避ける）
        if event_name.startswith("MTGO ") or event_name == "MTGO League":
            n_skip_mtgo += 1
            continue
        if not deck_infos:
            continue

        for deck_id, archetype in deck_infos:
            cards = get_deck_cards(deck_id, sb_board=sb_board)
            if not cards:
                continue

            saved = save_deck(
                conn, event_id, event_name, event_date,
                deck_id, archetype,
                format_code, cards,
                archetype=archetype, source=source,
            )
            if saved:
                total_decks += 1
                total_cards += len(cards)

    conn.close()
    print(f"\n完了: {total_decks} デッキ / {total_cards} カード行を取り込みました（MTGO 転載スキップ {n_skip_mtgo} イベント）")


# ─── 状況確認 ─────────────────────────────────────────────────

def check_status():
    conn = psycopg2.connect(**DB_CONFIG)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT source, COUNT(*) FROM deck_list GROUP BY source ORDER BY COUNT(*) DESC
        """)
        by_source = cur.fetchall()

        cur.execute("""
            SELECT format_name, COUNT(*) FROM deck_list
            WHERE source = 'mtgtop8'
            GROUP BY format_name ORDER BY COUNT(*) DESC
        """)
        by_format = cur.fetchall()

        # Counterspell の共起（大会データのみ）
        cur.execute("""
            SELECT
                CASE WHEN card_name_a = 'Counterspell'
                     THEN card_name_b ELSE card_name_a END AS partner,
                co_count
            FROM card_cooccurrence
            WHERE (card_name_a = 'Counterspell' OR card_name_b = 'Counterspell')
              AND source = 'mtgtop8'
            ORDER BY co_count DESC
            LIMIT 10
        """)
        cooc = cur.fetchall()

    conn.close()

    print("=== ソース別デッキ数 ===")
    for source, count in by_source:
        print(f"  {source}: {count}")

    print("\n=== フォーマット別（mtgtop8）===")
    for fmt, count in by_format:
        print(f"  {fmt}: {count}")

    if cooc:
        print("\n=== Counterspell 共起 TOP10（大会データ）===")
        for partner, count in cooc:
            print(f"  {count:3d}回  {partner}")
    else:
        print("\n大会データの共起集計は --cooccur で実行してください")


# ─── エントリーポイント ───────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", default="MO",
                        choices=["ST","PI","MO","LE","VI","PAU","EDH"],
                        help="フォーマットコード")
    parser.add_argument("--meta",   type=int, default=276,
                        help="メタコード（年別ID）")
    parser.add_argument("--year",   type=int, default=2024,
                        help="年（ログ表示用）")
    parser.add_argument("--status", action="store_true",
                        help="取得状況確認")
    parser.add_argument("--migrate", action="store_true",
                        help="足りない列や索引を作る（通常運転では DDL を打たない）")
    args = parser.parse_args()

    if args.status:
        check_status()
    else:
        scrape(args.format, args.meta, args.year)

"""
scrape_mtgo.py — Magic Online 公式デッキリスト（mtgo.com/decklists）スクレイパー
=====================================================================================
非商用の利用にかぎる。

権利の検査（実施）:
  - 運営は Daybreak Games。利用規約（2026 年 7 月 13 日版）に自動アクセス／スクレイプを
    名指しで禁じる条項は無し（「bot」はウイルス等と並ぶ投稿・送信禁止物の文脈）。
    13(f) 商用利用禁止＝本プロジェクトは非商用（AWS 公開 API は同日退役）。
  - robots.txt は 404（不在）。間隔 2 秒・User-Agent 明示＝mtgtop8 と同じ礼儀。
  - WotC ファン・コンテンツ・ポリシーの非商用範囲内。

取得フロー:
  1. /decklists/YYYY/MM              → その月の大会ページ一覧（href="/decklist/<site_name>"）
  2. /decklist/<site_name>           → ページ内 `window.MTGO.decklists.data = {...};` の JSON
     - League 型:  publish_date / decklists[].main_deck[]（sideboard "true"/"false" 旗）・順位なし
     - 大会型:     starttime / format "CSTANDARD" 等 / decklists[].main_deck + sideboard_deck /
                   final_rank[]（loginid→rank）・standings・winloss

テーブル:
  deck_list  source = 'mtgo'（Standard/Pioneer/Modern/Legacy）・'mtgo_pauper'・'mtgo_vintage'・
             'mtgo_edh'（Duel Commander・sideboard を board='commander' に）——mtgtop8 系と同じ分け方
  deck_cards board = 'main' / 'side' / 'commander'
  deck_name = 'mtgo_<site_name>_<loginid>'（UNIQUE・再走は ON CONFLICT DO NOTHING）
  source_url = https://www.mtgo.com/decklist/<site_name>  ← 再開はこれで判定
  tournament_event_id = 大会型は event_id、League 型は playeventid（League は毎日同じ ID・
                        日付で区別するため再開判定には使わない）

使い方:
  python scrape_mtgo.py --month 2026-08              # 1 か月分（取得済みページはスキップ）
  python scrape_mtgo.py --from 2025-01 --to 2026-08   # 範囲バックフィル
  python scrape_mtgo.py --month 2026-08 --limit 5 --dry-run
  python scrape_mtgo.py --status

Limited（sealed/draft）・Contraption 等の非構築は既定でスキップ（--include-limited で取り込み）。
"""

import argparse
import datetime as dt
import json
import re
import sys
import time

import psycopg2
import psycopg2.extras
import requests

from db_config import DB_CONFIG, connect_scrape, read_cursor

BASE_URL = "https://www.mtgo.com"
REQUEST_INTERVAL = 2.0
HEADERS = {
    "User-Agent": "MTG-RAG-Research-Bot/1.0 (educational, non-commercial; contact via GitHub)",
    "Accept-Language": "en-US,en;q=0.9",
}

# site_name の先頭語 → (format_name, source)
FORMAT_MAP = {
    "standard":        ("Standard",       "mtgo"),
    "pioneer":         ("Pioneer",        "mtgo"),
    "modern":          ("Modern",         "mtgo"),
    "legacy":          ("Legacy",         "mtgo"),
    "vintage":         ("Vintage",        "mtgo_vintage"),
    "pauper":          ("Pauper",         "mtgo_pauper"),
    "duel-commander":  ("Duel Commander", "mtgo_edh"),
    "premodern":       ("Premodern",      "mtgo_other"),
    "contraption":     ("Contraption",    "mtgo_other"),
    "limited":         ("Limited",        "mtgo_limited"),
    "sealed":          ("Limited",        "mtgo_limited"),
    "draft":           ("Limited",        "mtgo_limited"),
    "cube":            ("Limited",        "mtgo_limited"),
}
EVENT_WORDS = ("league", "challenge", "preliminary", "showcase", "qualifier",
               "trial", "super", "rc", "last-chance", "championship", "open",
               "eternal-weekend", "premier")

SITE_RE = re.compile(r"^(?P<body>.+?)-(?P<date>\d{4}-\d{2}-\d{2})(?P<id>\d+)$")
DATA_RE = re.compile(r"window\.MTGO\.decklists\.data\s*=\s*(\{.*?\})\s*;\s*\n", re.S)


# ─── HTTP ───────────────────────────────────────────────────────────────

_SESSION = requests.Session()
_SESSION.headers.update(HEADERS)


LAST_FETCH_FAILURE: str | None = None   # 直前の fetch が None を返した理由: "http"（302/404＝無い）／"net"（通信エラー＝後で再試行）


def fetch(url: str, retries: int = 3) -> str | None:
    """応答時間が 1〜20 秒で揺れる（実測）ので timeout は長め・再試行 3 回。
    存在しない /decklist/… は 302 で /decklists へ飛ばされる＝『無し』扱い（追わない）。
    None の理由は LAST_FETCH_FAILURE に残す（通信エラーを「ページが無い」と取り違えて
    月の行を完了扱いにした実例: 2015-11 で Connection reset ×3 → 一覧なし扱い）。"""
    global LAST_FETCH_FAILURE
    LAST_FETCH_FAILURE = None
    for attempt in range(retries):
        try:
            resp = _SESSION.get(url, timeout=60, allow_redirects=False)
            time.sleep(REQUEST_INTERVAL)
            if resp.status_code == 200:
                return resp.text
            print(f"  HTTP {resp.status_code}（{attempt+1}/{retries}）: {url}", flush=True)
            # 「確実に無い」と「サーバ側の一時的な不調」を分ける。一緒にすると 500/429 の月まで
            # 『ページが無い』と読んで完了扱いにし、行列から消してしまう（再試行されない）。
            LAST_FETCH_FAILURE = "http" if resp.status_code in (404, 301, 302, 303, 307, 308) else "net"
            if resp.status_code == 404:
                return None
            # 302 は「存在しない URL」でも「一時的な失敗」でも返る（同じ URL が 200/302 を
            # 行き来するのを実測）→ 再試行し、駄目なら諦める。URL は未記録のまま＝次回のジョブで拾い直す
            time.sleep(REQUEST_INTERVAL * (attempt + 2))
        except requests.RequestException as e:
            print(f"  通信エラー（{attempt+1}/{retries}）: {e}", flush=True)
            LAST_FETCH_FAILURE = "net"
            time.sleep(REQUEST_INTERVAL * (attempt + 2))
    return None


# ─── 解析 ───────────────────────────────────────────────────────────────

def parse_site_name(site_name: str) -> dict | None:
    """'standard-challenge-32-<YYYY-MM-DD><ID>' → 形式・種別・日付・ID"""
    m = SITE_RE.match(site_name)
    if not m:
        return None
    body, date, eid = m.group("body"), m.group("date"), m.group("id")
    fmt_key = None
    for k in sorted(FORMAT_MAP, key=len, reverse=True):   # duel-commander を commander より先に
        if body == k or body.startswith(k + "-"):
            fmt_key = k
            break
    if fmt_key is None:
        # 形式語が無い名前（例 mocs-showcase-open-…）→ ページ JSON の format で後決め
        return {"site_name": site_name, "format_key": None, "format_name": None,
                "source": None, "event_part": body, "date": date, "event_id": int(eid)}
    event_part = body[len(fmt_key):].strip("-")
    return {"site_name": site_name, "format_key": fmt_key,
            "format_name": FORMAT_MAP[fmt_key][0], "source": FORMAT_MAP[fmt_key][1],
            "event_part": event_part, "date": date, "event_id": int(eid)}


# 大会型 JSON の format コード → site_name の形式語
FORMAT_CODE = {"CSTANDARD": "standard", "CPIONEER": "pioneer", "CMODERN": "modern",
               "CLEGACY": "legacy", "CVINTAGE": "vintage", "CPAUPER": "pauper",
               "CPREMODERN": "premodern"}


def resolve_format(info: dict, data: dict) -> bool:
    """format_key 未定の info を JSON の format で埋める。埋まらなければ False。"""
    if info["format_key"]:
        return True
    code = str(data.get("format") or "").upper()
    key = FORMAT_CODE.get(code)
    if key is None:
        for k in FORMAT_MAP:                     # 'CDUELCOMMANDER' のような未知コードの保険
            if k.replace("-", "") in code.lower():
                key = k
                break
    if key is None:
        return False
    info["format_key"] = key
    info["format_name"], info["source"] = FORMAT_MAP[key]
    return True


def list_month(year: int, month: int) -> list[str] | None:
    """月の大会ページ名一覧。[] = 一覧が空（要再試行）・None = その月のページ自体が無い（完了扱い）。"""
    names: list[str] = []
    # 同じ URL でも「骨組みだけ（リンク 0・data-current-month が今月）」が返る月がある。ヘッダは no-cache で
    # CDN でなく mtgo.com 側の作り。こちらが叩いた後、数秒〜数分で本物に変わる（実測）。冷えた古い月は
    # 初回から本物。→ URL の形を変えつつ、間隔を 10→30→60→120 秒と伸ばして最大 5 回（最悪 3.7 分）。
    # それでも空なら [] を返し、呼び出し元が行を末尾へ回す。
    base = f"{BASE_URL}/decklists/{year}/{month:02d}"
    variants = [base, base + "/", base + "/", lambda: f"{base}/?r={int(time.time())}", lambda: f"{base}?r={int(time.time())}"]
    waits = [10, 30, 60, 120]
    for attempt, v in enumerate(variants):
        url = v() if callable(v) else v
        html = fetch(url)
        if html is None:
            if LAST_FETCH_FAILURE == "net":     # 通信エラー＝「無い」ではない → 空を返して後で再試行させる
                print(f"  一覧取得できず（{year}-{month:02d}・通信エラー・後で再試行）", flush=True)
                return []
            # 302/404＝その月のページ自体が無い（アーカイブの外）→ 変形を試しても無駄
            print(f"  一覧なし（{year}-{month:02d}・ページが無い）", flush=True)
            return None
        names = sorted(set(re.findall(r'href="/decklist/([^"]+)"', html or "")))
        if names:
            if attempt:
                print(f"  一覧 {year}-{month:02d}: {attempt+1} 回目（{url[len(BASE_URL):]}）で {len(names)} 件", flush=True)
            break
        if attempt < len(waits):
            print(f"  一覧が空（{year}-{month:02d}）・{waits[attempt]} 秒後に取り直し（{attempt+1}/{len(variants)}）", flush=True)
            time.sleep(max(REQUEST_INTERVAL, waits[attempt]))
        else:
            print(f"  一覧が空（{year}-{month:02d}）・{len(variants)} 回とも空", flush=True)
    return names


def parse_decklist_page(html: str) -> dict | None:
    m = DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        print(f"  JSON 解析失敗: {e}", flush=True)
        return None


_ALIAS: dict[str, str] = {}


def load_alias(conn) -> None:
    """mtgo_name_alias（MTGO 表示名→紙の正式名）を読み込む。
    実例: om1「Through the Omenpaths」= MTGO 限定のスパイダーマン別名（Kavaero, Mind-Bitten → Superior Spider-Man）。
    Scryfall では printed_name に別名・name に紙の名前が入る。"""
    with read_cursor(conn) as cur:      # 読んだら閉じる（HTTP の間ロックを握らない）
        cur.execute("SELECT mtgo_name, card_name FROM mtgo_name_alias")
        _ALIAS.update({a: b for a, b in cur.fetchall()})


def norm_card_name(name: str) -> str:
    """MTGO の表記を Scryfall 流儀へ寄せる（card_id 充填は fix_deck_links が担う）。"""
    name = name.strip()
    if name in _ALIAS:
        return _ALIAS[name]
    # 'Fire/Ice' → 'Fire // Ice'（既に ' // ' ならそのまま）
    if "/" in name and " // " not in name:
        parts = [p.strip() for p in name.split("/") if p.strip()]
        if len(parts) == 2:
            name = " // ".join(parts)
    return name


def extract_decks(data: dict, info: dict) -> tuple[str, str | None, list[dict]]:
    """→ (event_name, event_date, [ {loginid, placement, cards:[(name,count,board)]} ])"""
    is_league = "publish_date" in data
    if is_league:
        event_name = data.get("name") or info["event_part"] or "League"
        event_date = data.get("publish_date") or info["date"]
    else:
        event_name = data.get("description") or info["event_part"]
        st = data.get("starttime") or ""
        event_date = st[:10] if st else info["date"]

    rank_by_login = {}
    for r in data.get("final_rank") or []:
        try:
            rank_by_login[str(r["loginid"])] = int(r["rank"])
        except (KeyError, ValueError, TypeError):
            pass
    if not rank_by_login:
        for r in data.get("standings") or []:
            try:
                rank_by_login[str(r["loginid"])] = int(r["rank"])
            except (KeyError, ValueError, TypeError):
                pass

    sb_board = "commander" if info["format_key"] == "duel-commander" else "side"
    decks = []
    for d in data.get("decklists") or []:
        loginid = str(d.get("loginid") or "")
        # deck_name の鍵: League は同一プレイヤーが同日に複数 5-0 することがある →
        # 行ごとに一意な loginplayeventcourseid を優先（無ければ loginid）。順位引きは loginid
        deck_key = str(d.get("loginplayeventcourseid") or loginid)   # 大会型は loginid のまま（初回本走と同じ鍵）
        cards: list[tuple[str, int, str]] = []
        for c in d.get("main_deck") or []:
            attrs = c.get("card_attributes") or {}
            name = attrs.get("card_name") or c.get("card_name")
            if not name:
                continue
            try:
                qty = int(c.get("qty") or 0)
            except ValueError:
                qty = 0
            if qty <= 0:
                continue
            sb_flag = str(c.get("sideboard", "false")).lower() == "true"
            cards.append((norm_card_name(name), qty, sb_board if sb_flag else "main"))
        for c in d.get("sideboard_deck") or []:
            attrs = c.get("card_attributes") or {}
            name = attrs.get("card_name") or c.get("card_name")
            if not name:
                continue
            try:
                qty = int(c.get("qty") or 0)
            except ValueError:
                qty = 0
            if qty <= 0:
                continue
            cards.append((norm_card_name(name), qty, sb_board))
        if not cards:
            continue
        # プレイヤー名（d["player"]）は取らない（下の設計判断の注記を参照）
        decks.append({"loginid": loginid, "deck_key": deck_key,
                      "placement": rank_by_login.get(loginid), "cards": cards})
    return event_name, event_date, decks


# ─── DB ─────────────────────────────────────────────────────────────────

def scraped_urls(conn) -> set[str]:
    with read_cursor(conn) as cur:      # 同上。この後の fetch は 1 秒 1 回で長い
        cur.execute("SELECT DISTINCT source_url FROM deck_list "
                    "WHERE source LIKE 'mtgo%%' AND source_url IS NOT NULL")
        return {r[0] for r in cur.fetchall()}


def save_event(conn, info: dict, event_name: str, event_date: str | None,
               decks: list[dict], dry_run: bool) -> tuple[int, int]:
    url = f"{BASE_URL}/decklist/{info['site_name']}"
    saved = dup = 0
    if dry_run:
        return len(decks), 0
    with conn.cursor() as cur:
        for d in decks:
            unique_name = f"mtgo_{info['site_name']}_{d['deck_key']}"
            # 設計判断: この取り込みはプレイヤー名を持たない。deck_list に
            # player_name 列は無く、名前を隔離する players 表は開発側だけに置く。
            # ただし deck_name には出所側のキー（loginplayeventcourseid・無ければ
            # loginid）が重複判定のために残る＝「名前を持たない」であって「出所側の
            # 識別子が一切無い」ではない。
            cur.execute("""
                INSERT INTO deck_list
                    (deck_name, set_code, source, tournament_name, tournament_date,
                     placement, format_name, source_url,
                     tournament_event_id, archetype)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL)
                ON CONFLICT (deck_name) DO NOTHING
                RETURNING id
            """, (unique_name, info["format_key"][:10], info["source"], event_name,
                  event_date, d["placement"], info["format_name"],
                  url, info["event_id"]))
            row = cur.fetchone()
            if row is None:
                dup += 1
                continue
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO deck_cards (deck_id, card_name, count, board) VALUES %s",
                [(row[0], n, c, b) for n, c, b in d["cards"]])
            saved += 1
    conn.commit()
    return saved, dup


# ─── メイン ─────────────────────────────────────────────────────────────

def month_range(a: str, b: str):
    y, m = map(int, a.split("-"))
    y2, m2 = map(int, b.split("-"))
    while (y, m) <= (y2, m2):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def run(months, limit: int | None, dry_run: bool, include_limited: bool,
        formats: set[str] | None):
    conn = connect_scrape()
    load_alias(conn)
    done = scraped_urls(conn)
    tot_pages = tot_decks = tot_dup = skipped = 0
    n_fetched = 0
    empty_months: list[str] = []
    for y, m in months:
        names = list_month(y, m)
        if names is None:                   # ページが無い月（アーカイブの外）は失敗でなく完了扱い（行列が回り続けない）
            print(f"[{y}-{m:02d}] ページ無し（アーカイブ外）・完了扱い", flush=True)
            continue
        print(f"[{y}-{m:02d}] 大会ページ {len(names)} 件（取得済み URL {len(done)}）", flush=True)
        if not names:
            # 一覧が取れない月は「失敗」として呼び出し元に返す（cron 便は行列の行を残して翌日やり直す）。
            # の cron で空の 200 が 2 連続→ 0 件で正常終了→ 行が消えた事故の再発防止
            empty_months.append(f"{y}-{m:02d}")
            continue
        for site in names:
            info = parse_site_name(site)
            if info is None:
                print(f"  解釈不能・スキップ: {site}", flush=True)
                continue
            if info["source"] == "mtgo_limited" and not include_limited:
                continue
            if formats and info["format_key"] is not None and info["format_key"] not in formats:
                continue
            url = f"{BASE_URL}/decklist/{site}"
            if url in done:
                skipped += 1
                continue
            if limit is not None and n_fetched >= limit:
                break
            html = fetch(url)
            n_fetched += 1
            if not html:
                continue
            data = parse_decklist_page(html)
            if not data:
                print(f"  JSON 無し: {site}", flush=True)
                continue
            if not resolve_format(info, data):
                print(f"  形式不明・スキップ: {site}（format={data.get('format')}）", flush=True)
                continue
            if info["source"] == "mtgo_limited" and not include_limited:
                continue
            if formats and info["format_key"] not in formats:
                continue
            event_name, event_date, decks = extract_decks(data, info)
            saved, dup = save_event(conn, info, event_name, event_date, decks, dry_run)
            tot_pages += 1
            tot_decks += saved
            tot_dup += dup
            print(f"  {site}: {event_name} {event_date} デッキ {len(decks)}"
                  f"（保存 {saved}・重複 {dup}）{'[dry-run]' if dry_run else ''}", flush=True)
    print(f"完了: ページ {tot_pages}・デッキ保存 {tot_decks}・重複 {tot_dup}・"
          f"取得済みスキップ {skipped}{'・dry-run' if dry_run else ''}", flush=True)
    conn.close()
    if empty_months:
        print(f"一覧が取れなかった月: {', '.join(empty_months)}（exit 2・再試行すべし）", flush=True)
        return 2
    return 0


def status():
    conn = psycopg2.connect(**DB_CONFIG)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT source, format_name, count(*), min(tournament_date), max(tournament_date),
                   count(DISTINCT source_url)
            FROM deck_list WHERE source LIKE 'mtgo%%'
            GROUP BY 1, 2 ORDER BY 1, 3 DESC
        """)
        rows = cur.fetchall()
    if not rows:
        print("mtgo 系データは未取得")
    for r in rows:
        print(f"{r[0]:14s} {r[1]:16s} デッキ {r[2]:6d}  {r[3]}〜{r[4]}  大会ページ {r[5]}")
    conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--month", help="YYYY-MM（1 か月分）")
    ap.add_argument("--from", dest="from_", help="YYYY-MM（範囲の始め）")
    ap.add_argument("--to", help="YYYY-MM（範囲の終わり・省略時は今月）")
    ap.add_argument("--limit", type=int, help="取得するページ数の上限（試運転用）")
    ap.add_argument("--dry-run", action="store_true", help="DB に書かない")
    ap.add_argument("--include-limited", action="store_true")
    ap.add_argument("--formats", help="カンマ区切り（standard,modern,...）で絞る")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        status()
        return
    today = dt.date.today()
    if args.month:
        months = month_range(args.month, args.month)
    elif args.from_:
        months = month_range(args.from_, args.to or f"{today.year}-{today.month:02d}")
    else:
        months = month_range(f"{today.year}-{today.month:02d}", f"{today.year}-{today.month:02d}")
    fmts = set(args.formats.split(",")) if args.formats else None
    return run(list(months), args.limit, args.dry_run, args.include_limited, fmts)


if __name__ == "__main__":
    sys.exit(main())

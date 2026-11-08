#!/usr/bin/env python3
"""fill_set_codes.py — 新しいセットの印刷を Scryfall の検索 API で引き、set_codes にそのセット記号を足す（足すだけ）。

全印刷のバルク（all_cards・約 2.9GB）から作り直す enrich_printings.py は重く、丸ごと流すと Arena の調整版（A-）の
set_codes を NULL に戻す（--dry-run の試運転で 216 行。A- の行は Scryfall のファイルから名前ごと消えているため）。新しいセットの分だけを、
そのセットの印刷の一覧（/cards/search?q=e:<記号>&unique=prints・1 ページ 175 枚）で引いて足す。
毎晩の取り込みは oracle_cards（代表の印刷だけ）を読んで set_codes を書かないため、これが無いと先行収録の新カードの
set_codes は NULL のまま残る。

- 足すだけ: 記号を消さない・NULL に戻さない・既にその記号を持つ行は触らない（冪等）
- 紙の印刷だけ数える（デジタル専用の印刷は set_codes に入れない。enrich_printings.py は Arena 専用カードの行に限って
  デジタルの印刷も数えるが、ここでは数えない＝紙のカードについては同じ値）
- 並びは enrich_printings.py と同じ sorted()＝あとで丸ごと作り直しても同じ値になる
- 日本語の印刷の画像（image_url_ja）は空の行だけ埋める（e:<記号> lang:ja の印刷・発売日の新しい方・両面は表面）
- DB に無い名前（トークン・アートカード等）は数えるだけ

使い方: python fill_set_codes.py <セット記号>... [--dry-run]
毎晩は daily_set_watch.py が、発売が近い・発売直後のセットについて fill() を呼ぶ。
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "mtg-sisho set-watch/1.0 (educational project; contact via GitHub)"
SEARCH = "https://api.scryfall.com/cards/search"
_MAX_PAGES = 20          # 1 ページ 175 枚＝3,500 印刷まで（大きいセットでも 4〜5 ページ）。止まらない取得の歯止め
_INTERVAL = 0.2          # Scryfall の推奨間隔（50〜100ms 以上）を守る


def _http_json(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch_prints(code: str, lang: str | None = None, *, _get=None, _sleep=time.sleep) -> list[dict]:
    """e:<code>（lang があれば lang:<lang> も）の印刷を全ページ引く。該当なしの 404 は空。ほかの失敗は送出する。"""
    get = _get or _http_json
    q = f"e:{code}" + (f" lang:{lang}" if lang else "")
    url = f"{SEARCH}?unique=prints&q={urllib.parse.quote(q)}"
    out: list[dict] = []
    for _ in range(_MAX_PAGES):
        _sleep(_INTERVAL)
        try:
            page = get(url)
        except urllib.error.HTTPError as e:
            if e.code == 404 and not out:
                return []
            raise
        out.extend(page.get("data") or [])
        if not page.get("has_more"):
            return out
        url = page["next_page"]
    raise RuntimeError(f"{code}: {_MAX_PAGES} ページを超えた（取得を打ち切った）")


def _face_image(c: dict):
    """image_uris.normal（両面カードは表面）。enrich_printings.py と同じ規則。"""
    uris = c.get("image_uris")
    if not uris:
        faces = c.get("card_faces") or []
        if faces and isinstance(faces, list):
            uris = faces[0].get("image_uris")
    return (uris or {}).get("normal")


def plan_set_codes(code: str, prints: list[dict], rows: dict[str, list[tuple]]) -> tuple[list[tuple], dict]:
    """そのセットの紙の印刷に当たる DB の行へ、記号を足す更新の一覧を作る（DB も通信も使わない）。

    rows: {card_name: [(id, set_codes), ...]}（同じ名前の行が二つあれば両方に足す）。
    返り値: ([(新しい set_codes, id), ...], 内訳)。"""
    names = {c.get("name") for c in prints if c.get("name") and not c.get("digital")}
    updates, stat = [], {"prints": len(prints), "names": len(names), "not_in_db": 0, "null_to_value": 0,
                         "added": 0, "already": 0}
    for name in sorted(names):
        if name not in rows:
            stat["not_in_db"] += 1
            continue
        for cid, old in rows[name]:
            if old and code in old:
                stat["already"] += 1
                continue
            stat["null_to_value" if not old else "added"] += 1
            updates.append((sorted(set(old or []) | {code}), cid))
    return updates, stat


def plan_image_ja(prints_ja: list[dict], rows: dict[str, list[tuple]]) -> list[tuple]:
    """日本語の印刷の画像を、image_url_ja が空の行だけに入れる更新の一覧（発売日の新しい印刷を採る）。

    rows: {card_name: [(id, image_url_ja), ...]}。"""
    best: dict[str, tuple] = {}
    for c in prints_ja:
        name, url = c.get("name"), _face_image(c)
        if not name or not url or c.get("lang") != "ja":
            continue
        rel = c.get("released_at") or ""
        if name not in best or rel > best[name][0]:
            best[name] = (rel, url)
    return [(best[n][1], cid) for n in sorted(best) for cid, old in rows.get(n, []) if not old]


def fill(codes, *, dry_run: bool = False, log=print, _fetch=fetch_prints, _connect=None) -> dict:
    """セットごとに印刷を引いて set_codes に記号を足し、image_url_ja の空を埋める。セット単位で commit する。

    一つのセットの失敗（取得・DB）は記録して次のセットへ進む。返り値は {記号: 内訳}。"""
    if _connect is None:
        import psycopg2
        from db_config import get_db_config

        def _connect():
            return psycopg2.connect(**get_db_config())
    from psycopg2.extras import execute_batch

    result = {}
    conn = _connect()
    try:
        for code in codes:
            try:
                prints = _fetch(code)
                names = sorted({c["name"] for c in prints if c.get("name")})
                with conn.cursor() as cur:
                    cur.execute("SELECT card_name, id, set_codes, image_url_ja FROM mtg_cards_v2"
                                " WHERE card_name = ANY(%s)", (names,))
                    got = cur.fetchall()
                sc_rows, img_rows = {}, {}
                for name, cid, sc, img in got:
                    sc_rows.setdefault(name, []).append((cid, sc))
                    img_rows.setdefault(name, []).append((cid, img))
                updates, stat = plan_set_codes(code, prints, sc_rows)
                img_updates = plan_image_ja(_fetch(code, "ja"), img_rows) if names else []
                stat["image_ja"] = len(img_updates)
                if not dry_run:
                    with conn.cursor() as cur:
                        # 足すだけ: 読んだ後に別の取り込みが記号を足していたら触らない
                        execute_batch(cur, "UPDATE mtg_cards_v2 SET set_codes = %s WHERE id = %s"
                                           " AND NOT (%s = ANY(coalesce(set_codes, '{}')))",
                                      [(new, cid, code) for new, cid in updates], page_size=500)
                        execute_batch(cur, "UPDATE mtg_cards_v2 SET image_url_ja = %s WHERE id = %s"
                                           " AND image_url_ja IS NULL", img_updates, page_size=500)
                    conn.commit()
                result[code] = stat
                log(f"set_codes: {code} 印刷 {stat['prints']}・名前 {stat['names']}（DB に無い {stat['not_in_db']}）"
                    f"・NULL→値 {stat['null_to_value']}・足した {stat['added']}・持っていた {stat['already']}"
                    f"・日本語画像 {stat['image_ja']}" + ("（試運転＝書いていない）" if dry_run else ""))
            except Exception as e:
                conn.rollback()
                result[code] = {"error": f"{type(e).__name__}: {e}"}
                log(f"set_codes: {code} 失敗 {type(e).__name__}: {str(e)[:200]}（書いていない・次のセットへ）")
    finally:
        conn.close()
    return result


def main():
    ap = argparse.ArgumentParser(description="新しいセットの印刷を Scryfall で引き、set_codes に記号を足す（足すだけ）")
    ap.add_argument("codes", nargs="+", help="セット記号（例 trk trc）")
    ap.add_argument("--dry-run", action="store_true", help="書き込まず内訳だけ出す")
    args = ap.parse_args()
    res = fill([c.lower() for c in args.codes], dry_run=args.dry_run)
    sys.exit(1 if any("error" in v for v in res.values()) else 0)


if __name__ == "__main__":
    main()

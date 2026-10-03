#!/usr/bin/env python3
"""enrich_printings.py — 全印刷バルク（all_cards）から印刷由来の列を集約する。

充填する列（どちらもセット検索のための列）:
  - set_codes    text[] : そのカードが**一度でも収録された全セット**のコード集合。
      oracle_cards 由来の set_code（代表印刷のみ）は再録で上書きされるため、
      「灯争大戦のプレインズウォーカー」で看板 PW が全滅する偽陰性が実測で出た
      （Nicol Bolas→rvr 等）。全印刷の集合なら取りこぼさない。
      デジタル専用印刷（Arena/MTGO）は除外＝紙のセット名辞書と噛み合わせるため。
  - image_url_ja text   : 日本語印刷のカード画像 URL（lang="ja" の印刷のうち
      released_at 最新のもの・両面カードは表面）。表記切替「日」モードで使う。
      日本語印刷が無いカードは NULL（フロントが英語画像へフォールバック）。

設計メモ:
  - 入力は <データの置き場所>/all_cards_scryfall.json（2.4GB・全印刷・全言語・1 行 1 カード）。
    ストリームで舐める＝メモリに全部載せない。
  - 行数は 31,635 のまま＝**過去の重複排除（オラクル単位 1 行）は崩さない**。
    印刷履歴が属性として増えるだけ（set_codes を配列で持つ）。
  - 既存の text[] 列（front_keywords 等）と同型＝非正規化が正。
  - 冪等: 値が変わる行だけ UPDATE。バルクを新版に差し替えて再実行すれば追いつく。

使い方:
    python enrich_printings.py [all_cards のパス]
"""
import json
import os
import sys

import psycopg2
from psycopg2.extras import execute_batch

from db_config import DATA_DIR, get_db_config, migrate_requested, require_columns, require_indexes

# --migrate と --dry-run を除いた最初の引数がバルクのパス。「- で始まる物は全部除く」にすると
# -bulk.json のようなファイル名を黙って捨てて既定のバルクで走ってしまう。
_ARGS = [a for a in sys.argv[1:] if a not in ("--migrate", "--dry-run")]
DRY_RUN = "--dry-run" in sys.argv[1:]   # 書き込まず差分の内訳だけ出す
BULK = _ARGS[0] if _ARGS else os.path.join(DATA_DIR, "all_cards_scryfall.json")

ALTER = """
ALTER TABLE mtg_cards_v2 ADD COLUMN IF NOT EXISTS set_codes    text[];
ALTER TABLE mtg_cards_v2 ADD COLUMN IF NOT EXISTS image_url_ja text;
"""


def _face_image(c: dict):
    """image_uris.normal（両面カードは表面）。enrich_scryfall_meta と同じ規則。"""
    uris = c.get("image_uris")
    if not uris:
        faces = c.get("card_faces") or []
        if faces and isinstance(faces, list):
            uris = faces[0].get("image_uris")
    return (uris or {}).get("normal")


def main():
    conn = psycopg2.connect(**get_db_config())
    cur = conn.cursor()
    # 通常運転では DDL を打たない
    require_columns(conn, "mtg_cards_v2", ("set_codes", "image_url_ja"), ALTER,
                    migrate=migrate_requested(), label="ALTER")

    # DB 側の対象カード名（コア 31,635 のみ集約＝バルクの 9 万種全部は持たない）
    cur.execute("SELECT card_name, digital FROM mtg_cards_v2")
    rows_t = cur.fetchall()
    targets = {r[0] for r in rows_t}
    digital_names = {r[0] for r in rows_t if r[1]}
    print(f"対象カード名: {len(targets)}")

    sets: dict[str, set] = {}          # name -> set codes
    # Arena 専用カード（digital=true）はデジタルの印刷しか無い＝紙だけ数えると set_codes が
    # NULL に戻る。digital の行だけデジタルの印刷も数える。
    dsets: dict[str, set] = {}
    ja_img: dict[str, tuple] = {}      # name -> (released_at, url)
    n_lines = 0
    # 行単位の読みをやめて ijson のストリームにする。今の取り込みは
    # JSONL.gz を「改行なしの JSON 配列」に包み直すため、旧実装（1 行 1 カード前提の
    # for line in f）は 2.9GB を一行として丸読みし OOM Killed になった（実測）。
    # ijson.items(f, "item") は配列形式でも 1 行 1 カードを [ ] で包んだ形式でも同じに
    # 動き、メモリは O(1)（extract_japanese と同じ流儀）。
    import ijson
    with open(BULK, "rb") as f:
        for c in ijson.items(f, "item"):
            n_lines += 1
            name = c.get("name")
            if name not in targets:
                continue
            # set_codes: デジタル専用印刷は除外（紙のセット名辞書と噛み合わせる）
            sc = c.get("set")
            if sc and not c.get("digital"):
                sets.setdefault(name, set()).add(sc)
            elif sc and name in digital_names:
                dsets.setdefault(name, set()).add(sc)
            # image_url_ja: 日本語印刷のうち released_at 最新を採る
            if c.get("lang") == "ja":
                url = _face_image(c)
                if url:
                    rel = c.get("released_at") or ""
                    cur_best = ja_img.get(name)
                    if cur_best is None or rel > cur_best[0]:
                        ja_img[name] = (rel, url)
            if n_lines % 500000 == 0:
                print(f"  …{n_lines:,} 行走査")

    print(f"走査 {n_lines:,} 行 / set_codes 対象 {len(sets):,} 枚 / 日本語画像 {len(ja_img):,} 枚")

    # 差分だけ UPDATE（冪等）
    cur.execute("SELECT id, card_name, set_codes, image_url_ja FROM mtg_cards_v2")
    updates = []
    rows_all = cur.fetchall()
    for cid, name, old_sets, old_ja in rows_all:
        new_sets = sorted(sets.get(name, set()) | (dsets.get(name, set()) if name in digital_names else set())) or None
        new_ja = ja_img.get(name, (None, None))[1]
        if (old_sets or None) != new_sets or (old_ja or None) != (new_ja or None):
            updates.append((new_sets, new_ja, cid))
    print(f"差分: {len(updates):,} 行")
    if DRY_RUN:
        from collections import Counter
        old = {cid: (os_, oj) for cid, _n, os_, oj in rows_all}
        kinds = Counter()
        for ns, nj, cid in updates:
            os_, oj = old[cid]
            kinds[("set_codes " + ("NULL→値" if not os_ else "値→NULL" if not ns else "値→別の値") if (os_ or None) != ns else "set_codes 同じ",
                   "image_ja " + ("NULL→値" if not oj else "値→NULL" if not nj else "値→別の値") if (oj or None) != (nj or None) else "image_ja 同じ")] += 1
        for k, v in kinds.most_common():
            print(f"  {v:6,}  {k[0]} / {k[1]}")
        added, removed = Counter(), Counter()
        for ns, nj, cid in updates:
            o, n = set(old[cid][0] or []), set(ns or [])
            added.update(n - o); removed.update(o - n)
        print("  増えたセット（上位）:", added.most_common(12))
        print("  減ったセット（上位）:", removed.most_common(12))
        conn.rollback()
        return
    execute_batch(cur,
                  "UPDATE mtg_cards_v2 SET set_codes=%s, image_url_ja=%s WHERE id=%s",
                  updates, page_size=1000)
    conn.commit()

    # GIN 索引（set_codes && ARRAY[...] を索引で解く・front_keywords と同じ流儀)
    require_indexes(conn, "mtg_cards_v2", ("idx_cards_set_codes",),
                    "CREATE INDEX IF NOT EXISTS idx_cards_set_codes"
                    " ON mtg_cards_v2 USING gin(set_codes)",
                    migrate=migrate_requested())

    for label, sql in [
        ("set_codes 保有",    "SELECT count(*) FROM mtg_cards_v2 WHERE set_codes IS NOT NULL"),
        ("image_url_ja 保有", "SELECT count(*) FROM mtg_cards_v2 WHERE image_url_ja IS NOT NULL"),
    ]:
        cur.execute(sql)
        print(f"  {label}: {cur.fetchone()[0]:,}")
    cur.execute("SELECT card_name, set_codes FROM mtg_cards_v2"
                " WHERE card_name='Nicol Bolas, Dragon-God'")
    print("  検分（灯争大戦の看板）:", cur.fetchone())
    conn.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python
"""
import_rulings.py — Scryfall 経由の公式裁定（Gatherer rulings）の搬入（新設）
================================================================================
目的: カード個別の公式裁定を DB に置き、ルール問題の回答で「裁定の有無」まで
裏取りできるようにする（閃光×クローン調査で「裁定テーブル不在＝裏取り不能」と
正直に線を引いた穴を塞ぐ・AGENTIC_RAG の rulings() 道具の実体）。

出典: Scryfall の rulings バルク（毎日更新・jsonl.gz・圧縮 5MB 級）。
Gatherer を直接スクレイプする必要はない（source='wotc' が公式裁定）。

★運用メモ（方針・忘れないこと）:
  裁定は日々増える＝逐一アップデートが必要。**ローカルで使ううちは手動再走で
  足りるが、オンライン（クラウドの語り係が裁定を引く形）になったら定期更新の
  仕組みが必須になる**（夜間ジョブへの相乗り or 新セット時の手動実行・その日に裁定）。

設計の前提:
  - 裁定は oracle_id（カードの論理 ID）に紐づくが、mtg_cards_v2 に oracle_id 列は
    無い。橋は all_cards_scryfall.json（2.4GB）をストリームで舐めて
    oracle_id→英語名を集め、card_name で mtg_cards_v2.id に解決する
    （enrich_printings と同じ様式）。橋が架からない裁定も捨てずに入れる
    （card_id NULL＝非リーガル退避カードや、手元のバルクより新しいカード）
  - 冪等: TRUNCATE→INSERT（配布物は完全スナップショット・共起 v2 と同じ型）
  - 裁定文は英語のみ（日本語の公式配布は存在しない）
  - 原文の置き場は data/rulings/（Scryfall 配布物・リポジトリに含めない）

使い方:
  /mnt/new_hdd/my_rag_env/bin/python src/import_rulings.py \
      --file data/rulings/rulings-YYYYMMDD.jsonl.gz --version YYYY-MM-DD
"""
import argparse
import gzip
import json
import os
import sys

import ijson
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 自分と同じ src/
from db_config import DB_CONFIG

CARDS_BULK = '/mnt/new_hdd/all_cards_scryfall.json'

DDL = """
CREATE TABLE IF NOT EXISTS card_rulings (
    id             serial PRIMARY KEY,
    oracle_id      uuid NOT NULL,
    card_id        integer,          -- mtg_cards_v2.id（橋が架からなければ NULL）
    card_name      text,             -- バルク由来の英語名（照会の便宜）
    source         text NOT NULL,    -- 'wotc'（=Gatherer 公式）/ 'scryfall'
    published_at   date,
    comment        text NOT NULL,
    source_version date NOT NULL     -- 配布物の版
);
CREATE INDEX IF NOT EXISTS card_rulings_card_id ON card_rulings (card_id);
CREATE INDEX IF NOT EXISTS card_rulings_oracle  ON card_rulings (oracle_id);
"""


def load_rulings(path: str) -> list[dict]:
    opener = gzip.open if path.endswith('.gz') else open
    out = []
    with opener(path, 'rt', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def bridge_names(oracle_ids: set) -> dict:
    """all_cards_scryfall.json をストリームで舐めて oracle_id→英語名を集める。
    2.4GB を丸読みしない（ijson・enrich 流儀）。必要な oracle_id だけ拾う。"""
    names = {}
    with open(CARDS_BULK, 'rb') as f:
        for card in ijson.items(f, 'item'):
            oid = card.get('oracle_id')
            if oid in oracle_ids and oid not in names:
                names[oid] = card.get('name')
                if len(names) == len(oracle_ids):
                    break
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--file', required=True)
    ap.add_argument('--version', required=True, help='配布物の版（YYYY-MM-DD）')
    args = ap.parse_args()

    rulings = load_rulings(args.file)
    oids = {r['oracle_id'] for r in rulings}
    print(f'裁定 {len(rulings)} 件・カード {len(oids)} 種を読んだ')
    if len(rulings) < 50000:
        raise SystemExit('件数が経験値（7.8 万件級）を大きく割った＝配布物の壊れを疑う。中止')

    print('橋を架ける（2.4GB バルクをストリーム走査・数分）...')
    names = bridge_names(oids)
    print(f'  oracle_id→名前: {len(names)}/{len(oids)}（欠け={len(oids)-len(names)}＝'
          f'手元バルクより新しいカード・冪等の追いつき対象）')

    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(DDL)
    # 名前→card_id の解決表（DB 側・一括）
    cur.execute('SELECT card_name, id FROM mtg_cards_v2')
    id_of = dict(cur.fetchall())

    rows = []
    for r in rulings:
        name = names.get(r['oracle_id'])
        rows.append((r['oracle_id'], id_of.get(name), name, r['source'],
                     r.get('published_at'), r['comment'], args.version))
    cur.execute('TRUNCATE card_rulings RESTART IDENTITY')
    cur.executemany(
        'INSERT INTO card_rulings (oracle_id, card_id, card_name, source,'
        ' published_at, comment, source_version)'
        ' VALUES (%s, %s, %s, %s, %s, %s, %s)', rows)
    conn.commit()
    cur.execute("""SELECT count(*), count(card_id), count(*) - count(card_id)
                   FROM card_rulings""")
    total, matched, unmatched = cur.fetchone()
    print(f'搬入 {total} 行・card_id 解決 {matched}・未解決 {unmatched}')
    conn.close()


if __name__ == '__main__':
    main()

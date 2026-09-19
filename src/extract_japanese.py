"""
extract_japanese.py — Scryfall all_cards.json から最新日本語テキストを抽出
=========================================================================
同名カードが複数の言語版・セット版がある場合、
「printed_text が存在する中で released_at が最新」のものを採用する。

printed_text が空のセット（eoc 等）は採用しない。
これにより Farseek 等で英語テキストが誤って格納される問題を解決。

使い方:
  python extract_japanese.py
  python extract_japanese.py --status
"""

import os
import argparse
import re
import ijson
import psycopg2
from tqdm import tqdm


def is_japanese(text: str) -> bool:
    """テキストに日本語文字（ひらがな・カタカナ・漢字）が含まれているか"""
    return bool(re.search(r'[ぁ-んァ-ン一-龯]', text))

from db_config import DB_CONFIG

# 既定は従来どおり。**/mnt/new_hdd は postgres 所有で claude は書けない**ため
# （搬入便で発覚）、別の場所に置いた新版は MTG_ALL_CARDS_JSON で差す。
JSON_FILE    = os.environ.get("MTG_ALL_CARDS_JSON",
                              "/mnt/new_hdd/all_cards_scryfall.json")
BATCH_COMMIT = 500


def add_japanese_columns(conn):
    with conn.cursor() as cur:
        cur.execute("""
            ALTER TABLE mtg_cards_v2
            ADD COLUMN IF NOT EXISTS japanese_name TEXT,
            ADD COLUMN IF NOT EXISTS japanese_oracle_text TEXT;
        """)
    conn.commit()
    print("カラム確認完了: japanese_name, japanese_oracle_text")


def extract_printed_text(card: dict) -> tuple[str | None, str | None]:
    """日本語名・日本語テキストを抽出（両面カード対応）。

    printed_name の面フォールバック:
    両面・分割カードの日本語印刷はトップレベル printed_name が無く、各面の
    printed_name にだけ日本語名が入る（実測 704 枚が「日本語テキストあり・
    日本語名なし」だった）。printed_text が既にやっている面フォールバックを
    名前側にも写す。結合は card_name と同じ「 // 」区切り。全面が揃わない
    印刷は採用しない（片面だけの名前は不完全＝NULL の規約を守る）。"""
    ja_name = (card.get("printed_name") or "").strip() or None
    faces = card.get("card_faces") or []
    if not ja_name and faces:
        # 取り決め: 先頭面に日本語名があれば、日本語名を持つ面だけを
        # // で結合する。adventure/prepare の ja 印刷は Scryfall 側で当事者面の
        # printed_name が None（71 枚・厚かましい借り手等）＝全面必須だと永久に NULL。
        # カード上部の名前（先頭面）が本体なので、それを採り、無い面は足さない。
        names = [(f.get("printed_name") or "").strip() for f in faces]
        if names and names[0] and is_japanese(names[0]):
            ja_name = " // ".join(n for n in names if n and is_japanese(n))
    # 名前にも日本語文字の検査を掛ける（実測: spg 等の ja 印刷は面の
    # printed_name が英語のことがあり、面フォールバック導入で 62 枚に
    # 「Dusk // Dawn // Dusk // Dawn」のような英語名が入った）。テキスト側と同じ線。
    if ja_name and not is_japanese(ja_name):
        ja_name = None
    # 当事者面の printed_name はルビ付きで来る（「踏（ふ）みつけ」・35 枚実測）。
    # 名前としてはルビを剥がした形が正（検索・表示とも）。
    if ja_name:
        ja_name = re.sub(r"（[ぁ-ん]+）", "", ja_name)
    ja_text = (card.get("printed_text") or "").strip()
    if not ja_text:
        texts = [(f.get("printed_text") or "").strip() for f in faces
                 if (f.get("printed_text") or "").strip()]
        ja_text = " // ".join(texts) if texts else ""
    return ja_name, ja_text or None


def apply_manual_names(conn) -> int:
    """手動補正表 name_ja_manual を最後に当てる（設計判断 (a)）。

    背景: Scryfall の一部 ja 印刷は面の printed_name に英語名が入っている
    （MH3 の両面 4 枚を実測・上流も未修正）＝抽出では永久に NULL。さらに全量監査
    （MTGJSON 突き合わせ→mtgwiki 審判）で誤った値も 5 枚見つかった。
    表は公式ソースで裏取りした少数精鋭（source_url 必須）なので、**表にある行は
    常に表が勝つ**（NULL 埋めも誤り訂正も同じ一文・冪等＝値が違う行だけ UPDATE）。"""
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('name_ja_manual')")
        if cur.fetchone()[0] is None:
            return 0
        cur.execute("""UPDATE mtg_cards_v2 c SET japanese_name = m.ja_name
                       FROM name_ja_manual m
                       WHERE c.card_name = m.card_name
                         AND c.japanese_name IS DISTINCT FROM m.ja_name""")
        return cur.rowcount


def run():
    conn = psycopg2.connect(**DB_CONFIG)
    add_japanese_columns(conn)

    # mtg_cards_v2 の英語名 → id マップ
    with conn.cursor() as cur:
        cur.execute("SELECT id, card_name FROM mtg_cards_v2")
        name_to_id = {row[1]: row[0] for row in cur.fetchall()}
    total_cards = len(name_to_id)
    print(f"更新対象: {total_cards} 件")

    # all_cards_scryfall.json を1パスで読み込み
    # 「printed_text が存在する中で released_at が最新」のものを採用
    print(f"JSON スキャン中: {JSON_FILE}")
    # {name: (released_at, ja_name, ja_text)}
    ja_data: dict[str, tuple[str, str | None, str | None]] = {}
    skipped_empty = 0

    # 別名義印刷（Universes Beyond の reskin）の除外:
    #   実害: Ragavan の japanese_oracle_text が「ジタン・トライバルが〜」になっていた。
    #   機序: fca（Final Fantasy: Through the Ages）の**日本語版**は printed_name が
    #   正規名（敏捷なこそ泥、ラガバン）なのに printed_text だけキャラ名で書かれており、
    #   しかも **日本語版の flavor_name は None**（＝ja だけ見ても判別できない）。
    #   決め手: **同じ set＋collector_number の英語版に flavor_name='Zidane Tribal' がある**。
    #   → 全言語を走査して「flavor_name を持つ印刷の (set, collector_number)」を集め、
    #     その組に一致する ja 印刷を除外する（セット名の黒リスト等の当て推量を使わない）。
    #   採用は「除外後に残った ja 印刷のうち released_at が最新」＝従来の方針は維持。
    flavor_keys: set[tuple] = set()
    ja_cands: dict[str, list[tuple]] = {}
    dropped_flavor = 0

    with open(JSON_FILE, "r", encoding="utf-8") as f:
        for card in tqdm(ijson.items(f, "item"), desc="JSON scan", mininterval=10):
            key = (card.get("set"), card.get("collector_number"))
            if card.get("flavor_name") or any(
                    fc.get("flavor_name") for fc in (card.get("card_faces") or [])):
                flavor_keys.add(key)

            if card.get("lang") != "ja":
                continue
            name = card.get("name", "").strip()
            if not name or name not in name_to_id:
                continue

            ja_name, ja_text = extract_printed_text(card)

            # printed_text が空、または日本語文字を含まない場合は採用しない
            # eoc 等のセットで英語テキストが誤って printed_text に格納されている問題を回避
            if not ja_text or not is_japanese(ja_text):
                skipped_empty += 1
                continue

            released_at = card.get("released_at", "1900-01-01")
            ja_cands.setdefault(name, []).append(
                (released_at, ja_name, ja_text, key))

    print(f"別名義印刷の (set, collector_number): {len(flavor_keys)} 組")
    for name, cands in ja_cands.items():
        clean = [c for c in cands if c[3] not in flavor_keys]
        dropped_flavor += len(cands) - len(clean)
        if not clean:
            continue                      # 正史の日本語印刷が無い＝採用しない
        best = max(clean, key=lambda c: c[0])
        # 名前だけは「最新印刷に無ければ、名前を持つ最新の印刷」から補う。
        # テキストの採用規則（released_at 最新）は変えない。
        best_name = best[1] or next(
            (c[1] for c in sorted(clean, key=lambda c: c[0], reverse=True) if c[1]),
            None)
        ja_data[name] = (best[0], best_name, best[2])
    print(f"別名義印刷として除外した ja 印刷: {dropped_flavor} 件")

    print(f"日本語データ収集完了: {len(ja_data)} 件")
    print(f"printed_text 空でスキップ: {skipped_empty} 件")

    # DB を一括 UPDATE（全件上書き）
    print("DB を更新中...")
    updated = 0
    not_found = 0

    with conn.cursor() as cur:
        for name, (released_at, ja_name, ja_text) in tqdm(
            ja_data.items(), desc="DB update", mininterval=5
        ):
            card_id = name_to_id.get(name)
            if not card_id:
                not_found += 1
                continue
            cur.execute("""
                UPDATE mtg_cards_v2
                SET japanese_name = %s, japanese_oracle_text = %s
                WHERE id = %s
            """, (ja_name, ja_text, card_id))
            updated += 1
            if updated % BATCH_COMMIT == 0:
                conn.commit()

    n_manual = apply_manual_names(conn)
    if n_manual:
        print(f"手動補正 name_ja_manual: {n_manual} 行を充填")
    conn.commit()
    conn.close()

    print(f"\n完了!")
    print(f"  日本語テキスト更新: {updated} 件")
    print(f"  日本語版なし（NULL のまま）: {total_cards - updated} 件")


def check_status():
    conn = psycopg2.connect(**DB_CONFIG)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM mtg_cards_v2")
        total = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*) FROM mtg_cards_v2
            WHERE japanese_oracle_text IS NOT NULL
        """)
        ja_text_count = cur.fetchone()[0]

        # 英語テキストが誤って入っていないか確認
        cur.execute("""
            SELECT COUNT(*) FROM mtg_cards_v2
            WHERE japanese_oracle_text IS NOT NULL
              AND japanese_oracle_text = oracle_text
        """)
        wrong_count = cur.fetchone()[0]

        cur.execute("""
            SELECT card_name, japanese_name, japanese_oracle_text
            FROM mtg_cards_v2
            WHERE card_name IN (
                'Farseek', 'Llanowar Elves', 'Counterspell',
                'Lightning Bolt', 'City of Traitors'
            )
        """)
        samples = cur.fetchall()

    conn.close()

    print(f"総カード数:                {total}")
    print(f"japanese_oracle_text あり: {ja_text_count}")
    print(f"日本語版なし（NULL）:      {total - ja_text_count}")
    print(f"英語テキストが誤って格納:  {wrong_count} 件")
    print("\nサンプル:")
    for en, ja_name, ja_text in samples:
        print(f"  {en} → {ja_name}")
        print(f"    {(ja_text or 'NULL')[:80]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true",
                        help="取得状況を確認する")
    args = parser.parse_args()

    if args.status:
        check_status()
    else:
        run()

"""gen_jaformat_questions.py — 《日本語名》書式ベンチの問題集を DB から決定的に生成する（2026-08-22）。

目的: 本人裁定「Opus に 1000 本ノックして全問正解ならそれでいい」（8/22 夕）＝大きい n で
「Opus low は日本語の問いに《正式な日本語名》で答える」を数字にする。手で 1000 問は書けないので、
型（テンプレート）× DB の実体（アーキタイプ・カード・統率者候補）で機械生成する。

型は 10 種（今日の 10 問のカテゴリを保つ: 複数枚の日本語名・土地名・読点入り・日本語版なし・両面・
英語名で問う・裸の日本語名で問う・合法性・フォーマット横断）。seed 固定で同じ問題集が再現する。
100 問ずつ切り出す: --offset 0 --n 100 → 1 便目、--offset 100 → 2 便目。

使い方:
  PYTHONPATH=src /mnt/new_hdd/my_rag_env/bin/python src/gen_jaformat_questions.py \
      --n 100 --offset 0 --out docs/me/bench/jaformat_1000/questions_001.csv
"""
import argparse, csv, random

FMT_JA = {"Standard": "スタンダード", "Pioneer": "パイオニア", "Modern": "モダン", "Legacy": "レガシー",
          "Vintage": "ヴィンテージ", "Pauper": "パウパー", "Duel Commander": "デュエルコマンダー",
          "Premodern": "プレモダン"}
TYPE_JA = {"Creature": "クリーチャー", "Instant": "インスタント", "Sorcery": "ソーサリー",
           "Artifact": "アーティファクト", "Enchantment": "エンチャント", "Planeswalker": "プレインズウォーカー"}


def q(cur, sql, params=()):
    cur.execute(sql, params)
    return cur.fetchall()


def build_pool(cur):
    pool = []   # (template_id, question, note)
    # 材料 --------------------------------------------------------------
    archetypes = q(cur, """
        SELECT format_name, archetype, COUNT(*) FROM deck_list
        WHERE archetype IS NOT NULL AND archetype NOT IN ('', 'Unknown') AND format_name = ANY(%s)
        GROUP BY 1, 2 HAVING COUNT(*) >= 40 ORDER BY 3 DESC""", (list(FMT_JA),))
    top_cards = q(cur, """
        SELECT s.format_name, c.card_name, c.japanese_name, c.type_line FROM card_format_strength s
        JOIN mtg_cards_v2 c ON c.id = s.card_id
        WHERE s.format_name = ANY(%s) AND c.type_line NOT ILIKE '%%Basic Land%%'
          AND s.play_decks >= 40
        ORDER BY s.format_name, s.play_decks DESC""", (list(FMT_JA),))
    edh_legends = q(cur, """
        SELECT c.card_name, c.japanese_name FROM edh_card_strength s
        JOIN mtg_cards_v2 c ON c.id = s.card_id
        WHERE c.type_line ILIKE '%%Legendary Creature%%' AND s.play_decks >= 20
        ORDER BY s.play_decks DESC LIMIT 300""")
    noja = [(f, en) for f, en, ja, t in top_cards if ja is None]
    dfc = [(f, en, ja) for f, en, ja, t in top_cards if ja and " // " in en]
    comma = [(f, en, ja) for f, en, ja, t in top_cards if ja and "、" in ja]
    # 問いの中では両面・分割カードは表の名前で呼ぶ（人の書き方に合わせる）
    withja = [(f, en, ja.split(" // ")[0]) for f, en, ja, t in top_cards if ja]

    # 型 ----------------------------------------------------------------
    for f, a, n in archetypes:
        pool.append(("T1_arch_cards", f"{FMT_JA[f]}の{a}の主要なカードを教えて", f"archetype={a} decks={n}"))
        pool.append(("T6_arch_lands", f"{FMT_JA[f]}の{a}でよく使われる土地を5枚挙げて", f"archetype={a}"))
    for f, en, ja in withja:
        pool.append(("T2_partner_ja", f"{FMT_JA[f]}で《{ja}》と一緒に入るカードは？", f"card={en}"))
        pool.append(("T4_legal_bare", f"{FMT_JA[f]}で{ja}はまだ使える？", f"card={en}（裸の日本語名で問う）"))
    for en, ja in edh_legends:
        name = f"《{ja}》" if ja else en
        pool.append(("T3_edh_partner", f"統率者戦で{name}を使うデッキでよく一緒に使われるカードは？", f"card={en}"))
    for f, en in noja:
        pool.append(("T10_noja", f"{en} と一緒に使われるカードは？", f"card={en}（日本語版なし）"))
        pool.append(("T5_en_question", f"{en} はどのフォーマットのどんなデッキで使われてる？", f"card={en}（日本語版なし）"))
    for f, en, ja in withja[::7]:
        pool.append(("T5_en_question", f"{en} はどのフォーマットのどんなデッキで使われてる？", f"card={en}"))
    for f, en, ja in dfc:
        front = ja.split(" // ")[0]
        pool.append(("T8_dfc", f"《{front}》を使うデッキは{FMT_JA[f]}でどれくらいある？主なカードも教えて", f"card={en}（両面/分割）"))
    for f, en, ja in comma:
        pool.append(("T9_comma", f"{FMT_JA[f]}で《{ja.split(' // ')[0]}》と一緒によく使われるカードは？", f"card={en}（読点入り）"))
    for f in FMT_JA:
        for t, tj in TYPE_JA.items():
            pool.append(("T7_top_type", f"{FMT_JA[f]}で今いちばん採用率の高い{tj}は？上位5枚", f"type={t}"))
    return pool


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--seed", type=int, default=20260822)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import psycopg2
    from db_config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    pool = build_pool(conn.cursor())
    conn.close()
    # 型ごとに均す: 型を回しながら 1 問ずつ取る（型内の順は seed で混ぜる）
    rnd = random.Random(a.seed)
    by_t = {}
    for t, qt, note in pool:
        by_t.setdefault(t, []).append((qt, note))
    for lst in by_t.values():
        rnd.shuffle(lst)
    order = sorted(by_t)
    seq, i = [], 0
    while any(by_t.values()):
        t = order[i % len(order)]
        if by_t[t]:
            qt, note = by_t[t].pop()
            seq.append((t, qt, note))
        i += 1
    chunk = seq[a.offset:a.offset + a.n]
    with open(a.out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "template", "question", "note"])
        for k, (t, qt, note) in enumerate(chunk, start=a.offset + 1):
            w.writerow([k, t, qt, note])
    from collections import Counter
    print(f"母集団 {len(seq)} 問・書き出し {len(chunk)} 問 → {a.out}")
    print(Counter(t for t, _, _ in chunk))


if __name__ == "__main__":
    main()

"""names.py — カード名の解決を 1 箇所に置く（2026-09-05 Step 6 作業 1）。

DESIGN 12（名前が複数あるものは面ごとの列で持つ）の帰結として、
「渡された名前 → DB の正式名（card_name）」の解決規則が 2 箇所に別実装で存在していた:

  - sisho/tools/partners.py の `_name_variants`
      `SELECT card_name … WHERE name_en_back IS NOT NULL AND (name_en_front = %s OR name_en_back = %s)`
  - sisho/tools/rules.py の `get_card_rulings` 内の直書き
      `SELECT card_name … WHERE name_en_front = %s OR name_en_back = %s
       ORDER BY (name_en_front = %s) DESC LIMIT 1`

やっていることは同じ「面の名前 → 正式名」で、片方だけ直す事故が起きうる（Step 4 報告 4-2）。
両者が呼ぶ 1 つの関数をここに置く。**返り値（相方検索・裁定検索の答え）は変えない**。

実測で確かめた 2 つの実装の意味差（2026-09-05・rag_dev）:
  - `name_en_front` も `name_en_back` も DB 内で重複なし（実測 0 件）＝1 つの名前に当たる行は最大 2 行
    （表で当たる行 1 つ・裏で当たる行 1 つ）。
  - 単面札は `card_name = name_en_front`（`name_en_back IS NULL AND card_name <> name_en_front` は 0 件）。
    つまり partners 側の `name_en_back IS NOT NULL` の絞りが落とすのは「その名前そのもの」だけで、
    partners は元の名前を候補の先頭に必ず持つ＝**絞りを外しても候補の集合は変わらない**（重複除去で消える）。
  - 裏面名が別の本物のカード名と同じ札は 21 枚（例: `Emeritus of Ideation // Ancestral Recall` の
    裏面名 `Ancestral Recall`）。`ORDER BY (name_en_front = %s) DESC` は本物のカード（表面一致）を先に置く
    ＝DESIGN 12 の「正式名 → 表面名 → 裏面名」の順そのもの。
  - 大文字小文字はどちらの実装も区別する（`=` の完全一致・`lower()` を掛けていない）＝ここでも変えない。

差は「解決した後の使い方」だけ: 裁定検索は先頭 1 つを採り、相方検索は候補を全部持って ANY() に渡す。
だから解決は候補の一覧（表面一致が先）を返し、絞り込みは呼ぶ側に置く。
"""
from sisho.db import LANE_LIGHT, _db


def resolve_face_name(name: str) -> list[str]:
    """面の名前（表面名・裏面名）を正式名（card_name）に解決した候補を返す。

    - 並びは「表面で当たった行が先」（DESIGN 12 の 正式名 → 表面名 → 裏面名）。
    - 一致しなければ空リスト。複数一致は推測せず、そのまま候補として全部返す
      （どれを採るかは呼ぶ側の仕事）。
    - 正式名（`表 // 裏`）そのものを渡しても面の列には一致しない＝空が返る。
      正式名で引く経路は呼ぶ側が先に持っている（裁定検索の完全一致・相方検索の候補の先頭）。
    """
    n = (name or "").strip()
    if not n:
        return []
    return [r[0] for r in _db(
        "SELECT card_name FROM mtg_cards_v2"
        " WHERE name_en_front = %s OR name_en_back = %s"
        " ORDER BY (name_en_front = %s) DESC", (n, n, n), lane=LANE_LIGHT)]


def face_display(en: str, ja: str | None, digital: bool = False) -> str:
    """面の完成形。ja があれば《ja/en》・無ければ「en（日本語版なし）」・digital の札は「en（日本語名未収録）」。"""
    if ja:
        return f"《{ja}/{en}》"
    note = "日本語名未収録" if digital else "日本語版なし"
    return f"{en}（{note}）"


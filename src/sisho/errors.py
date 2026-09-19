"""errors.py — 道具が返す error の種類。

なぜ要るか: 道具の返り値はクライアント（接続している LLM）に読ませる文章だが、文章だけだと
「入力が悪い」「そもそも該当が無い」「DB が落ちている」をクライアントが同じ「失敗」に丸めてしまう。
短い名前（error_kind）を 1 つ添えると、次の一手が名前で分かる（直して呼び直す／諦めて別の道具へ／待つ）。
番号は使わない（番号は表を引かないと意味が分からない＝返り値の自己完結に反する・DESIGN 1）。

文言の物差し（境目は正確に・想定外は素通し）:
  - 境目（道具が入力を解釈する場所）の error は「何が分からなかったか」と「次に何を試すか」を両方書く。
  - 想定外の例外は「失敗しました」に丸めず、生の例外文（PostgreSQL のエラー文など）を先頭 200〜400 字そのまま載せる。
  - 「該当なし」（引けたが無い）と「入力が不明・不正」は、文言も error_kind も分ける。

いま `error_kind` を実際に載せているのは **返り値がもともと JSON の道具**
（search_mtg_cards・mtg_probability・find_combos）だけ。返り値がテキストの道具
（lookup_mtg_rule・get_card_rulings・find_partner_cards・query_mtg_database・
describe_mtg_tables・verify_answer・mtg_rag_health）は**返り値の形を変えない**ので名前は載せていない
（クライアントの読み方が変わる＝別の裁定が要る）。ただし棚卸しの上ではテキストの道具の error にも
下の名前を割り当ててある＝将来テキスト側にも載せるときに語彙を作り直さないため。
"""

# ─── 境目（入力の解釈）で断る ───
EMPTY_QUERY = "empty_query"                     # 必須の入力が空
NO_MATCH = "no_match"                           # 入力は解釈できたが該当が無い（該当なし・裁定なし・共起なし）
UNKNOWN_FORMAT = "unknown_format"               # legalities にその format の鍵が無い
AMBIGUOUS_FORMAT = "ambiguous_format"           # 近い format の鍵が複数＝推測しない
UNKNOWN_OPTION = "unknown_option"               # 列挙の引数（kind・scope・order_by）が一覧に無い
OUT_OF_RANGE = "out_of_range"                   # 数値・件数が範囲外、または入力の組み合わせが定義できない
INVALID_IDENTIFIER = "invalid_identifier"       # 識別子（スキーマ名・表名）に使えない文字が入っている
UNKNOWN_TABLE = "unknown_table"                 # その名前の表が無い
SQL_REJECTED = "sql_rejected"                   # 入口の鞘が SQL を拒否（複文・SELECT/WITH 以外）

# ─── 想定外・外の世界 ───
BUSY = "busy"                                   # DB のスロット取りが順番待ちを超えた（失敗でなく混雑）
UPSTREAM_UNREACHABLE = "upstream_unreachable"   # 外部 API（Commander Spellbook）に届かない
RATE_LIMITED = "rate_limited"                   # 外部 API を守るための札ごとの枠
DB_ERROR = "db_error"                           # DB からの例外（生の例外文を素通しする）

#: 名前 → 意味（報告と試験のための一覧。ここに無い名前を返り値に載せない）
KINDS: dict[str, str] = {
    EMPTY_QUERY: "必須の入力が空（何を入れるかを文で言う）",
    NO_MATCH: "入力は解釈できたが該当が無い（次の引き方を文で言う）",
    UNKNOWN_FORMAT: "legalities にその format の鍵が無い（有効な鍵の一覧を添える）",
    AMBIGUOUS_FORMAT: "近い format の鍵が複数あり推測しない（候補を並べて選ばせる）",
    UNKNOWN_OPTION: "列挙の引数（kind・scope・order_by）が一覧に無い（使える値を並べる）",
    OUT_OF_RANGE: "数値・件数が範囲外、または入力の組み合わせが定義できない（範囲を書く）",
    INVALID_IDENTIFIER: "識別子（スキーマ名・表名）に使えない文字（使える文字と次の一手を書く）",
    UNKNOWN_TABLE: "その名前の表が無い（一覧の出し方を書く）",
    SQL_REJECTED: "入口の鞘が SQL を拒否した（複文・SELECT/WITH 以外）",
    BUSY: "DB のスロット取りが順番待ちの上限を超えた＝混雑（失敗ではない・待って呼び直す）",
    UPSTREAM_UNREACHABLE: "外部 API に届かない（この道具だけの障害・他の道具は影響なし）",
    RATE_LIMITED: "外部 API を守るための札ごとの枠（指定秒数待って呼び直す）",
    DB_ERROR: "DB からの例外（生の例外文を先頭 200〜400 字そのまま載せる）",
}


#: 返り値がテキストの道具の error を、文頭で上の名前に対応づける表。
#: **返り値には載せない**（テキストの道具の形は変えない）。道具ログの出口の行に
#: 書く outcome を決めるためだけに使う。並びは長い頭語が先（"SQL エラー:" と "エラー:" は
#: startswith なので衝突しないが、読む人のために特定的な方から並べる）。
TEXT_PREFIXES: tuple[tuple[str, str], ...] = (
    ("混雑:", BUSY),                          # _db_slot → query・describe・health
    ("拒否:", SQL_REJECTED),                   # query（複文・SELECT/WITH 以外）
    ("SQL エラー:", DB_ERROR),                 # query
    ("health 失敗:", DB_ERROR),                # health
    ("エラー:", DB_ERROR),                     # describe
    ("該当なし", NO_MATCH),                    # lookup_mtg_rule
    ("裁定なし", NO_MATCH),                    # get_card_rulings
    ("共起なし", NO_MATCH),                    # find_partner_cards
    ("テーブルなし:", UNKNOWN_TABLE),           # describe
    ("表名に使えない", INVALID_IDENTIFIER),      # describe
    ("スキーマ名に使えない", INVALID_IDENTIFIER),  # describe
    ("order_by が不正", UNKNOWN_OPTION),        # find_partner_cards
    ("scope が不正", UNKNOWN_OPTION),           # find_partner_cards
)

#: 道具ログの出口の行に書く outcome のうち、error_kind でないもの（error_kind の語彙＝KINDS を
#: 汚さないよう、ここは定数にしない＝KINDS と定数の一致を縫う試験の対象外）。
#:   ok        … error でない返り値
#:   error     … JSON の error だが error_kind が載っていない（＝載せ忘れの発見器）
#:   exception … 道具が例外を投げた（包みが記録してから再送出する）


def outcome_of(result) -> str:
    """道具の返り値から道具ログの outcome を決める（"ok" か error_kind か "error"）。

    返り値がもともと JSON の道具は `error_kind` をそのまま読む。テキストの道具は
    TEXT_PREFIXES で文頭を対応づける（返り値は変えない・読み取るだけ）。
    どちらでもなければ "ok"。**この関数は返り値に触らない**（観測専用）。
    """
    if not isinstance(result, str):
        return "ok"
    head = result.lstrip()[:400]
    if head.startswith("{"):
        if '"error' not in head:          # 成功の JSON（大多数）は解析しない
            return "ok"
        try:
            import json
            obj = json.loads(result)
        except Exception:
            return "ok"
        if isinstance(obj, dict) and "error" in obj:
            kind = obj.get("error_kind")
            return kind if isinstance(kind, str) and kind in KINDS else "error"
        return "ok"
    for prefix, kind in TEXT_PREFIXES:
        if head.startswith(prefix):
            return kind
    return "ok"


def err_json(kind: str, message: str, **extra) -> str:
    """JSON で返す道具の error の形を 1 箇所に（error・error_kind・道具ごとの追加の鍵）。

    鍵の順は error → error_kind → 追加（クライアントが先頭の 2 行で用が足りるように）。
    """
    assert kind in KINDS, f"未登録の error_kind: {kind}"
    import json
    out = {"error": message, "error_kind": kind}
    out.update(extra)
    return json.dumps(out, ensure_ascii=False, indent=1)

#!/usr/bin/env python
"""mcp_server.py — MTG RAG のシンプル MCP（2026-08-21・本人裁定「全部撤廃」版）。

2026-08-21 本人裁定: ルーター・門・腕（mtg_hybrid_search_v2 のパイプライン一式）を
撤廃し、MCP を DB 直結だけの最小構成にする。根拠は 8/11〜8/20 の実運用ログ
（93 呼び出し中 SQL 53 / search 7・search の中身もルーター ollama 待ち 6〜86 秒 vs
直行 65ms・脳は自前の ILIKE＋人気順で腕の仕事を代替済み）。利用者側に LLM（Claude）
が既にいる世界では、クエリ意図の解釈も曖昧文の束ねもクライアントの仕事＝サーバは
検証済みの事実層を速く正確に返すことに徹する。

設計メモ:
- 全道具がローカル PostgreSQL 直結。API サーバ（:8000）依存は撤去済み。
- search_mtg_cards は素の一致検索（名前優先→本文 AND・EDHREC 人気順）。
  LLM もルーターも呼ばない＝決定的・応答は 1 秒未満。
- 門と札（2026-09-07）: 接続 URL は発行ページ（/issue）のボタン一つで札（token_urlsafe）を
  発行し、/mcp/<札> で待ち受ける。GateASGI が札ごとのレート制限（60/分・find_combos 10/分）
  を課し、未知の札は 404（not found）で存在を漏らさない。旧パスは legacy 札として当面生かす。
- mcp SDK は /home/claude/pylibs（boto3 と同じ流儀）。

起動: PYTHONPATH=/home/claude/pylibs \
      /mnt/new_hdd/my_rag_env/bin/python <リポジトリ>/src/mcp_server.py
"""
import os

from mcp.server import MCPServer

# 役目ごとの包み（2026-09-05 Step 2・Step 3 で切り出し）。旧名で受けるのは tests と外の脚本が
# m._RateLimiter・m._db・m._log_tool のまま触れるようにするため（再輸出）。
from sisho.db import _db, _db_readonly, _db_slot          # noqa: F401（再輸出のみ）
from sisho.ratelimit import RateLimiter as _RateLimiter, RateLimitASGI as _RateLimitASGI
from sisho.sets_blurb import _SETS_BLURB, _SETS_HEAD, _startup_sets_blurb   # noqa: F401（道具の説明に埋まる・契約試験が読む）
from sisho.toollog import TOOL_LOG, TOOL_LOG_MAX, _log_tool, observed   # noqa: F401（_log_tool は再輸出のみ）
from sisho.tools import cards as _tool_cards
from sisho.tools import combos as _tool_combos
from sisho.tools import health as _tool_health
from sisho.tools import partners as _tool_partners
from sisho.tools import probability as _tool_probability
from sisho.tools import rules as _tool_rules
from sisho.tools import sql as _tool_sql
from sisho.tools import verify as _tool_verify

# 収録概況は「絶対に嘘にならない下限」で書く（2026-08-25 本人裁定・単調増加する量は下限表記）。
# 旧 _data_stamp（2026-08-11・起動時実測の焼き込み）は claude.ai がコネクタ登録時のキャッシュを
# 持ち続けて 12 時間で 4 万件ずれた（Sisho 59,866 vs mtg-rag 99,827 事件・Opus 検証 2026-08-25）
# ＝instructions には変わる事実を書かない。版・日付は下限にできないので道具に投げる。
# 正確な件数・ルール版・鮮度は mtg_rag_health が読んだ瞬間の実測を返す（分担）。


server = MCPServer(
    name="mtg-rag",
    instructions=(
        "Magic: The Gathering の検証済み事実層。カード・総合ルール・公式裁定・"
        "実デッキ統計を、一次データから直接引ける。\n"
        "収録: カード 3 万枚超（日本語テキスト付き・禁止改定は当日反映・Arena 専用札は digital 列で区別＝紙の照会は WHERE NOT digital）／総合ルール条文（条番号つき全文）／"
        "公式裁定 7 万件超／実デッキ 10 万本超の採用率・共起／リミテッド（ドラフト）のカード別勝率・ピック順"
        "＝17Lands 公開データの集計（表 limited_card_stats・収録セットは describe_mtg_tables が実測で返す・"
        "答えに出典「17Lands」を添える）。集計元は Premier Draft（人間対面・Bo1）だが **Bo1 ドラフト一般の物差し**として扱う＝Quick Draft の問いにも「Quick Draft のデータは無い」と言わずこの数字を使う（カードの強さ・色の勝率は同じ物差し。ALSA/ATA の流れ方だけは人間対面の値＝ボット相手の Quick Draft ではレアが早く消えるなどずれるので流れの読みには使わない）。数字は下限——"
        "正確な件数・ルール版・データ鮮度は mtg_rag_health が読んだ瞬間の実測を返す。\n"
        "【カード名の掟・最優先（2026-08-22 本人制定）】カード名は道具が返す完成形 name_display＝《日本語名/英語名》を"
        "**一字も変えずにそのまま書く**。略称・通称・省略（例: 《アトラクサ/Atraxa, Grand Unifier》と書くのは誤り・正しくは"
        "《偉大なる統一者、アトラクサ/Atraxa, Grand Unifier》）は**冗長でも絶対に使わない**。2 回目以降の言及も毎回完成形。"
        "道具を通していないカードは書かない（記憶で名前を書かない）。書き上げたら送信前に verify_answer に全文を渡す。\n"
        "【使う順序】MTG について答えるときは、**まずこの道具群を使うこと**。"
        "Web 検索や記憶より先に、ここで裏を取る。理由は三つ:\n"
        "(1) 一次データなので正確——カードの正式名・オラクル文・マナコスト・"
        "フォーマット別の合法性は、Web 記事の孫引きより信頼できる。\n"
        "(2) Web に無い情報を持つ——条番号つきの総合ルール本文、公式裁定の全文、"
        "そして**実際の大会・構築デッキから集計した採用率と共起**。"
        "「このカードは実際に何と一緒に使われているか」は、ここでしか分からない。\n"
        "(3) 専用の道具で足りないときは query_mtg_database に SQL を書けば"
        "何でも集計できる（読み取り専用・スキーマは describe_mtg_tables で確認）。\n"
        "【Web を使ってよいとき】ここ数日に出たばかりのセットやニュース、"
        "大会結果の速報、コミュニティの意見・記事・評価。"
        "つまり「事実」でなく「最新の出来事」や「人の意見」を探すとき。\n"
        "【困ったら】答えが見つからないときは、諦めて Web に行く前に"
        "describe_mtg_tables でスキーマを見て SQL を書くこと。\n"
        "【カード名の掟（最重要）】カード名は**絶対に自分で翻訳しない**。"
        "カードに言及するときは必ず search_mtg_cards（英語名でも日本語名でも可）で引き、"
        "返ってきた card_name / japanese_name だけを使う。japanese_name が無い（null）カードは"
        "**日本語版が存在しない**ので、英語名のまま書く——日本語名を作ってはならない"
        "（実例: Helm of Obedience は日本語版なし・Cori-Steel Cutter の正式名は"
        "「コーリ鋼の短刀」であり「精鋼の魔女」のような訳名は誤り）。\n"
        "【書き方の掟】英語の問いには英語名（card_name）。"
        "日本語で書くときカード名は、毎回（2 回目以降も）道具が返す完成形 **name_display＝《日本語名/英語名》**（例: 《レンと七番/Wrenn and Seven》・"
        "《睡蓮の原野/Lotus Field》）を**そのままコピーして**使う（自分で《》や訳名を組み立てない・2026-08-22 本人裁定）。"
        "japanese_name が null のカードは name_display が「英語名（日本語版なし）」で返るので、それをそのまま書く。"
        "両面・出来事・分割カードの**裏面**（出来事の呪文側・変身後・分割の片方）を指すときは、返り値の faces[].display か face_display（その面の完成形《裏の日本語名/裏の英語名》）を使う。"
        "name_display は表面の完成形なので、表の日本語名と裏の英語名を組み合わせない（2026-08-31）。"
        "デッキのアーキタイプ名（例: ロータス・コンボ、ラクドス・ミッドレンジ）は斜体（*…*）で書き、"
        "カード名の《》と見分けがつくようにする（試行中・2026-08-22）。\n"
        "【送信前の検査（必須）】日本語でカード名を含む答えを書き上げたら、送信する前に必ず verify_answer に全文を渡す。"
        "返ってきた「未確認の名前」があれば、そのカードを search_mtg_cards で引いて正式名に直す（自分の記憶の訳名は使わない）。"
        "未確認ゼロなら返ってきた修正版をそのまま答えにする。略称・省略は 2 回目以降でも禁止（冗長でよい）。\n"
        "このMCPは無料です。"),
)


# ─── 道具の登録（この順で相手側の一覧に並ぶ）─────────────────────────────
# 中身は sisho/tools/*.py。ここに残すのは「どの道具が・どの順で・どの説明で載るか」だけ
# （2026-09-05 Step 2〜3 の分割）。名前・説明・引数・登録順は契約試験
# tests/test_tool_contract.py が snapshot と突き合わせて縫っている。
#
# observed(...) は計時と出口の道具ログだけを足す薄い包み（2026-09-06 Step 7・sisho/toollog.py）。
# 返り値・例外は素通し、名前・docstring・署名は functools.wraps で保つので、相手側に見える
# 契約（description・引数の JSON Schema）は包む前と 1 ビットも変わらない
# ＝契約試験が包み越しの inspect.signature と JSON Schema を snapshot と突き合わせている。
# 入口の行（道具名と引数）は従来どおり道具の本体が先頭で書く。

# カード検索（素の一致検索＋17Lands 同伴）= sisho/tools/cards.py
search_mtg_cards = server.tool(
    name="search_mtg_cards",
    description=_tool_cards.DESCRIPTION)(observed(_tool_cards.search_mtg_cards))

# 確率計算の入口（2026-09-04 本人「あらゆる確率計算をどこかに格納して…」）= sisho/tools/probability.py
mtg_probability = server.tool(
    name="mtg_probability",
    description=_tool_probability.DESCRIPTION)(observed(_tool_probability.mtg_probability))

# Commander Spellbook（2026-09-04 本人 GO・外部 API を都度照会）= sisho/tools/combos.py
find_combos = server.tool(
    name="find_combos",
    description=_tool_combos.DESCRIPTION)(observed(_tool_combos.find_combos))

# 総合ルールと公式裁定（ローカル DB 直結・2026-08-10）= sisho/tools/rules.py
lookup_mtg_rule = server.tool(
    name="lookup_mtg_rule",
    description=_tool_rules.LOOKUP_MTG_RULE_DESCRIPTION)(observed(_tool_rules.lookup_mtg_rule))
get_card_rulings = server.tool(
    name="get_card_rulings",
    description=_tool_rules.GET_CARD_RULINGS_DESCRIPTION)(observed(_tool_rules.get_card_rulings))

# 共起（実デッキ集計・Phase 2 のデッキ壁打ち用）= sisho/tools/partners.py
find_partner_cards = server.tool(
    name="find_partner_cards",
    description=_tool_partners.DESCRIPTION)(observed(_tool_partners.find_partner_cards))

# 自由 SQL の口（2026-08-11 本人発案）= sisho/tools/sql.py
query_mtg_database = server.tool(
    name="query_mtg_database",
    description=_tool_sql.QUERY_MTG_DATABASE_DESCRIPTION)(observed(_tool_sql.query_mtg_database))

# 答案検査（2026-08-22 夕・本人裁定「選択肢 1」）= sisho/tools/verify.py
verify_answer = server.tool(
    name="verify_answer",
    description=_tool_verify.DESCRIPTION)(observed(_tool_verify.verify_answer))

# スキーマの窓（SQL を書く前に列を確認する口）= sisho/tools/sql.py
describe_mtg_tables = server.tool(
    name="describe_mtg_tables",
    description=_tool_sql.DESCRIBE_MTG_TABLES_DESCRIPTION)(observed(_tool_sql.describe_mtg_tables))

# 健全性確認（DB 実疎通・行数・鮮度）= sisho/tools/health.py
mtg_rag_health = server.tool(
    name="mtg_rag_health",
    description=_tool_health.DESCRIPTION)(observed(_tool_health.mtg_rag_health))


# ─── 旧名の再輸出（tests と外の脚本が mcp_server 越しに触る名前）───────────
# 契約試験 test_module_reexports が毎回検査する。注意: これは束縛の写しなので、
# 試験で差し替える（monkeypatch）ときは中身のモジュール側を指すこと
# （例: sisho.tools.combos._spellbook_post・sisho.db._db）。ここを差し替えても道具には届かない。
ARENA_FORMATS = _tool_cards.ARENA_FORMATS
_attach_limited_stats = _tool_cards._attach_limited_stats
_DRAFT_RECENT_N = _tool_cards._DRAFT_RECENT_N
_limited_sets = _tool_cards._limited_sets
_resolve_draft_set = _tool_cards._resolve_draft_set
_sets_line = _tool_cards._sets_line
_archetype_lines = _tool_cards._archetype_lines
_limited_archetypes = _tool_cards._limited_archetypes
_SPELLBOOK_URL = _tool_combos._SPELLBOOK_URL
_SPELLBOOK_TIMEOUT = _tool_combos._SPELLBOOK_TIMEOUT
_spellbook_post = _tool_combos._spellbook_post
_display_map = _tool_combos._display_map
_name_variants = _tool_partners._name_variants
_attach_japanese_names = _tool_sql._attach_japanese_names
_TABLE_NOTES = _tool_sql._TABLE_NOTES
_ident = _tool_sql._ident
_limited_sets_note = _tool_sql._limited_sets_note
_PROB_KINDS = _tool_probability._PROB_KINDS
_names = _tool_verify._names
_NAME_CACHE = _tool_verify._NAME_CACHE
_JA_STOP = _tool_verify._JA_STOP


# レート制限（配布前の門・2026-09-02）は sisho/ratelimit.py へ切り出した（2026-09-05 Step 2）。
# 旧名 _RateLimiter／_RateLimitASGI で届くように冒頭で別名輸入している（切り出し先の公開名は
# RateLimiter／RateLimitASGI）。設計の経緯と実測値はそちらの注記に丸ごと移してある。


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "http":
        # リモート版（一時公開試験・2026-08-10）: 127.0.0.1 に束縛し、外への口は
        # Cloudflare 即席トンネルが持つ。DNS rebinding 防御はトンネルの Host 名
        # （毎回ランダム）が allowed_hosts に書けないため、この一時試験に限り無効化。
        # 恒久のリモート版（工程表 3 番）では allowed_hosts を固定ドメインで縫うこと。
        from mcp.server.transport_security import TransportSecuritySettings
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
        # 待ち受けパス（2026-08-22・本人裁定「2 で」）: Funnel のホスト名は CT ログで公開される
        # ので、秘密は URL のパスに持たせる。既定 /mcp・本番は unit の EnvironmentFile
        # （~/.config/mtg-rag/mcp.env・claude 専用ホーム）から MCP_HTTP_PATH を注入。
        http_path = os.environ.get("MCP_HTTP_PATH", "/mcp")
        # stateless（2026-08-29・公開サーバーで採用）: claude.ai のコネクタは道具呼び出しをセッション ID
        # 無しで送ってくることがあり、既定（stateful）だと「Bad Request: Missing session ID」で
        # 全滅する（箱の実測・health 1 回成功→以降 400）。道具はすべて独立（サーバ発の通知なし）
        # なので、リクエストごとに独立処理しても失うものは無い。MCP_STATELESS=1 で有効。
        stateless = os.environ.get("MCP_STATELESS", "") in ("1", "true", "yes")
        import uvicorn
        app = server.streamable_http_app(
            streamable_http_path=http_path,
            stateless_http=stateless,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
            host="127.0.0.1")
        # 停止は 3 秒で切り上げる（2026-08-31 17:45 の実測: 箱の再起動で uvicorn が「接続が閉じるのを待つ」まま
        # systemd の TimeoutStopSec=15 に掛かり SIGKILL → 'timeout' 失敗 → OnFailure（Discord＋ビープ）が鳴った。
        # claude.ai のコネクタが SSE を掴んだままにするので、待っても閉じない。SDK の run() は uvicorn.Config に
        # graceful の上限を渡さないため、ここで uvicorn を直接組む。道具は 1 秒未満で返るので 3 秒あれば取りこぼさない）
        # 門と札の外皮（2026-09-07 小片 8・sisho/gate.py を参照）。
        # 札ごとのレート制限・発行ページ（/issue）・旧パス互換を GateASGI が束ねる。
        from sisho.gate import GateASGI
        app = GateASGI.from_env(app, inner_path=http_path)
        kw = _uvicorn_kwargs(port, server.settings.log_level.lower())
        uvicorn.run(app, **kw)
    else:
        server.run("stdio")


def _uvicorn_kwargs(port: int, log_level: str) -> dict:
    """uvicorn.run に渡す引数（試験用に関数へ切り出し）。

    uvicorn の既定のアクセスログは要求行＝パス＝札の全文を書く。
    門の [gate] と道具ログで観測は足りる。
    Opus のレビュー A-1・2026-09-07。
    """
    return {
        "host": "127.0.0.1",
        "port": port,
        "log_level": log_level,
        "timeout_graceful_shutdown": 3,
        "access_log": False,
    }


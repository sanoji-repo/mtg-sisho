"""limited.py — 17Lands リミテッド統計・ドラフトセット解決・プール所属判定をここ 1 箇所に置く。

設計の不変条件:
1. ドラフトのプール所属（in_draft_set）と統計の結合は limited_card_stats（expansion × event_type × db_card_name）のみを正本とし、
   mtg_cards_v2.set_codes は一切参照しない（スペシャルゲスト・ボーナスシート・リマスターの欠落と、Starter/Jumpstart/A- の混入を防ぐ）。
2. 統計照会の「成功して 0 行（不在 → in_draft_set=False）」と「DB 例外・タイムアウト（照会不能 → in_draft_set を出さない）」を混同しない。
3. 同じ db_card_name に複数の統計行が衝突した場合は黙って上書きせず検出する。
4. セット名の部分一致は候補が 1 件に一意に決まるときだけ採り、複数セットに当たる短い語（「the」「of」等）で推測しない。
5. セット一覧の照会の失敗を不在（None）に丸めない: 利用者の入力を判定する入口（cards・draft）は propagate_db_error=True で
   呼んで障害を区別する（False のままだと障害を「手持ちに無い」と答えてしまう）。
"""
from __future__ import annotations

import os
import time
from typing import Callable

from sisho.db import LANE_LIGHT, _db as _default_db, _db_readonly as _default_db_readonly

_db = _default_db
_db_readonly = _default_db_readonly

DEFAULT_EVENT_TYPE = "PremierDraft"
# draft_set が無いとき同伴する最新 N セット。注意: 発売日の最新が今のドラフト環境とは限らない（実測: 最新は MSH
# だが Arena の Quick Draft は SOS）＝既定は最新 N で取りこぼしを減らし、本命は呼び出し側の draft_set
_DRAFT_RECENT_N = int(os.environ.get("MCP_DRAFT_RECENT_SETS", "2"))

SETS_SQL = (
    "SELECT s.expansion, m.set_name, m.released_at, extract(year from m.released_at)::int"
    " FROM (SELECT DISTINCT expansion FROM limited_card_stats WHERE event_type = 'PremierDraft') s"
    " LEFT JOIN mtg_sets m ON lower(m.set_code) = lower(s.expansion)"
    " WHERE s.expansion NOT ILIKE 'cube%%' ORDER BY m.released_at DESC NULLS LAST, s.expansion"
)

_sets_cache: dict = {"t": 0.0, "rows": []}


def _limited_sets(
    _db: Callable | None = None,
    *,
    propagate_db_error: bool = False,
) -> list[dict]:
    """limited_card_stats の収録セットを発売日の新しい順に（記号・名前・発売日）。Cube は除く。

    5 分キャッシュは既定の _db のときだけ（差し替えた _db の試験の結果がキャッシュに混ざらないように）。
    照会の失敗は既定で []、propagate_db_error=True なら送出する（不変条件 5）。"""
    if _db is None:
        _db = globals()["_db"]
    is_default = _db is _default_db
    if is_default and time.time() - _sets_cache["t"] < 300 and _sets_cache["rows"]:
        return _sets_cache["rows"]
    try:
        rows = _db(SETS_SQL, (), lane=LANE_LIGHT)
    except Exception:
        if propagate_db_error:
            raise
        return []
    out = [{"code": r[0], "name": r[1], "released_at": str(r[2]) if r[2] else None} for r in rows]
    if is_default:
        _sets_cache.update(t=time.time(), rows=out)
    return out


def _resolve_draft_set(
    s: str | None,
    *,
    allow_cube: bool = False,
    propagate_db_error: bool = False,
    _db: Callable | None = None,
) -> str | None:
    """『#SOS』『LimitedSOS』『sos』『Secrets of Strixhaven』を記号 SOS に。手持ちに無いか、部分一致が複数のセットに
    当たれば None（推測しない・呼ぶ側の文面は「無いか、1 つに決まらない」）。

    - コード完全一致（大文字小文字無視）→ セット名完全一致 → セット名部分一致（4 文字以上かつ候補が 1 件に一意のときのみ）。
    - allow_cube=True（draft_pack_stats 用）のときは、通常セット一覧になければ limited_card_stats.expansion の完全一致も引く
      （_limited_sets は Cube を除くが統計は実在する＝Cube_-_Powered は 538 行）。
      また DB 障害を不在 None に丸めず呼び出し元へ送出する。
    """
    if not s:
        return None
    if _db is None:
        _db = globals()["_db"]
    key = str(s).strip().lstrip("#").strip()
    low = key.lower()
    for pre in ("limited", "quickdraft", "quick draft", "quick", "premierdraft", "premier", "draft", "sealed"):
        if low.startswith(pre):
            key = key[len(pre):].strip(" _-:：")
            low = key.lower()
    if not key:
        return None
    sets = _limited_sets(_db=_db, propagate_db_error=propagate_db_error)
    for x in sets:
        if x["code"].lower() == low:
            return x["code"]
    for x in sets:
        if x["name"] and x["name"].lower() == low:
            return x["code"]
    if len(low) >= 4:
        hits = [
            x["code"]
            for x in sets
            if x["name"] and (low in x["name"].lower() or x["name"].lower() in low)
        ]
        if len(hits) == 1:
            return hits[0]
    if allow_cube:
        hit = _db(
            "SELECT expansion FROM limited_card_stats"
            " WHERE lower(expansion) = lower(%s) AND event_type = %s LIMIT 1",
            (key, DEFAULT_EVENT_TYPE),
            lane=LANE_LIGHT,
        )
        if hit:
            return hit[0][0]
    return None


def _sets_line(_db: Callable | None = None) -> str:
    return "・".join(f"{x['code']}（{x['name']}）" if x["name"] else x["code"] for x in _limited_sets(_db=_db))


def _mark_draft_set(cards: list[dict], expansion: str) -> None:
    """各カードに in_draft_set（指定セットの 17Lands 統計行が在るか）を立てる。

    背景: ChatGPT で画像から読んだ曖昧な名前を「聖遺」のような部分文字列で引く使い方がある。
    draft_set は **検索を絞らない**（17Lands 統計を添えるセットの指定）ので
    別セットのカードが EDHREC 人気順に並び、唯一のそのセット収録カードが 5 番目に沈む。
    どれがそのセットかを**クライアントが判断できない**状態は、
    「誤読 → 部分一致 → 複数候補 → もっともらしい 1 枚を選ぶ」の入口になる。
    絞る・並べ替えるのでなく、**事実を足して判断材料を渡す**（列 → 並べ方の順番）。

    判定条件: ドラフトのプールは mtg_cards_v2.set_codes では定義できない
    （実測: set_codes で判定するとスペシャルゲスト・ボーナスシート・リマスターの 24 セット 1,789 枚が落ち、
    逆に Starter/Jumpstart/Box Topper 等のドラフト外カード 11 セット 343 枚＋アルケミー A- 再調整版 149 枚が混入した）。
    このため limited_card_stats の統計行だけを正本にする（上の件数は実測時点の値で、別案を退けた根拠）。

    注意: 統計照会が成功した後にだけ呼ぶこと（照会失敗時に呼ぶと全員 False に化ける）。
    通常は下の attach_and_mark_limited を通じて一括で行う。
    """
    up = expansion.upper()
    for c in cards:
        stats = c.pop("limited_stats", None)
        c["in_draft_set"] = any(str(s.get("set", "")).upper() == up for s in (stats or []))
        if stats is not None:
            c["limited_stats"] = stats


def _query_limited_stats(
    cards: list[dict],
    sets: list[str] | None = None,
    *,
    event_type: str | None = DEFAULT_EVENT_TYPE,
    _db: Callable | None = None,
) -> tuple[dict[str, list[str]], bool]:
    """内部実装: (elsewhere, ok) を返す。DB 例外時は ({}, False) を返し、成功した 0 行 ({}, True) と区別する。

    検索結果の各カードに、17Lands 集計（limited_card_stats）があればセット別に同伴する。
    背景: クライアントが SOS のカードの GIH WR を聞かれても、mtg_cards_v2 と information_schema を手探りするだけで
    表に辿り着けない（instructions は claude.ai に届かない＝返り値に載っていないものは無いのと同じ）。
    行が無いカードにはキーを出さない（不在は無言でなく、クライアントが「無い」と読めるように limited_stats_note で線引き）。
    sets（記号の一覧）に入るセットの行だけ同伴し、それ以外のセットは card_name→[記号] で返す（道しるべ用）。"""
    names = [c["card_name"] for c in cards if c.get("card_name")]
    if not names:
        return {}, True
    if _db is None:
        _db = globals()["_db"]
    try:
        if event_type:
            rows = _db(
                "SELECT db_card_name, expansion, event_type, gih_games, gih_wr, oh_wr, gd_wr, alsa, ata"
                " FROM limited_card_stats WHERE db_card_name = ANY(%s) AND event_type = %s"
                " ORDER BY db_card_name, expansion",
                (names, event_type),
                lane=LANE_LIGHT,
            )
        else:
            rows = _db(
                "SELECT db_card_name, expansion, event_type, gih_games, gih_wr, oh_wr, gd_wr, alsa, ata"
                " FROM limited_card_stats WHERE db_card_name = ANY(%s)"
                " ORDER BY db_card_name, expansion",
                (names,),
                lane=LANE_LIGHT,
            )
    except Exception:
        return {}, False
    by: dict[str, list] = {}
    elsewhere: dict[str, list[str]] = {}
    seen_keys: set[tuple[str, str, str]] = set()
    for n, ex, ev, g, gih, oh, gd, alsa, ata in rows:
        key = (n, ex, ev)
        if key in seen_keys:
            raise RuntimeError(f"limited_card_stats duplicate resolved key: {key!r}")
        seen_keys.add(key)
        if sets is not None and ex not in sets:
            if not ex.lower().startswith("cube"):
                elsewhere.setdefault(n, []).append(ex)
            continue
        by.setdefault(n, []).append({
            "set": ex, "event": ev, "gih_games": g,
            "gih_wr": float(gih) if gih is not None else None,
            "oh_wr": float(oh) if oh is not None else None,
            "gd_wr": float(gd) if gd is not None else None,
            "alsa": float(alsa) if alsa is not None else None,
            "ata": float(ata) if ata is not None else None,
            "source": "17Lands",
        })
    for c in cards:
        if c.get("card_name") in by:
            c["limited_stats"] = by[c["card_name"]]
    return elsewhere, True


def _attach_limited_stats(
    cards: list[dict],
    sets: list[str] | None = None,
    *,
    event_type: str | None = DEFAULT_EVENT_TYPE,
    _db: Callable | None = None,
) -> dict[str, list[str]]:
    """互換口: 各カードに limited_stats を同伴し、対象外セットの道しるべ {card_name: [set]} を返す。"""
    elsewhere, _ok = _query_limited_stats(cards, sets, event_type=event_type, _db=_db)
    return elsewhere


def attach_and_mark_limited(
    cards: list[dict],
    sets: list[str] | None,
    *,
    requested_set: str | None = None,
    event_type: str | None = DEFAULT_EVENT_TYPE,
    _db: Callable | None = None,
) -> tuple[dict[str, list[str]], bool]:
    """統計付与と in_draft_set 判定を一体で行う。

    統計照会が成功したとき（ok=True）だけ in_draft_set を付与する。
    照会が失敗したとき（ok=False）は in_draft_set を付けない（障害を「ドラフト外」に丸めない）。
    """
    elsewhere, ok = _query_limited_stats(cards, sets, event_type=event_type, _db=_db)
    if ok and requested_set:
        _mark_draft_set(cards, requested_set)
    return elsewhere, ok


def fetch_pack_stats(
    expansion: str,
    card_names: list[str],
    *,
    event_type: str = DEFAULT_EVENT_TYPE,
    _db: Callable | None = None,
) -> dict[str, tuple]:
    """draft_pack_stats 用: 指定セット×イベントの統計行を {db_card_name: (gih_wr, gih_games, alsa, gp_wr, oh_wr, gd_wr, ata)} で返す。
    DB 障害は握り潰さず呼び出し元へ送出する。同一 db_card_name に複数行があれば RuntimeError を送出する。
    """
    if not card_names:
        return {}
    if _db is None:
        _db = globals()["_db"]
    rows = _db(
        "SELECT db_card_name, gih_wr, gih_games, alsa, gp_wr, oh_wr, gd_wr, ata"
        "  FROM limited_card_stats"
        " WHERE expansion = %s AND event_type = %s AND db_card_name = ANY(%s)",
        (expansion, event_type, list(card_names)),
        lane=LANE_LIGHT,
    )
    stats: dict[str, tuple] = {}
    for r in rows:
        name = r[0]
        if name in stats:
            raise RuntimeError(f"limited_card_stats duplicate db_card_name in {expansion}/{event_type}: {name!r}")
        stats[name] = r[1:]
    return stats


class SetPoolError(RuntimeError):
    """セットのカード一覧が組めない（同じ db_card_name の重複行など）。文にカード名を入れない（ログに残さない）。"""


#: fetch_set_pool の行の鍵（SQL の列の順と同じ）
SET_POOL_KEYS = (
    "card_name", "name_display", "japanese_name", "name_en_front", "name_en_back", "name_ja_front", "name_ja_back",
    "layout", "mana_cost", "cmc", "type_line", "card_faces", "oracle_text", "produced_mana",
    "removal", "draw_count", "draw_x", "tutor", "gih_wr", "gih_games", "alsa", "ata",
)

SET_POOL_SQL = (
    "SELECT c.card_name, c.name_display, c.japanese_name, c.name_en_front, c.name_en_back, c.name_ja_front,"
    "       c.name_ja_back, c.layout, c.mana_cost, c.cmc, c.type_line, c.card_faces_json, c.oracle_text,"
    "       c.produced_mana, c.removal, c.draw_count, c.draw_x, c.tutor,"
    "       s.gih_wr, s.gih_games, s.alsa, s.ata"
    "  FROM limited_card_stats s"
    "  LEFT JOIN mtg_cards_v2 c ON c.card_name = s.db_card_name"
    " WHERE s.expansion = %s AND s.event_type = %s"
)


def fetch_set_pool(expansion: str, *, _db: Callable | None = None) -> list[dict]:
    """draft_pool 用: 照合済みのセット記号のカード一覧（カードの列＋17Lands の統計）を SQL 1 本で読む。

    引数に渡すのは照合済みのセット記号とイベントだけ（カード名は SQL に載せない＝遅い文・打ち切られた文の
    PostgreSQL のログに名前が残らない）。
    expansion は _resolve_draft_set（allow_cube=False＝入力を SQL に渡さない経路）の戻り値だけを渡すこと。
    同じ db_card_name の行が二つある・カード表と結べない行がある（db_card_name が NULL か、カード表に無い）は、
    黙って上書き・脱落させずに SetPoolError（不変条件 3・INNER JOIN だと結べない行が SQL の段で消えて検出できないため LEFT JOIN で読む。
    実測: Cube を除く PremierDraft の結べない行は 0）。DB の例外は送出する。
    利用者の状態ではない公開のデータだが、控えは持たない（毎回読む）。
    軽いレーン（lane=LANE_LIGHT）で読む＝セット記号とイベントの 2 値の固定の SQL 1 本で、実測 25〜58ms。
    """
    if _db is None:
        _db = globals()["_db"]
    rows = _db(SET_POOL_SQL, (expansion, DEFAULT_EVENT_TYPE), lane=LANE_LIGHT)
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        d = dict(zip(SET_POOL_KEYS, r))
        if d["card_name"] is None:
            raise SetPoolError(f"{expansion} のカード一覧に、カード表と結べていない行がある（17Lands の名前の結び付けの欠け）")
        if d["card_name"] in seen:
            raise SetPoolError(f"{expansion} のカード一覧に同じカードの行が二つある（17Lands の名前の結び付けの重複）")
        seen.add(d["card_name"])
        out.append(d)
    return out


def _limited_archetypes(
    sets: list[str],
    *,
    event_type: str | None = DEFAULT_EVENT_TYPE,
    _db: Callable | None = None,
) -> dict:
    """セットごとの色の組み合わせ別勝率（2 色・タッチ無し・limited_color_stats）を search の返り値に同伴する。

    背景: クライアントは「アーキタイプ別勝率を参考にしながら」と言われないと、SOS に無いアーキタイプ
    （有効色）でデッキを作り出す。返り値に載っていない表はクライアントが引かない前提なので、
    カードの統計を付けたセットについて 2 色の組み合わせをプレイ数順で丸ごと（10 行）載せる（勝率順だと
    母数 100 戦の組み合わせが上位に混ざって読み違える＝SOS で実測）。share_pct はセット内の割合＝成立しない
    組み合わせ（SOS の WU 0.04% 等）をクライアントが構造で見分けるための列。
    baseline_wr はセット全体の勝率（limited_format_stats のランク帯合算）＝組み合わせの良し悪しの基準線。
    色の列が無いセット（STX）や表が無い環境では空。"""
    if not sets:
        return {}
    if _db is None:
        _db = globals()["_db"]
    try:
        if event_type:
            rows = _db(
                "SELECT expansion, main_colors, games, wins FROM limited_color_stats"
                " WHERE expansion = ANY(%s) AND event_type = %s AND NOT splash AND length(main_colors) = 2"
                " ORDER BY expansion, games DESC",
                (sets, event_type),
                lane=LANE_LIGHT,
            )
            base = _db(
                "SELECT expansion, sum(games), sum(wins) FROM limited_format_stats"
                " WHERE expansion = ANY(%s) AND event_type = %s GROUP BY 1",
                (sets, event_type),
                lane=LANE_LIGHT,
            )
        else:
            rows = _db(
                "SELECT expansion, main_colors, games, wins FROM limited_color_stats"
                " WHERE expansion = ANY(%s) AND NOT splash AND length(main_colors) = 2"
                " ORDER BY expansion, games DESC",
                (sets,),
                lane=LANE_LIGHT,
            )
            base = _db(
                "SELECT expansion, sum(games), sum(wins) FROM limited_format_stats"
                " WHERE expansion = ANY(%s) GROUP BY 1",
                (sets,),
                lane=LANE_LIGHT,
            )
    except Exception:
        return {}
    out: dict[str, dict] = {}
    for ex, g, w in base:
        out[ex] = {"baseline_wr": round(w / g, 4) if g else None, "color_pairs": []}
    tot: dict[str, int] = {}
    for ex, _mc, g, _w in rows:
        tot[ex] = tot.get(ex, 0) + (g or 0)
    for ex, mc, g, w in rows:
        out.setdefault(ex, {"baseline_wr": None, "color_pairs": []})["color_pairs"].append(
            {
                "colors": mc,
                "games": g,
                "share_pct": round(100.0 * g / tot[ex], 2) if tot.get(ex) else None,
                "wr": round(w / g, 4) if g else None,
                "source": "17Lands",
            }
        )
    return {ex: v for ex, v in out.items() if v["color_pairs"]}


def _archetype_lines(code: str, *, _db: Callable | None = None) -> str:
    """返り値に添える、そのセットの色の組み合わせ表（1 行）。"""
    a = _limited_archetypes([code], _db=_db).get(code)
    if not a:
        return ""
    pairs = "・".join(f"{p['colors']} {p['share_pct']}% wr{p['wr']}" for p in a["color_pairs"])
    return (
        f"\n[#{code} の色の組み合わせ（17Lands・2 色・タッチ無し・プレイ数順・share% と勝率・基準線 {a['baseline_wr']}）] {pairs}"
        "\n（share が 1% 未満の組み合わせはそのセットでは成立していない）"
    )


def _limited_sets_note(_db_readonly: Callable | None = None) -> str:
    """limited_card_stats の収録セットを実測で一行に（無いセットを書かないための一覧・describe_mtg_tables 用）。表が無ければ空文字。"""
    if _db_readonly is None:
        _db_readonly = globals()["_db_readonly"]
    try:
        _, srows = _db_readonly(
            "SELECT expansion, event_type, count(*) FROM limited_card_stats"
            " GROUP BY 1, 2 ORDER BY 2, 1",
            200,
        )
    except Exception:
        return ""
    if not srows:
        return ""
    by_ev: dict[str, list[str]] = {}
    for exp, ev, n in srows:
        by_ev.setdefault(ev, []).append(f"{exp}({n})")
    parts = [f"{ev}: " + "・".join(v) for ev, v in by_ev.items()]
    return (
        "  収録セット（expansion 列の値・括弧はカード数・実測）: "
        + "／".join(parts)
        + "。ここに無いセットは持っていない（無いものは書かない）。event_type は集計元の名前（PremierDraft）＝"
        "Bo1 ドラフト一般（Quick Draft を含む）の物差しとして使う。"
    )

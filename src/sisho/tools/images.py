"""images.py — カード画像を並べる道具 show_card_images（MCP Apps の画面つき）。

登録（server.tool）は mcp_server.py 側。ここは DESCRIPTION・素の関数・画面の HTML の読み込みだけを持つ。
画面は同じ階層の card_images.html（HTML 一枚・外のスクリプトを読まない）。画面向けのデータは結果の
_meta に載せ、本文（content）には《日本語名/英語名》の名前と画像の URL を文字で書く＝画面を出せないクライアントにも用が足りる。
structuredContent は使わない（クライアントによっては AI にも見えて本文と二重になる・契約試験が確かめる）。
"""
from pathlib import Path

from mcp.types import CallToolResult, TextContent

from sisho import errors
from sisho.db import LANE_LIGHT, _db
from sisho.names import (
    display_map_for_en_names,
    not_found_hint,
    preview_unavailable_note,
    resolve_names_to_en_ex,
)
from sisho.toollog import _log_tool

#: 画面の資源の住所（mcp_server.py が Apps に載せ、道具の meta がここを指す）
CARD_IMAGES_URI = "ui://sisho/card-images.html"

#: 画面の HTML（パスはこのファイル基準。起動時に一度だけ読む）
HTML = (Path(__file__).with_name("card_images.html")).read_text(encoding="utf-8")

MAX_CARDS = 10

DESCRIPTION = (
    "【カード名の掟】カード名は返り値の完成形《日本語名/英語名》を一字も変えず書く。"
    "【利用者がカードの絵（カード画像）を見たいときはこれ】カード名（英語名・日本語名どちらでも・最大 10 枚）を渡すと、"
    "日本語版の画像があればそれを・無ければ英語版の画像を、画面に並べて見せる（画像は Scryfall の配信）。"
    "画面を出せる客（MCP Apps に対応したクライアント）では画像を並べた画面が出る。出せない客には、"
    "完成形の名前と画像の URL を文字で返す。名前が見つからない・1 枚に決まらないものは本文に理由と候補を書く。"
    "11 枚以上渡したら最初の 10 枚だけ扱う。カードの画像と文章の著作権は Wizards of the Coast。"
    "画像の著作権表記と絵師名を隠したり切り抜いたりしない。")

_NOTICE = (
    "画面を表示できる客では、この画像を並べた画面が出る。画像は Scryfall の配信、カードの画像と文章の著作権は "
    "Wizards of the Coast。画像の著作権表記と絵師名を隠したり切り抜いたりしない。"
    "カード名は上の完成形をそのまま書く")

_LANG_LABEL = {"ja": "日本語版の画像", "en": "英語版の画像"}


def show_card_images(names: list[str]) -> CallToolResult | str:
    _log_tool("show_card_images", {"n": len(names or []), "names": names})
    # 注釈は広く取ってある: 成功は画面向けの _meta つきの CallToolResult・失敗は他の道具と同じ文字列で返す
    # （_tool は structured_output=False なので SDK は返り値の注釈を見ない）
    given = [n.strip() for n in (names or []) if isinstance(n, str) and n.strip()]
    if not given:
        return errors.err_json(
            errors.EMPTY_QUERY,
            "names が空です。画像を見たいカード名（英語名・日本語名どちらでも・最大 10 枚）を 1 枚以上入れて呼び直す")
    cut = len(given) > MAX_CARDS
    given = given[:MAX_CARDS]

    resolved, ambiguous = resolve_names_to_en_ex(given, _db=_db)
    ordered: list[str] = []          # 入力順・同じカードに決まった入力は 1 枚にまとめる
    for n in given:
        en = resolved.get(n)
        if en and en not in ordered:
            ordered.append(en)
    missing = [n for n in given if n not in resolved and n not in ambiguous]
    amb_names = sorted({c for cs in ambiguous.values() for c in cs})

    if ordered or amb_names:
        disp, pnotes = display_map_for_en_names(ordered + amb_names, fallback_suffix="（DB 未収録）", _db=_db)
    else:
        disp, pnotes = {}, {}
    amb_lines = [f"「{k}」→ {'・'.join(disp.get(c, c) for c in cs)}" for k, cs in ambiguous.items()]
    miss_lines = [f"「{n}」: {not_found_hint(n, 'search_mtg_cards で綴りを確かめて呼び直す', _db=_db)}" for n in missing]

    if not ordered:
        extra = {"ambiguous_names": ambiguous} if ambiguous else {}
        return errors.err_json(
            errors.NO_MATCH,
            "画像を出せるカードが 1 枚も決まらなかった。"
            + ("見つからない名前: " + "／".join(miss_lines) + "。" if miss_lines else "")
            + ("1 枚に決まらない名前（同じ名前のカードが複数）: " + "／".join(amb_lines)
               + "。どれかの英語の正式名（両面は 表 // 裏）で呼び直す。" if amb_lines else ""),
            **extra)

    # 同じ英語名の行が複数あるとき（紙とデジタル）は紙を先に取る＝names.py の解決と同じ order by digital, id
    img: dict[str, tuple[str | None, str | None]] = {}
    for cn, url_en, url_ja in _db(
            "SELECT card_name, image_url, image_url_ja FROM mtg_cards_v2 WHERE card_name = ANY(%s)"
            " ORDER BY digital, id", (ordered,), lane=LANE_LIGHT):
        img.setdefault(cn, (url_en, url_ja))

    cards, lines = [], []
    for i, en in enumerate(ordered, 1):
        url_en, url_ja = img.get(en, (None, None))
        if url_ja:
            url, lang = url_ja, "ja"
        elif url_en:
            url, lang = url_en, "en"
        else:
            url, lang = None, None
        d = disp.get(en, en)
        cards.append({"display": d, "image": url, "lang": lang})
        lines.append(f"{i}. {d} — " + (f"{_LANG_LABEL[lang]}: {url}" if url else "画像なし"))

    parts = [f"カード画像 {len(cards)} 枚（日本語版の画像を優先・無ければ英語版）", *lines]
    if miss_lines:
        parts.append("見つからない名前: " + "／".join(miss_lines))
    if amb_lines:
        parts.append("1 枚に決まらない名前（同じ名前のカードが複数）: " + "／".join(amb_lines)
                     + "＝どれかの英語の正式名（両面は 表 // 裏）で呼び直す")
    if cut:
        parts.append(f"最初の {MAX_CARDS} 枚だけ表示した（{MAX_CARDS} 枚を超えた分は扱っていない）")
    pn = preview_unavailable_note(pnotes)
    if pn:
        parts.append(pn)
    parts.append(_NOTICE)

    meta = {
        "cards": cards,
        "not_found": missing,
        "ambiguous": [{"name": k, "candidates": [disp.get(c, c) for c in cs]} for k, cs in ambiguous.items()],
    }
    return CallToolResult(content=[TextContent(type="text", text="\n".join(parts))], meta=meta)

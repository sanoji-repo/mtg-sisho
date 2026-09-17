#!/usr/bin/env python3
"""手配置の全体図。Python 標準ライブラリのみ。隣に SVG と座標検証を生成。

BOXES: x,y,w,h,parent（包含関係だけを交差判定から除外）
EDGES: source,target,双方向,点列。TEXT: owner,x,baseline,text,size,bold。
文字の検証は仮定の幅による予約領域。実フォントの描画測定ではない。
"""
from pathlib import Path
from itertools import combinations
from html import escape
import unicodedata
import xml.etree.ElementTree as ET

W, H = 880, 1220
FONT = 'Hiragino Sans, Noto Sans JP, Meiryo, sans-serif'
BOXES = {
    'human': (40, 130, 180, 110, None),
    'ai': (290, 105, 550, 170, None),
    'server': (20, 345, 840, 590, None),
    'gate': (50, 435, 780, 170, 'server'),
    'mcp': (50, 665, 560, 245, 'server'),
    'local': (70, 735, 340, 155, 'mcp'),
    'combo': (430, 735, 160, 155, 'mcp'),
    'log': (650, 675, 180, 150, 'server'),
    'db': (50, 1040, 420, 125, None),
    'api': (520, 1040, 300, 125, None),
}
EDGES = [
    ('human', 'ai', True, [(220, 185), (290, 185)]),
    ('ai', 'gate', True, [(560, 275), (560, 435)]),
    ('gate', 'mcp', True, [(320, 605), (320, 665)]),
    ('mcp', 'log', False, [(610, 700), (650, 700)]),
    ('local', 'db', True, [(230, 890), (230, 1040)]),
    ('combo', 'db', True, [(490, 890), (490, 1080), (470, 1080)]),
    ('combo', 'api', True, [(590, 850), (850, 850), (850, 1100), (820, 1100)]),
]
TEXT = []


def text(owner, x, y, value, size=15, bold=False):
    TEXT.append((owner, x, y, value, size, bold))


text(None, 40, 40, 'mtg_sisho 全体図', 24, True)
text(None, 40, 73, '問いを解釈する AI と、事実を返すサーバー', 19, True)
text('human', 60, 165, '利用者（人）', 19, True)
text('human', 60, 198, '質問する', 15)
text('human', 60, 222, '回答を受け取る', 15)
text('ai', 310, 135, 'AI アシスタント', 19, True)
text('ai', 310, 164, '問いの解釈・道具の選択・回答の構成', 16)
text('ai', 310, 191, '確認済みのクライアント', 13, True)
text('ai', 310, 214, 'Claude Code ／ claude.ai（カスタムコネクタ）', 14)
text('ai', 310, 247, 'ChatGPT（開発者モードのカスタムコネクタ）', 14)
text(None, 578, 312, 'MCP', 14, True)
text('server', 40, 378, '公開サーバー', 21, True)
text('server', 595, 378, '事実の取得・照合・計算', 15, True)
text('server', 595, 406, '速く正確に返す', 15)
text('gate', 70, 464, '門 ｜ 接続用の合言葉（札）の発行・検査', 18, True)
text('gate', 70, 495, 'レート制限：滑走 60 秒窓（既定値）', 15, True)
text('gate', 70, 523, '接続元 IP：60 回/分　・　全体：300 回/分　・　札：60 回/分', 15)
text('gate', 70, 551, 'find_combos：札ごと 10 回/分', 15)
text('gate', 70, 581, '日次の上限は札の発行だけ：IP 3 回/日・全体 100 回/日', 15)
text('mcp', 70, 699, 'MCP サーバー ｜ 道具は 10 個', 20, True)
text('local', 86, 763, 'DB 内で完結する 9 個', 18, True)
text('local', 86, 793, 'カード・総合ルール・公式裁定（3）', 14)
text('local', 86, 819, '共起（1）・SQL 実行／スキーマ（2）', 14)
text('local', 86, 845, '回答文のカード名照合（1）', 14)
text('local', 86, 871, '確率の厳密計算（1）・DB 健全性（1）', 14)
text('combo', 442, 763, '外部照会 1 個', 17, True)
text('combo', 442, 793, 'find_combos', 15, True)
text('combo', 442, 823, '唯一の外向き', 15)
text('combo', 442, 851, '名前の照合は', 14)
text('combo', 442, 874, 'DB も利用', 14)
text('log', 668, 706, '道具ログ', 18, True)
text('log', 668, 738, '入力・成功／エラー', 14)
text('log', 668, 766, '所要時間', 14)
text('log', 668, 794, 'DB 呼び出しの計時', 14)
text('db', 70, 1073, 'PostgreSQL 18', 21, True)
text('db', 70, 1104, '読み取り専用の役割：readonly_ai', 16)
text('db', 70, 1136, 'カード・ルール・統計・SQL 関数', 15)
text('api', 538, 1071, '外部 API', 16, True)
text('api', 538, 1101, 'Commander Spellbook', 19, True)
text('api', 538, 1127, '都度照会 /find-my-combos', 14)
text('api', 538, 1151, 'backend.commanderspellbook.com', 12)
text(None, 40, 1200, '双方向矢印：要求・応答　／　片方向矢印：ログ記録', 14)


def rect(name):
    x, y, w, h, _ = BOXES[name]
    return (x, y, x+w, y+h)


def intersect(r, s):
    hit = (max(r[0], s[0]), max(r[1], s[1]), min(r[2], s[2]), min(r[3], s[3]))
    return hit if hit[0] <= hit[2] and hit[1] <= hit[3] else None


def segment(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


def ancestors(name):
    result = set()
    while BOXES[name][4]:
        name = BOXES[name][4]
        result.add(name)
    return result


def expand(r, margin):
    return (r[0]-margin, r[1]-margin, r[2]+margin, r[3]+margin)


def text_rect(item):
    _, x, y, value, size, _ = item
    # Japanese: 1 em; ASCII and other narrow characters: 0.70 em.
    # Add 2 px on all sides; font substitutions may still exceed this estimate.
    width = sum(1 if unicodedata.east_asian_width(c) in ('W', 'F') else .70 for c in value) * size
    return (x-2, y-size-2, x+width+2, y+size*.25+2)


def boundary(p, name):
    x0, y0, x1, y1 = rect(name)
    return (p[0] in (x0, x1) and y0 < p[1] < y1) or (p[1] in (y0, y1) and x0 < p[0] < x1)


def validate():
    assert W <= 880
    for n in BOXES:
        r = rect(n)
        assert intersect(r, (0, 0, W, H)) == r
        for parent in ancestors(n):
            assert intersect(r, rect(parent)) == r
    for n, m in combinations(BOXES, 2):
        if n not in ancestors(m) and m not in ancestors(n):
            assert intersect(rect(n), rect(m)) is None, ('box overlap', n, m)
    segments = []
    for i, (src, dst, _, pts) in enumerate(EDGES):
        assert boundary(pts[0], src) and boundary(pts[-1], dst)
        for j, (a, b) in enumerate(zip(pts, pts[1:])):
            assert (a[0] == b[0]) != (a[1] == b[1])
            r = segment(a, b)
            segments.append((i, j, r))
            for n in BOXES:
                if n in ancestors(src) | ancestors(dst):
                    continue  # crossing a containing server/MCP frame is intentional
                hit = intersect(r, rect(n))
                allowed = []
                if n == src and j == 0:
                    allowed.append((*pts[0], *pts[0]))
                if n == dst and j == len(pts)-2:
                    allowed.append((*pts[-1], *pts[-1]))
                assert hit is None or hit in allowed, ('line-box', i, n, hit)
                if n not in (src, dst):
                    assert intersect(expand(r, 5), rect(n)) is None, ('clearance', i, n)
    minimum = float('inf')
    for (i, j, r), (k, l, s) in combinations(segments, 2):
        hit = intersect(r, s)
        if i == k and abs(j-l) == 1:
            assert hit and hit[:2] == hit[2:]
        else:
            assert hit is None, ('line-line', i, k)
        if i != k:
            dx, dy = max(r[0]-s[2], s[0]-r[2], 0), max(r[1]-s[3], s[1]-r[3], 0)
            minimum = min(minimum, (dx*dx+dy*dy)**.5)
    assert minimum > 10
    for item in TEXT:
        owner = item[0]
        r = text_rect(item)
        assert intersect(r, (0, 0, W, H)) == r, ('text canvas', item[3])
        if owner:
            assert intersect(r, rect(owner)) == r, ('text fit', item[3], r)
        for _, _, s in segments:
            assert intersect(r, expand(s, 5)) is None, ('text-line', item[3])
        for n in BOXES:
            if n != owner and (not owner or n not in ancestors(owner)):
                assert intersect(r, rect(n)) is None, ('text-box', item[3], n)
    for a, b in combinations(TEXT, 2):
        assert intersect(text_rect(a), text_rect(b)) is None, ('text-text', a[3], b[3])
    return f'''座標検証: PASS
キャンバス: {W} × {H} px
線はすべて水平・垂直。7 本の接続の両端は指定した箱の辺に一致。
箱どうしの重複: 0（明示した親子の包含を除く）
線と箱の交差・接触: 0（接続端点と包含枠の通過を除く）
線どうしの交差・接触・同一路径の重複: 0（同じ折れ線の隣接端点を除く）
異なる線の中心線最小距離: {minimum:g} px
線と無関係な箱: 中心線の周囲 5 px の余白を確保
文字の予約領域どうし、および予約領域と線・無関係な箱の重なり: 0
文字の予約領域: 和文 1 em、狭い文字 0.70 em、周囲 2 px の仮定で計算
SVG の XML 構文・外部参照なし・スクリプトなし: PASS
フォントによる実際の文字幅と描画の目視確認は未実施。

設計上の注記:
公開サーバー・MCP サーバーの包含枠は接続線が横切る仕様。
双方向矢印は要求・応答を一本にまとめたもの。片方向はログ記録。
9 個の道具は DB 内で完結。find_combos も名前の変換・照合に DB を利用。
道具ログの結果は成功・エラーであり、結果本文の保存を意味しない。
制限値は指定された既定値を掲載し、全設定を網羅する表ではない。
外部 API の URL は文字列としてのみ掲載し、取得・埋め込みはしない。
'''


def svg():
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}" role="img" aria-labelledby="title desc">',
           '<title id="title">mtg_sisho 全体図：問いの解釈は利用者側の AI、サーバーは事実を返す</title>',
           '<desc id="desc">利用者は言語モデルを持つ AI アシスタントに質問する。確認済みクライアントは Claude Code、claude.ai のカスタムコネクタ、ChatGPT の開発者モードのカスタムコネクタ。門は札の発行・検査とレート制限を行う。MCP の道具は 10 個あり、9 個は PostgreSQL 18 内で完結。readonly_ai で読み取り、検索・照合・計算を行う。外部 API に出るのは find_combos だけで、https://backend.commanderspellbook.com/find-my-combos に都度照会する。この道具もカード名照合に DB を使う。道具ログには入力と成功・エラー、計時を記録。双方向矢印は要求と応答、片方向矢印はログ記録。</desc>',
           '<defs><marker id="arrow" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="8" refX="9" refY="4" orient="auto-start-reverse" viewBox="0 0 9 8"><path d="M0 0 L9 4 L0 8 Z" fill="#48566b"/></marker></defs>',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
    for n, (x, y, w, h, _) in BOXES.items():
        fill = '#ffffff' if n in ('server', 'mcp') else '#f4f6fa'
        if n == 'ai':
            fill = '#edf4ff'
        stroke = '#9aa4b5' if n == 'server' else '#48566b'
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>')
    for _, _, both, pts in EDGES:
        start = ' marker-start="url(#arrow)"' if both else ''
        points = ' '.join(f'{x},{y}' for x, y in pts)
        out.append(f'<polyline points="{points}" fill="none" stroke="#48566b" stroke-width="1.8" stroke-linejoin="miter"{start} marker-end="url(#arrow)"/>')
    for _, x, y, value, size, bold in TEXT:
        weight = '700' if bold else '400'
        out.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="#161922">{escape(value)}</text>')
    out.append('</svg>')
    result = '\n'.join(out)+'\n'
    root = ET.fromstring(result)
    for element in root.iter():
        assert element.tag.split('}')[-1] not in ('script', 'image', 'foreignObject')
        assert not any('href' in key or key.startswith('on') for key in element.attrib)
    assert 'url(' not in result.replace('url(#arrow)', '') and '@import' not in result
    return result


if __name__ == '__main__':
    report = validate()
    content = svg()
    directory = Path(__file__).resolve().parent
    (directory/'overview.svg').write_text(content, encoding='utf-8')
    (directory/'validation.txt').write_text(report, encoding='utf-8')
    print(report)

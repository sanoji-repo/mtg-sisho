#!/usr/bin/env python3
"""手動配置の関連図。標準ライブラリのみ。実行すると隣に SVG と検証結果を保存。

BOXES = (x, y, width, height, role); GROUPS = (x, y, width, height, label, members)
EDGES = (source, target, foreign_key, points). points の順序が矢印の向き。
検証ではテーブルの矩形への接触を、参照元・参照先の端点だけ許可する。
グループ境界はグループ間参照が横切るため、この禁止対象には含めない。
"""
from pathlib import Path
from itertools import combinations
from html import escape
import xml.etree.ElementTree as ET

W, H = 880, 680
FONT = 'Hiragino Sans, Noto Sans JP, Meiryo, sans-serif'
BOXES = {
    'deck_list': (40, 180, 210, 64, '実デッキ見出し'),
    'deck_cards': (40, 330, 210, 64, 'デッキ明細'),
    'card_rulings': (335, 150, 210, 64, '公式裁定'),
    'mtg_cards_v2': (330, 330, 220, 80, 'カード本体'),
    'mtg_sets': (330, 540, 220, 64, 'セット一覧'),
    'format_deck_counts': (630, 180, 210, 64, 'フォーマット別デッキ総数'),
    'card_format_strength': (630, 330, 210, 64, '構築採用率'),
    'limited_card_stats': (630, 540, 210, 64, 'ドラフト統計'),
}
GROUPS = [
    (20, 100, 250, 324, '実デッキ情報', ['deck_list', 'deck_cards']),
    (310, 100, 260, 140, '公式裁定', ['card_rulings']),
    (310, 285, 260, 343, 'カード・セット情報', ['mtg_cards_v2', 'mtg_sets']),
    (610, 100, 250, 528, '採用率・統計', ['format_deck_counts', 'card_format_strength', 'limited_card_stats']),
]
EDGES = [
    ('deck_cards', 'deck_list', True, [(145, 330), (145, 244)]),
    ('deck_cards', 'mtg_cards_v2', True, [(250, 362), (330, 362)]),
    ('card_format_strength', 'mtg_cards_v2', True, [(630, 362), (550, 362)]),
    ('mtg_cards_v2', 'mtg_cards_v2', True, [(350, 410), (350, 460), (380, 460), (380, 410)]),
    ('mtg_cards_v2', 'mtg_sets', False, [(440, 410), (440, 540)]),
    ('card_format_strength', 'format_deck_counts', False, [(735, 330), (735, 244)]),
    ('card_rulings', 'mtg_cards_v2', False, [(440, 214), (440, 330)]),
    ('limited_card_stats', 'mtg_cards_v2', False, [(735, 540), (735, 470), (520, 470), (520, 410)]),
    ('limited_card_stats', 'mtg_sets', False, [(630, 572), (550, 572)]),
]


def intersection(a, b, c, d):
    """Axis-aligned closed segment intersection as a bounding rectangle, or None."""
    x0, x1 = max(min(a[0], b[0]), min(c[0], d[0])), min(max(a[0], b[0]), max(c[0], d[0]))
    y0, y1 = max(min(a[1], b[1]), min(c[1], d[1])), min(max(a[1], b[1]), max(c[1], d[1]))
    return (x0, y0, x1, y1) if x0 <= x1 and y0 <= y1 else None


def boundary(p, box):
    x, y, w, h = box[:4]
    return (p[0] in (x, x+w) and y < p[1] < y+h) or (p[1] in (y, y+h) and x < p[0] < x+w)


def validate():
    assert W <= 880 and len(BOXES) == 8 and len(EDGES) == 9
    assert sum(e[2] for e in EDGES) == 4
    members = [n for *_, names in GROUPS for n in names]
    assert sorted(members) == sorted(BOXES)
    for gx, gy, gw, gh, _, names in GROUPS:
        for name in names:
            x, y, w, h = BOXES[name][:4]
            assert gx < x and gy+30 < y and x+w < gx+gw and y+h < gy+gh
    for (n, r), (m, s) in combinations(BOXES.items(), 2):
        assert not intersection(r[:2], (r[0]+r[2], r[1]+r[3]), s[:2], (s[0]+s[2], s[1]+s[3])), (n, m)
    segments = []
    for i, (src, dst, _, pts) in enumerate(EDGES):
        assert boundary(pts[0], BOXES[src]) and boundary(pts[-1], BOXES[dst])
        for j, (a, b) in enumerate(zip(pts, pts[1:])):
            assert (a[0] == b[0]) != (a[1] == b[1]), (i, 'non-orthogonal')
            assert all(0 < x < W and 0 < y < H for x, y in (a, b))
            for name, (x, y, w, h, _) in BOXES.items():
                hit = intersection(a, b, (x, y), (x+w, y+h))
                if hit:
                    allowed = []
                    if name == src and j == 0:
                        allowed.append((*pts[0], *pts[0]))
                    if name == dst and j == len(pts)-2:
                        allowed.append((*pts[-1], *pts[-1]))
                    assert hit in allowed, (src, dst, name, hit)
            segments.append((i, j, a, b))
    for (i, j, a, b), (k, l, c, d) in combinations(segments, 2):
        hit = intersection(a, b, c, d)
        if i == k and abs(j-l) == 1:
            assert hit and hit[:2] == hit[2:]
        else:
            assert hit is None, ('edge collision', i, k, hit)
    # Stroke and arrowhead clearance: each rendered edge fits within 5 px of its
    # centerline (stroke 1.8 px, arrow width 8 px). Distinct edges stay > 10 px apart.
    minimum = float('inf')
    for (i, _, a, b), (k, _, c, d) in combinations(segments, 2):
        if i == k:
            continue
        dx = max(min(a[0], b[0])-max(c[0], d[0]), min(c[0], d[0])-max(a[0], b[0]), 0)
        dy = max(min(a[1], b[1])-max(c[1], d[1]), min(c[1], d[1])-max(a[1], b[1]), 0)
        minimum = min(minimum, (dx*dx+dy*dy)**0.5)
    assert minimum > 10, minimum
    # Check a 5 px envelope against all unrelated table boxes as well.
    for i, _, a, b in segments:
        src, dst = EDGES[i][:2]
        for name, (x, y, w, h, _) in BOXES.items():
            if name not in (src, dst):
                assert intersection(a, b, (x-5, y-5), (x+w+5, y+h+5)) is None
    return f'''座標検証: PASS
キャンバス: {W} × {H} px
テーブル: 8 / グループ: 4 / 実線: 4 / 破線: 5
全線分が水平または垂直。始点・終点は指定テーブルの辺に接続。
テーブル矩形と線の交差・接触: 0（接続端点を除く）
異なる参照線どうしの交差・接触・同一路径の重複: 0
異なる参照線どうしの中心線最小距離: {minimum:g} px
自己参照を含む各折れ線の自己交差: 0
無関係なテーブルに対する線の周囲 5 px の余白: 確保
グループ枠はグループ間参照が横切る仕様。テーブル矩形とは区別。
SVG の XML 構文・外部参照なし・スクリプトなし: PASS
フォントによる文字幅と実際の描画の目視確認は未実施。
'''


def svg():
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}" role="img" aria-labelledby="title desc">',
           '<title id="title">mtg_sisho 中核テーブル関連図</title>',
           '<desc id="desc">8 表と 9 本の参照。実線は外部キー、破線は外部キー制約のない参照。矢印は参照元から参照先へ向かう。接続列は本文の線の一覧を参照。</desc>',
           '<defs><marker id="arrow" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="8" refX="9" refY="4" orient="auto" viewBox="0 0 9 8"><path d="M0 0 L9 4 L0 8 Z" fill="#48566b"/></marker></defs>',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
           '<text x="440" y="39" text-anchor="middle" font-size="21" font-weight="700" fill="#161922">mtg_sisho 中核テーブル関連図</text>',
           '<text x="440" y="67" text-anchor="middle" font-size="13" fill="#5b6274">実線：外部キー　／　破線：外部キー制約のない参照</text>']
    for x, y, w, h, label, _ in GROUPS:
        out.extend([f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="#a6afbd" stroke-width="1"/>',
                    f'<text x="{x+16}" y="{y+24}" font-size="14" font-weight="600" fill="#5b6274">{label}</text>'])
    for name, (x, y, w, h, role) in BOXES.items():
        out.extend([f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#f4f6fa" stroke="#48566b" stroke-width="1.2"/>',
                    f'<text x="{x+w/2:g}" y="{y+h/2-5:g}" text-anchor="middle" font-size="15" font-weight="600" fill="#161922">{escape(name)}</text>',
                    f'<text x="{x+w/2:g}" y="{y+h/2+17:g}" text-anchor="middle" font-size="13" fill="#5b6274">{role}</text>'])
    for src, dst, fk, pts in EDGES:
        dash = '' if fk else ' stroke-dasharray="6 5"'
        points = ' '.join(f'{x},{y}' for x, y in pts)
        out.append(f'<polyline points="{points}" fill="none" stroke="#48566b" stroke-width="1.8" stroke-linejoin="miter"{dash} marker-end="url(#arrow)"/>')
    out.append('<text x="440" y="660" text-anchor="middle" font-size="12" fill="#5b6274">矢印：参照元 → 参照先　・　接続する列は本文「線の一覧」を参照</text>')
    out.append('</svg>')
    result = '\n'.join(out) + '\n'
    root = ET.fromstring(result)
    for element in root.iter():
        assert element.tag.split('}')[-1] not in ('script', 'image', 'foreignObject')
        assert not any('href' in key or key.startswith('on') for key in element.attrib)
    assert 'url(' not in result.replace('url(#arrow)', '') and '@import' not in result
    return result


if __name__ == '__main__':
    report = validate()
    content = svg()
    output = Path(__file__).resolve().parent
    (output / 'er.svg').write_text(content, encoding='utf-8')
    (output / 'validation.txt').write_text(report, encoding='utf-8')
    print(report)

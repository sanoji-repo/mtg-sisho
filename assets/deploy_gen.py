#!/usr/bin/env python3
"""手配置の配置図。標準ライブラリのみ。隣に deploy.svg / validation.txt を生成。

BOXES: (x, y, width, height, parent)。TEXT: (owner, x, baseline, value, size, bold)。
EDGES: (source, target, kind, points)。kind は flow / request / connect。
文字幅の仮定による予約領域を検証する。実フォントの測定ではない。
"""
from pathlib import Path
from itertools import combinations
from html import escape
import unicodedata
import xml.etree.ElementTree as ET

W, H = 880, 2000
FONT = 'Hiragino Sans, Noto Sans JP, Meiryo, sans-serif'
BOXES = {
    'legend': (40, 105, 340, 210, None),
    'ai': (520, 105, 300, 85, None),
    'internet': (520, 245, 300, 85, None),
    'dev': (20, 375, 380, 1405, None),
    'public': (480, 375, 380, 1405, None),
    'health': (40, 465, 340, 100, 'dev'),
    'jobs': (40, 620, 340, 140, 'dev'),
    'publisher': (40, 840, 340, 240, 'dev'),
    'export': (40, 1250, 340, 110, 'dev'),
    'schedule': (40, 1405, 340, 350, 'dev'),
    'funnel': (500, 465, 340, 90, 'public'),
    'mcp': (500, 620, 340, 110, 'public'),
    'subscriber': (500, 840, 340, 240, 'public'),
    'restore': (500, 1250, 340, 110, 'public'),
    'monitor': (500, 1405, 340, 100, 'public'),
    'excluded': (500, 1545, 340, 210, 'public'),
    'acl': (20, 1810, 840, 155, None),
}
EDGES = [
    ('ai', 'internet', 'request', [(670, 190), (670, 245)]),
    ('internet', 'funnel', 'request', [(820, 285), (870, 285), (870, 510), (840, 510)]),
    ('health', 'internet', 'request', [(380, 510), (440, 510), (440, 285), (520, 285)]),
    ('funnel', 'mcp', 'request', [(670, 555), (670, 620)]),
    ('mcp', 'subscriber', 'request', [(670, 730), (670, 840)]),
    ('jobs', 'publisher', 'flow', [(210, 760), (210, 840)]),
    ('subscriber', 'publisher', 'connect', [(500, 925), (380, 925)]),
    ('publisher', 'subscriber', 'flow', [(210, 1080), (210, 1150), (670, 1150), (670, 1080)]),
    ('publisher', 'export', 'flow', [(40, 990), (30, 990), (30, 1305), (40, 1305)]),
    ('export', 'restore', 'flow', [(380, 1305), (500, 1305)]),
    ('restore', 'subscriber', 'flow', [(840, 1305), (850, 1305), (850, 1000), (840, 1000)]),
]
TEXT = []


def text(owner, x, y, value, size=15, bold=False):
    TEXT.append((owner, x, y, value, size, bold))


text(None, 40, 40, 'mtg_sisho 配置図', 25, True)
text(None, 40, 73, '開発機・公開サーバーと、その接続', 17)
text('legend', 56, 135, '線の読み方', 17, True)
text('legend', 56, 165, '実線：データ・処理の向き', 14)
text('legend', 56, 193, '破線：レプリケーションの接続開始', 14)
text('legend', 56, 221, '双方向：要求と応答', 14)
text('legend', 56, 260, 'レプリケーションの 2 本は、', 14)
text('legend', 56, 288, '同じ通信の異なる向きを示す', 14)
text('ai', 540, 140, '利用者の AI アシスタント', 18, True)
text('ai', 540, 170, '公開 URL へ接続', 15)
text('internet', 540, 280, 'インターネット', 19, True)
text('internet', 540, 310, '公開 URL へのアクセス', 15)
text('dev', 40, 408, '開発機', 23, True)
text('dev', 40, 438, '家の仮想マシン・Ubuntu', 16)
text('public', 500, 408, '公開サーバー', 23, True)
text('public', 500, 438, '別の機械・Ubuntu Server', 16)
text('health', 56, 496, '外からの見張り', 18, True)
text('health', 56, 524, '公開 URL で健全性を定期確認', 15)
text('health', 56, 549, 'mtg_rag_health', 14)
text('jobs', 56, 654, '取り込み・集計の脚本', 19, True)
text('jobs', 56, 686, 'データを取得・整形して DB を更新', 15)
text('jobs', 56, 718, '定期実行の内容は下段に記載', 14)
text('publisher', 56, 876, 'PostgreSQL 18', 21, True)
text('publisher', 56, 909, 'publisher ｜ データの正本', 16, True)
text('funnel', 516, 498, 'Tailscale Funnel', 21, True)
text('funnel', 516, 532, '8765 をインターネットへ公開', 15)
text('mcp', 516, 655, 'MCP サーバー（門つき）', 19, True)
text('mcp', 516, 688, '待ち受け：8765', 15)
text('mcp', 516, 715, 'readonly_ai で DB を読む', 15)
text('subscriber', 516, 876, 'PostgreSQL 18', 21, True)
text('subscriber', 516, 909, 'subscriber ｜ 公開用の複製', 16, True)
text('subscriber', 516, 943, '約 0.9 GB', 15)
text('subscriber', 516, 976, '読み取り専用の役割：readonly_ai', 14)
text('subscriber', 516, 1011, '開発機の PostgreSQL へ接続を開始', 14)
text('subscriber', 516, 1045, '論理レプリケーションで追従', 15)
text(None, 412, 908, '接続開始', 14, True)
text(None, 419, 1132, 'データ', 14, True)
text('dev', 56, 1193, '日々の更新：論理レプリケーション', 14)
text('public', 516, 1193, 'データは開発機から受信', 14)
text('export', 56, 1281, '公開用 dump の書き出し', 18, True)
text('export', 56, 1311, 'sh/make_public_dump.sh', 14)
text('export', 56, 1340, '初期化・作り直し用', 15)
text('restore', 516, 1281, 'dump から復元', 18, True)
text('restore', 516, 1311, 'sh/restore_public_dump.sh', 14)
text('restore', 516, 1340, '数分で作り直す運用', 15)
text(None, 420, 1288, 'dump', 14, True)
text('schedule', 56, 1438, '開発機の定期実行', 19, True)
text('schedule', 56, 1472, '毎日 03:00〜10:00', 16, True)
text('schedule', 56, 1501, 'MTGTop8・MTGO・Moxfield の', 14)
text('schedule', 56, 1528, '差分取得を並行実行', 14)
text('schedule', 56, 1555, '完了後、共起を全件再集計', 14)
text('schedule', 56, 1595, '4 時間おき（実行時刻の 30 分）', 15, True)
text('schedule', 56, 1624, 'MTGO 公式デッキの過去分を取得', 14)
text('schedule', 56, 1651, '1 回に 1 か月分ずつ遡る', 14)
text('schedule', 56, 1693, '毎週日曜 00:00', 16, True)
text('schedule', 56, 1724, '論理バックアップ・4 世代保持', 14)
text('monitor', 516, 1438, '自機の見張り', 19, True)
text('monitor', 516, 1473, '公開サーバー自身の状態を定期記録', 15)
text('excluded', 516, 1577, '公開サーバーに置かないもの', 18, True)
text('excluded', 516, 1610, 'プレイヤー名（players 表に隔離）', 14)
text('excluded', 516, 1637, 'publication・dump ともに対象外', 14)
text('acl', 40, 1843, 'Tailscale ACL', 20, True)
text('acl', 40, 1877, '開発機 → 公開サーバー：SSH・8765 を許可', 16)
text('acl', 40, 1910, '公開サーバー → 他ノード：原則拒否', 16)
text('acl', 40, 1943, '例外：論理レプリケーションの接続先となる開発機の PostgreSQL', 16)


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
        assert name not in result, 'circular parent'
        result.add(name)
    return result


def expand(r, margin):
    return (r[0]-margin, r[1]-margin, r[2]+margin, r[3]+margin)


def text_rect(item):
    _, x, y, value, size, _ = item
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
            assert intersect(expand(r, 5), rect(parent)) == expand(r, 5)
    for n, m in combinations(BOXES, 2):
        if n not in ancestors(m) and m not in ancestors(n):
            assert intersect(rect(n), rect(m)) is None, ('box overlap', n, m)
    segments = []
    for i, (src, dst, kind, pts) in enumerate(EDGES):
        assert kind in ('flow', 'connect', 'request')
        assert boundary(pts[0], src) and boundary(pts[-1], dst)
        for j, (a, b) in enumerate(zip(pts, pts[1:])):
            assert (a[0] == b[0]) != (a[1] == b[1])
            r = segment(a, b)
            assert intersect(expand(r, 5), (0, 0, W, H)) == expand(r, 5)
            segments.append((i, j, r))
            for n in BOXES:
                if n in ancestors(src) | ancestors(dst):
                    continue
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
        owner, _, _, value, _, _ = item
        r = text_rect(item)
        assert intersect(r, (0, 0, W, H)) == r, ('text canvas', value)
        if owner:
            assert intersect(r, rect(owner)) == r, ('text fit', value, r)
        for _, _, s in segments:
            assert intersect(r, expand(s, 5)) is None, ('text-line', value)
        for n in BOXES:
            if n != owner and (not owner or n not in ancestors(owner)):
                assert intersect(r, rect(n)) is None, ('text-box', value, n)
    for a, b in combinations(TEXT, 2):
        assert intersect(text_rect(a), text_rect(b)) is None, ('text-text', a[3], b[3])
    return f'''座標検証: PASS
キャンバス: {W} × {H} px
接続: {len(EDGES)} 本。すべて水平・垂直。両端は指定した箱の辺に一致。
箱どうしの重複: 0（明示した包含関係を除く）
線と箱の交差・接触: 0（接続端点と包含枠の通過を除く）
線どうしの交差・接触・同一路径の重複: 0（同じ折れ線の隣接端点を除く）
異なる線の中心線最小距離: {minimum:g} px
線と無関係な箱: 中心線の周囲 5 px の余白を確保
文字の予約領域どうし、および文字と線・無関係な箱の重なり: 0
文字の予約領域: 和文 1 em、狭い文字 0.70 em、周囲 2 px の仮定
SVG の XML 構文、title / desc、白背景、外部参照なし、スクリプトなし: PASS
フォントによる実際の文字幅と描画の目視確認は未実施。

意味と範囲:
機械を示す包含枠は機械間の線が横切る仕様。
破線の接続開始（subscriber → publisher）と実線のデータ転送
（publisher → subscriber）は、同じレプリケーション通信の異なる向きを示す。
実線を別途開く接続として数えていない。ACL の例外もこの接続を指す。
双方向矢印は要求・応答を一本にまとめる。
同じ機械内の見張り・定期実行の箱は配備された処理の説明であり、別機械ではない。
SSH・8765 の管理アクセスは ACL 欄に記載。配線を重複して描かない。
公開 URL の監視はインターネット経由。tailnet の直接監視と混同しない。

根拠（ローカル文書・SQL の読取り。稼働機の実測は行っていない）:
docs/PUBLIC_SERVER.md の役割・dump 復元・論理レプリケーション・公開口・見張り。
docs/SETUP.md の定期運用。時刻のタイムゾーンは文書に明示がないため補っていない。
公開サーバー自身の状態の定期記録は依頼で提示された構成として掲載。
呼び出しログはユーザー指定により除外項目に記載しない。
IP・ホスト名・秘密の待ち受けパス・パスワードは掲載しない。
'''


def svg():
    desc = ('開発機は家の Ubuntu 仮想マシンで、取り込み・集計と正本の PostgreSQL 18 publisher を持つ。'
            '別の Ubuntu Server 機は公開用 PostgreSQL 18 subscriber、門つき MCP サーバー、Tailscale Funnel、自機の見張りを持つ。'
            '論理レプリケーションの接続開始は公開サーバーから開発機へ、データの流れは開発機から公開サーバーへ。'
            'これらは同じ通信を二つの意味で描いたものである。'
            '初期化と作り直しでは開発側で公開用 dump を作り、公開側で復元する。'
            'MCP は readonly_ai で DB を読み、Funnel が 8765 をインターネットへ公開する。'
            '利用者の AI と開発機の外部監視は公開 URL に接続する。'
            'ACL は開発機から公開側の SSH と 8765 を許可し、公開側から他ノードへは原則拒否。開発側 PG へのレプリケーション接続は例外。'
            'プレイヤー名は開発側 players 表に隔離し、publication と dump に含めない。'
            '定期実行とバックアップの周期は図内に記載。破線は接続開始、実線は処理・データ方向、双方向矢印は要求と応答。')
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}" role="img" aria-labelledby="title desc">',
           '<title id="title">mtg_sisho 配置図</title>',
           f'<desc id="desc">{escape(desc)}</desc>',
           '<defs><marker id="arrow" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="8" refX="9" refY="4" orient="auto-start-reverse" viewBox="0 0 9 8"><path d="M0 0 L9 4 L0 8 Z" fill="#48566b"/></marker></defs>',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
    for n, (x, y, w, h, _) in BOXES.items():
        fill = '#ffffff' if n in ('dev', 'public', 'legend', 'acl') else '#f4f6fa'
        if n in ('publisher', 'subscriber'):
            fill = '#edf4ff'
        stroke = '#9aa4b5' if n in ('dev', 'public', 'legend', 'acl') else '#48566b'
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" stroke="{stroke}" stroke-width="1.2"/>')
    for _, _, kind, pts in EDGES:
        start = ' marker-start="url(#arrow)"' if kind == 'request' else ''
        dash = ' stroke-dasharray="6 5"' if kind == 'connect' else ''
        points = ' '.join(f'{x},{y}' for x, y in pts)
        out.append(f'<polyline points="{points}" fill="none" stroke="#48566b" stroke-width="1.8" stroke-linejoin="miter"{start}{dash} marker-end="url(#arrow)"/>')
    for _, x, y, value, size, bold in TEXT:
        weight = '700' if bold else '400'
        out.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="#161922">{escape(value)}</text>')
    out.append('</svg>')
    result = '\n'.join(out)+'\n'
    root = ET.fromstring(result)
    tags = []
    for element in root.iter():
        tag = element.tag.split('}')[-1]
        tags.append(tag)
        assert tag not in ('script', 'image', 'foreignObject')
        assert not any('href' in key or key.startswith('on') for key in element.attrib)
    assert 'title' in tags and 'desc' in tags
    assert 'url(' not in result.replace('url(#arrow)', '') and '@import' not in result
    return result


if __name__ == '__main__':
    report = validate()
    content = svg()
    directory = Path(__file__).resolve().parent
    (directory/'deploy.svg').write_text(content, encoding='utf-8')
    (directory/'validation.txt').write_text(report, encoding='utf-8')
    print(report)

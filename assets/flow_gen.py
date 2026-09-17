#!/usr/bin/env python3
"""手配置のデータフロー図。標準ライブラリのみ。
BOXES=(x,y,w,h,parent); EDGES=(source,target,kind,points)。
TEXT=(owner,x,baseline,value,size,bold)。座標はこのファイルで編集。
実行すると隣に flow.svg と validation.txt を保存する。
文字領域は幅を仮定した予約領域。実フォントの描画測定ではない。
"""
from pathlib import Path
from itertools import combinations
from html import escape
import unicodedata
import xml.etree.ElementTree as ET

W, H = 880, 1740
FONT = 'Hiragino Sans, Noto Sans JP, Meiryo, sans-serif'
BOXES = {
    'policy': (20, 100, 840, 70, None),
    'card_sources': (20, 210, 260, 320, None),
    'deck_sources': (310, 210, 260, 320, None),
    'lands_source': (600, 210, 260, 320, None),
    'card_import': (20, 600, 260, 130, None),
    'deck_import': (310, 600, 260, 130, None),
    'lands_stream': (600, 600, 260, 130, None),
    'card_raw': (20, 810, 260, 170, None),
    'deck_raw': (310, 810, 260, 170, None),
    'lands_stats': (600, 810, 260, 170, None),
    'card_derive': (20, 1060, 260, 190, None),
    'deck_derive': (310, 1060, 260, 190, None),
    'public_data': (20, 1350, 840, 135, None),
    'exit': (280, 1570, 320, 110, None),
}
EDGES = [
    ('card_sources', 'card_import', 'flow', [(150, 530), (150, 600)]),
    ('deck_sources', 'deck_import', 'flow', [(440, 530), (440, 600)]),
    ('lands_source', 'lands_stream', 'flow', [(730, 530), (730, 600)]),
    ('card_import', 'card_raw', 'flow', [(150, 730), (150, 810)]),
    ('deck_import', 'deck_raw', 'flow', [(440, 730), (440, 810)]),
    ('lands_stream', 'lands_stats', 'flow', [(730, 730), (730, 810)]),
    ('card_raw', 'card_derive', 'flow', [(150, 980), (150, 1060)]),
    ('deck_raw', 'deck_derive', 'flow', [(440, 980), (440, 1060)]),
    ('card_derive', 'public_data', 'flow', [(150, 1250), (150, 1350)]),
    ('deck_derive', 'public_data', 'flow', [(440, 1250), (440, 1350)]),
    ('lands_stats', 'public_data', 'flow', [(730, 980), (730, 1350)]),
    ('public_data', 'exit', 'flow', [(440, 1485), (440, 1570)]),
]
TEXT = []


def text(owner, x, y, value, size=15, bold=False):
    TEXT.append((owner, x, y, value, size, bold))


text(None, 30, 40, 'mtg_sisho データの流れ', 25, True)
text(None, 30, 73, '出所から取り込み・加工・公開へ', 17)
text('policy', 38, 128, '取り込みの共通方針：取得済みはスキップ', 17, True)
text('policy', 38, 155, '同じコマンドを毎晩実行しても、新規分だけを取りに行く差分運用', 15)
text('card_sources', 36, 244, 'カード・ルール', 20, True)
text('card_sources', 36, 285, 'Scryfall（バルクデータ）', 15, True)
text('card_sources', 36, 315, 'カード本体（英語）', 14)
text('card_sources', 36, 343, '日本語名・日本語本文・公式裁定', 14)
text('card_sources', 36, 392, 'Wizards of the Coast', 15, True)
text('card_sources', 36, 422, '（総合ルール）', 14)
text('card_sources', 36, 452, '条文・用語集', 14)
text('deck_sources', 326, 244, '大会・公開・製品デッキ', 18, True)
text('deck_sources', 326, 282, 'MTGO 公式デッキリスト', 15, True)
text('deck_sources', 326, 307, '大会デッキ', 14)
text('deck_sources', 326, 340, 'MTGTop8', 15, True)
text('deck_sources', 326, 365, '大会デッキ（紙の大会が主）', 14)
text('deck_sources', 326, 398, 'Moxfield', 15, True)
text('deck_sources', 326, 423, '多人数統率者戦の公開デッキ', 14)
text('deck_sources', 326, 456, 'MTGJSON（AllDeckFiles）', 14, True)
text('deck_sources', 326, 482, '構築済み製品のデッキ', 14)
text('lands_source', 616, 245, '17Lands', 20, True)
text('lands_source', 616, 280, 'Public Datasets', 16, True)
text('lands_source', 616, 320, 'ドラフトの生データ', 15)
text('lands_source', 616, 365, 'S3 の公開ファイル', 15)
text('lands_source', 616, 400, '1 セットにつき 1 回取得', 15)
text('card_import', 36, 633, '出所別の取り込み', 17, True)
text('card_import', 36, 665, 'バルク・公式テキストを取得', 14)
text('card_import', 36, 697, '出所ごとの脚本で取り込む', 14)
text('deck_import', 326, 633, '出所別の取り込み', 17, True)
text('deck_import', 326, 665, '大会・公開・製品デッキを取得', 14)
text('deck_import', 326, 697, '出所ごとの脚本で取り込む', 14)
text('lands_stream', 616, 633, 'ダウンロード・集計', 17, True)
text('lands_stream', 616, 665, 'gz を流し読み', 15)
text('lands_stream', 616, 697, 'セット × カードで集計', 15)
text('card_raw', 36, 845, '元の情報を表に保持', 17, True)
text('card_raw', 36, 880, 'カード情報・日本語の名前と本文', 14)
text('card_raw', 36, 910, '総合ルール・公式裁定', 14)
text('card_raw', 36, 947, '出所から取り込んだ情報を保持', 14)
text('deck_raw', 326, 845, '出所の表記のまま保持', 17, True)
text('deck_raw', 326, 880, 'デッキの見出し・明細', 15)
text('deck_raw', 326, 912, 'カード名は出所の表記のまま', 14)
text('deck_raw', 326, 946, 'カードとの対応は後から埋める', 14)
text('lands_stats', 616, 845, '集計値だけを表に保存', 17, True)
text('lands_stats', 616, 877, '勝率・ピック順など', 14)
text('lands_stats', 616, 908, '生データは DB・リポジトリに', 14)
text('lands_stats', 616, 934, '入れない', 14)
text('lands_stats', 616, 963, '生データは再配布しない', 14)
text('card_derive', 36, 1095, '導出情報を追加', 18, True)
text('card_derive', 36, 1130, 'カード本文から構造化', 15)
text('card_derive', 36, 1162, '除去・サーチ・ドロー・マナ加速', 14)
text('card_derive', 36, 1194, '導出列を作る', 15)
text('card_derive', 36, 1227, '総合ルール・公式裁定も保持', 14)
text('deck_derive', 326, 1095, '対応付け・集計', 18, True)
text('deck_derive', 326, 1130, 'デッキとカードを対応付け', 14)
text('deck_derive', 326, 1162, '元のカード名は保持（非破壊）', 14)
text('deck_derive', 326, 1194, 'フォーマット別の採用率', 15)
text('deck_derive', 326, 1227, '共起の集計', 15)
text('public_data', 40, 1386, '公開対象のデータ', 21, True)
text('public_data', 40, 1420, 'カード・ルール・裁定・デッキと、導出情報・集計表', 16)
text('public_data', 40, 1454, '17Lands 由来のデータは集計値のみ', 16)
text('exit', 300, 1605, '公開サーバーへ', 21, True)
text('exit', 300, 1643, '論理レプリケーション', 17)
text(None, 30, 1715, '矢印：データの流れ　／　出所・権利の詳細：docs/DATA_SOURCES.md', 14)


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
接続: {len(EDGES)} 本。全線分は水平または垂直、両端は対応する箱の辺に一致。
箱どうしの重複: 0
線と箱の交差・接触: 0（接続端点を除く）
線どうしの交差・接触・同一路径の重複: 0
異なる線の中心線最小距離: {minimum:g} px
線と無関係な箱: 中心線の周囲 5 px の余白を確保
文字の予約領域どうし、および文字と線・無関係な箱の重なり: 0
文字の予約領域: 和文 1 em、狭い文字 0.70 em、周囲 2 px の仮定
SVG の XML 構文、title / desc、白背景、外部参照なし、スクリプトなし: PASS
フォントによる実際の文字幅と描画の目視確認は未実施。

内容と範囲:
指定された 7 出所を 3 系統にまとめて表示。出所ごとの脚本は取り込みの箱に束ねる。
線は同一路径を共有せず、独立した系統を通って公開対象のデータへ入る。
導出は元の情報への追加を表す。ルール・裁定をカード本文の構造化に掛ける意味ではない。
デッキの元のカード名を保持し、カードへの対応を後から追加する。
17Lands は保存前に gz を流し読みして集計し、生データの保存段階を通らない。
生データは DB にもリポジトリにも入れず再配布しない。
図は依頼された取り込みの流れであり、全出所の履歴の網羅ではない。
件数、脚本名の列挙、表構造、機械配置、権利・ライセンスの条文は掲載しない。
根拠: docs/DATA_SOURCES.md の出所表・共通の礼儀・17Lands の注記、
DATA_MODEL.md のデッキ明細・導出情報・設計の考え方。
ローカル文書を読んだ範囲での整理。取得処理の実行や稼働 DB の検査は行っていない。
'''


def svg():
    desc = ('指定された七つの出所から取り込み・加工・公開へ至るデータの流れ。'
            'Scryfall のバルクから英語のカード本体、日本語名と日本語本文、公式裁定を取得。'
            'Wizards of the Coast から総合ルールの条文と用語集を取得。'
            'MTGO 公式デッキリストと MTGTop8 から大会デッキ、Moxfield から多人数統率者戦の公開デッキ、MTGJSON AllDeckFiles から構築済み製品のデッキを取得。'
            '出所ごとの脚本で取得済みをスキップし、同じコマンドで新規分だけを取り込む。'
            '元の情報を保持し、デッキのカード名は出所の表記のまま保存する。'
            '後からカード本文の構造化、デッキとカードの非破壊の対応付け、フォーマット別採用率、共起の集計を行う。'
            '17Lands Public Datasets は別経路で、S3 の公開ファイルを一セット一回取得し、gz を流し読みしてセットとカードごとの集計値だけを表に保存する。'
            '生データは DB にもリポジトリにも入れず再配布しない。'
            '公開対象のデータは論理レプリケーションで公開サーバーへ流れる。矢印はデータの流れを表す。')
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" font-family="{FONT}" role="img" aria-labelledby="title desc">',
           '<title id="title">mtg_sisho データの流れ</title>',
           f'<desc id="desc">{escape(desc)}</desc>',
           '<defs><marker id="arrow" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="8" refX="9" refY="4" orient="auto" viewBox="0 0 9 8"><path d="M0 0 L9 4 L0 8 Z" fill="#48566b"/></marker></defs>',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>']
    for n, (x, y, w, h, _) in BOXES.items():
        fill = '#ffffff' if n == 'policy' else '#f4f6fa'
        if n in ('public_data', 'exit'):
            fill = '#edf4ff'
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" stroke="#48566b" stroke-width="1.2"/>')
    for _, _, _, pts in EDGES:
        points = ' '.join(f'{x},{y}' for x, y in pts)
        out.append(f'<polyline points="{points}" fill="none" stroke="#48566b" stroke-width="1.8" stroke-linejoin="miter" marker-end="url(#arrow)"/>')
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
    (directory/'flow.svg').write_text(content, encoding='utf-8')
    (directory/'validation.txt').write_text(report, encoding='utf-8')
    print(report)

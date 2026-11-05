#!/usr/bin/env python
"""enrich_removal.py — oracle から除去メカ・対象型を導出して mtg_cards_v2 に列として持つ。

設計:
  target_types text[]  … 正規化した対象型＋クエリ頻出 qualifier トークン
                          (creature/permanent/artifact/enchantment/land/planeswalker/
                           player/spell/any + creature_spell/noncreature_spell)
                          → 絞り込み・GIN・配列統計(most_common_elems)用。%creature% の
                          先頭ワイルドカード LIKE を避け、noncreature 偽陽性を殺す。
  target jsonb         … フル句＋qualifier（例 "nonblack creature"）→ 条件付き判定と長い尾。
  removal_types text[] … メカ種別 destroy/exile/damage/minus/sacrifice/bounce/tuck/pacify → is_removal 相当・絞り込み。
  removal jsonb        … 詳細 {type,object,amount,stat,permanent,targeted} → 順位づけ・恒久性。
                          項目によっては条件の印も付く: requires（対象の状況・性質）・variable（量が X など可変）・
                          upgrade_amount / upgrade_by（条件を満たすと上がる量とその条件名）・mv_max / mv_max_upgrade・
                          power_max・extra_cost（キッカーの点数）。印の語彙は下の _REQ_PATTERNS などにある物だけ＝
                          語彙の外の条件は付けない。除去の列の作り込みは、ある程度機能していれば切り上げる方針
                          （語彙を足し続けない）。
不在は NULL（番兵禁止・data_handling 規約）。導出列なので reembed 不要。
冪等: 再実行しても同じ結果（値が変わる行だけ UPDATE する）。新セット取り込み後はこれを再実行して populate（索引はデータに自動追随）。
--dry-run: 接続を読み取り専用にして差分の集計だけ出す（--report <ファイル> で差分の一覧を書く・--migrate が付いていても DDL は打たない）。
"""
import json, re, sys
import psycopg2
from psycopg2.extras import Json, execute_batch
from db_config import get_db_config, migrate_requested, require_columns, require_indexes

TT = ["creature", "permanent", "artifact", "enchantment", "land",
      "planeswalker", "player", "spell"]


def strip_reminder(t):
    return re.sub(r'\([^)]*\)', '', t or '')


def castable_oracle(oracle_text, card_faces_json):
    """役割列の導出に使うテキスト＝「手札から唱えられる面」だけの oracle。
    規則は face_cmcs/face_types と同一（mana_cost 非空の面のみ・全面空なら表面
    フォールバック）。
    全文（裏面込み）をパースすると、変身カードの唱えられない裏面にしか無い
    destroy/exile が役割タグに混ざって機構ゲートを通してしまう
    （Elesh Norn の destroy=裏面英雄譚 III 章のみ、で確認）ので唱えられる面だけを使う。
    単面カードは全文のまま＝導出結果は変わらない。全面 castable（split/adventure/MDFC）は ' // ' 連結＝oracle_text
    と同形＝これも不変。"""
    faces = card_faces_json or []
    if not isinstance(faces, list) or len(faces) < 2:
        return oracle_text
    castable = [f for f in faces if (f.get('mana_cost') or '').strip()]
    if not castable:
        castable = faces[:1]
    return ' // '.join((f.get('oracle_text') or '') for f in castable)


def find_types(p):
    return [t for t in TT if re.search(r'\b' + t + r's?\b', p)]


# 対象句のトークン: アポストロフィ・数字込み（実例: 紅蓮破の
# "if it's blue" は文字クラス [a-z\-] だとアポストロフィで句切れて青が消える）
TOK = r"[a-z0-9'\-]+"


# ---- 役割の列の直し（噛みつき・格闘・ダメージの行き先・固めるオーラ・自分への -1/-1）用の部品 ----

# ダメージの行き先を切り取る終端。「for each / equal to / where …」以降は量の説明で、
# 行き先ではない（"to each opponent for each creature you control" の creature は行き先でない）
_DMG_STOP_RE = re.compile(
    r" for each | where | equal to | instead| unless | if | then |, you | and you | and gains? ")
# 「that creature's controller」「its owner」など、持ち主・操縦者に向かう語（クリーチャーの語を含んでも
# 行き先はプレイヤー）
_CONTROLLER_RE = re.compile(
    r"\b(?:the )?(?:controller|owner)s? of (?:that|target|the|this|enchanted|it)\b[a-z' \-]*?(?=$|[,.]| and | or )"
    r"|\b(?:(?:that|this|the|target|enchanted|each|its|their)\s+)?(?:[a-z\-]+\s+)?"
    r"(?:(?:[a-z\-]+'s\s+(?:or\s+)?)+|its\s+|their\s+)?(?:controller|owner)s?\b")
# クリーチャー・PW・バトル・パーマネントに届く語
_BOARD_WORD_RE = re.compile(
    r"\b(?:creatures?|planeswalkers?|battles?|permanents?)\b"
    r"|\bany (?:other )?target\b|\btargets\b|\b(?:another|other) target\b")


_DMG_PRONOUN_RE = re.compile(
    r"\s*(?:to )?(?:it|them|that creature|that permanent|those creatures|each of them)\b")
_DMG_ANTECEDENT_RE = re.compile(
    r"\b(?:a|an|each|another|target|that|those|any|enchanted)\s+(?:[a-z\-]+\s+)?"
    r"(creatures?|permanents?|planeswalkers?|artifacts?|enchantments?|lands?|players?|opponents?"
    r"|spells?|cards?)\b(\s+you control\b)?")


def _damage_dest(tl, start, end, pw_ok=True):
    """「deals N damage」（位置 start〜end）の行き先が、クリーチャー・プレインズウォーカー・
    バトル・パーマネントに届くなら {"seg": 行き先の句} を、届かなければ None を返す。
    プレイヤー（you / each opponent / target player / that player / its controller）に
    しか向かわない物は None。「to it / to that creature」のような代名詞は、直前の文脈で
    最後に出てくる名詞句がクリーチャー等（自分側でない）のときだけ届くとみなし、句には
    その先行詞を返す。pw_ok=False のときは、プレイヤーと並べた "or planeswalker" だけの
    行き先（target player or planeswalker）を届くとみなさない。"""
    seg = re.split(r"[.;•]", tl[end:], 1)[0]
    seg = _DMG_STOP_RE.split(seg, 1)[0]
    seg = _CONTROLLER_RE.sub(" ", seg)
    if _DMG_PRONOUN_RE.match(seg):
        w0 = max(0, start - 300)
        ants = list(_DMG_ANTECEDENT_RE.finditer(tl[w0:start]))
        if not ants:
            return None
        last = ants[-1]
        if (last.group(1).rstrip('s') not in ('creature', 'permanent', 'planeswalker')
                or last.group(2)):
            return None
        a0 = w0 + last.start()
        return {"seg": re.split(r"[,.;•]", tl[a0:a0 + 80], 1)[0]}
    reach = re.sub(r"\bplaneswalkers?\b", " ", seg) if not pw_ok else seg
    return {"seg": seg} if _BOARD_WORD_RE.search(reach) else None


# 噛みつき: 「deals damage equal to … to (another / up to one) target creature」と、語順違いの
# 「deals damage to target creature equal to …」。後者は自分のクリーチャーへ向かう物
# （target creature you control）を除く
_BITE_EQUAL_RE = re.compile(
    r"\bdeals? damage equal to [^.•;]{0,100}? to "
    r"(?:each of (?:up to )?\w+ )?(?:another |up to \w+ )?(?:other )?target "
    r"(?:[a-z\-]+ ){0,2}?(?:creature|planeswalker|permanent|battle)")
_BITE_ORDER_RE = re.compile(
    r"\bdeals? damage to (?:another |up to \w+ )?(?:other )?target "
    r"(?:creature|planeswalker)(?: or planeswalker)?(?! you control)[^.•;]{0,60}? equal to ")
# 格闘（総合ルール 701.14a: 互いにパワー分のダメージ）
_FIGHT_RE = re.compile(
    r"\bfights? (?:another |up to \w+ )?(?:other )?target "
    r"(?:[a-z\-]+ ){0,2}?(?:creature|planeswalker)")

# 固めるオーラ: 「Enchant …」の行に creature / permanent / planeswalker が入る物
# （Enchant artifact or creature・Enchant creature without flying・Enchant creature or
# planeswalker you don't control …）。付ける先が自分のクリーチャーに限られる物
# （… you control）と、墓地のカードに付く物は除く。
_AURA_LINE_RE = re.compile(
    r"^enchant (?!.*\byou control\b)(?!.*\bgraveyard\b).*\b(?:creature|permanent|planeswalker)\b")
_ENCHANTED = r"enchanted (?:creature|permanent)"
# 文頭が「Enchanted creature …」で、止める文の形。直後に if / unless / as long as が続く
# 条件付きの形（Oppressive Rays・Tangle Kelp）は付けない
_NO_COND = r"(?!\s+(?:unless|if|as long as)\b)"
_PACIFY_SENT_RES = (
    re.compile(_ENCHANTED + r" (?:loses all (?:other )?abilities and )?"
               r"can't attack or block\b" + _NO_COND),
    re.compile(_ENCHANTED + r" can't attack, block\b" + _NO_COND),
    re.compile(_ENCHANTED + r" (?:loses all (?:other )?abilities and )?"
               r"doesn't untap during its controller's untap step\b" + _NO_COND),
)


# ---- 条件の印（variable / upgrade_* / mv_max* / power_max / requires / object）用の部品 ----

# 対象の状況・性質の条件。語彙はここにある物だけ（外の条件は付けない）
_REQ_PATTERNS = (
    (re.compile(r"\battacking\b"), "attacking"),
    (re.compile(r"\bblocking\b"), "blocking"),
    (re.compile(r"(?<!un)\btapped\b"), "tapped"),
    (re.compile(r"\buntapped\b"), "untapped"),
    (re.compile(r"\bwithout flying\b|\bnonflying\b"), "without flying"),
    (re.compile(r"(?<!without )\bwith flying\b(?! or)|\bflying (?:creature|permanent)"), "flying"),
    (re.compile(r"\bcolored\b|\bone or more colors\b"), "colored"),
    (re.compile(r"\bnonartifact\b"), "nonartifact"),
    (re.compile(r"\bnoncreature\b"), "noncreature"),
    (re.compile(r"\bnonwhite\b"), "nonwhite"),
    (re.compile(r"\bnonblue\b"), "nonblue"),
    (re.compile(r"\bnonblack\b"), "nonblack"),
    (re.compile(r"\bnonred\b"), "nonred"),
    (re.compile(r"\bnongreen\b"), "nongreen"),
    (re.compile(r"\bnontoken\b"), "nontoken"),
    (re.compile(r"(?<!non)(?<!aren't )(?<!isn't )\blegendary\b"), "legendary"),
    (re.compile(r"\bnonlegendary\b|\b(?:aren't|isn't) legendary\b"), "nonlegendary"),
)
_HEAD_RE = re.compile(
    r"^\s*(?:another |up to \w+ |each |all |any number of )*(?:other )?(?:target )?"
    r"((?:[a-z0-9'\-]+,? (?:or )?){0,7}?"
    r"(?:creatures?|permanents?|planeswalkers?|artifacts?|enchantments?|lands?|battles?"
    r"|players?|opponents?|cards?|tokens?))\b([^,.;•]*)")
_PHRASE_CUT_RE = re.compile(
    r" unless | if | instead| until | equal to | and (?:you|its|that|it|they|each)\b| then ")


def _object_phrase(s):
    """動詞（や "damage to"）の直後の文字列から、対象の句（修飾語＋名詞＋後置の修飾）を取る"""
    m = _HEAD_RE.match(s)
    if not m:
        return ''
    return m.group(1) + _PHRASE_CUT_RE.split(m.group(2))[0]


def _requires(phrase):
    # 「the player or planeswalker it's attacking」の attacking は対象の条件でない
    phrase = re.sub(r"\bit(?:'s| is) attacking\b", " ", phrase)
    return [tok for rx, tok in _REQ_PATTERNS if rx.search(phrase)]


def _damage_object(seg):
    """ダメージの届く先が分かるときだけ値を返す（分からなければ None）"""
    if re.search(r"\bany (?:other )?target\b", seg):
        return "any"
    cre = re.search(r"\bcreatures?\b", seg)
    pw = re.search(r"\bplaneswalkers?\b", seg)
    if cre and pw:
        return "creature_or_planeswalker"
    if cre:
        return "creature"
    if pw:
        return "planeswalker"
    return None


def _sent_bounds(tln, pos):
    """tln（改行を残した小文字の本文）で pos を含む文の範囲（. • 改行で区切る）"""
    a = max(tln.rfind(c, 0, pos) for c in '.•\n') + 1
    ends = [k for k in (tln.find(c, pos) for c in '.•\n') if k != -1]
    return a, (min(ends) if ends else len(tln))


def _segments(tln):
    out, a = [], 0
    for m in re.finditer(r"[.•\n]", tln):
        out.append((a, m.start()))
        a = m.end()
    out.append((a, len(tln)))
    return out


def _upgrade_by(seg):
    """上がる条件の名前: キッカーなら "kicker"、能力語（"Delirium —" など）なら小文字の英語。
    分からなければ None"""
    if re.search(r"\bwas kicked\b", seg):
        return "kicker"
    m = re.match(r"\s*([a-z][a-z' ]{1,24}?)\s*—", seg)
    if m and not m.group(1).startswith("choose"):
        return m.group(1).strip()
    return None


def _upgrade_keys(tln, kind, base, home_a=None):
    """条件を満たすと上がる値（"… deals 6 damage instead"）を探す。
    kind = damage / minus（base = 今の点数）/ mv（base = 今の上限、home_a = 元の文の始まり）"""
    for a, b in _segments(tln):
        seg = tln[a:b]
        if ' instead' not in seg:
            continue
        if kind == 'damage':
            m = re.search(r"\bdeals? (\d+) damage(?: to [^,]*?)? instead", seg)
            key = 'upgrade_amount'
        elif kind == 'minus':
            m = re.search(r"[-−]\s?\d+/[-−]\s?(\d+)[^.]*? instead", seg)
            key = 'upgrade_amount'
        else:
            m = None if a == home_a else re.search(r"mana value (\d+) or less", seg)
            key = 'mv_max_upgrade'
        if m and isinstance(base, int) and int(m.group(1)) > base:
            out = {key: int(m.group(1))}
            by = _upgrade_by(seg)
            if by:
                out['upgrade_by'] = by
            return out
    return {}


def _annotate_damage(e, tln, seg):
    """damage の項目に条件の印を足す。seg = ダメージの行き先の句（行き先の文から取る）"""
    obj = _damage_object(seg)
    if obj:
        e['object'] = obj
    req = _requires(seg)
    if req:
        e['requires'] = req
    if e['amount'] is None or e['amount'] == 'X':
        e['variable'] = True
    else:
        e.update(_upgrade_keys(tln, 'damage', e['amount']))


def _annotate_target_entry(e, tl, tln, pos, verb_len):
    """destroy / exile の項目に条件の印を足す。条件はその項目が出てきた文から読む"""
    a, b = _sent_bounds(tln, pos)
    hs = tln[a:b]
    req = _requires(_object_phrase(tl[pos + verb_len:b]))
    if req:
        e['requires'] = req
    mv = re.search(r"\b(?:with|has|have) (?:a )?mana value (\d+) or less", hs)
    if mv:
        e['mv_max'] = int(mv.group(1))
        e.update(_upgrade_keys(tln, 'mv', e['mv_max'], home_a=a))
    if re.search(r"\bmana value (?:is )?(?:x or less|less than or equal to|equal to)", hs):
        e['variable'] = True
    pw = re.search(r"\b(?:with|has|have) power (\d+) or less", hs)
    if pw:
        e['power_max'] = int(pw.group(1))


def _is_pacify_aura(oracle):
    """本文に Enchant 行（相手のクリーチャーにも付けられる形）があり、付けたクリーチャーを
    止める文（攻撃もブロックもできない・アンタップしない・全能力喪失＋基本P/T上書き）を持つか。
    条件付きの文（unless / if / as long as …）は付けない（精度優先）。"""
    raw = strip_reminder(oracle or '').lower().replace(' // ', '\n')
    if not any(_AURA_LINE_RE.match(ln.strip().rstrip('.')) for ln in raw.split('\n')):
        return False
    # 乗っ取り（You control enchanted creature）と組になった物は止めるオーラでない
    if re.search(r"\bcontrols? enchanted\b", raw):
        return False
    for sent in re.split(r"[.\n•]", raw):
        sent = sent.strip()
        if any(r.match(sent) for r in _PACIFY_SENT_RES):
            return True
        if (re.match(_ENCHANTED + r"\b", sent)
                and not re.search(r"\b(?:unless|if|as long as)\b", sent)
                and re.search(r"\bloses all (?:other )?abilities\b", sent)
                and 'base power and toughness' in sent):
            return True
    return False


_MINUS_RE = re.compile(r'[-−]\s?(\d+|x)/[-−]\s?(\d+|x)')


_ANAPHORA_RE = re.compile(
    r"(?:it\b|them\b|that (?:creature|permanent)\b|those creatures\b|the other\b|each of them\b)")
_TRIGGER_LEAD_RE = re.compile(
    r"^\s*(?:(?:when|whenever|at the beginning of|if|as long as)\b[^,]*,\s*)+")
_TARGET_CREATURE_RE = re.compile(r"target (?:[a-z\-]+ ){0,3}?creatures?\b(?! cards?)")


def _other_creature_phrase(p, prior=''):
    """「target creature」「each creature」「creatures an opponent controls」など、自分以外の
    クリーチャーに届く語句か。this / 自分の側（you control）は False。
    「it / that creature / the other」は、直前の文脈（prior）の最後の「target creature」が
    自分側（you control）でなければ、その対象を指すとみなす。"""
    p = p.strip()
    if _ANAPHORA_RE.match(p):
        ants = list(_TARGET_CREATURE_RE.finditer(prior))
        if not ants:
            return False
        tail = prior[ants[-1].end():ants[-1].end() + 40]
        return not re.match(r"[^,.:;]*\byou control\b", tail)
    if not re.search(r'\b(?:creatures?|permanents?)\b', p):
        return False
    if re.match(r"(?:this|~|itself|its|equipped)", p):
        return False
    if re.search(r"\byou control\b", p):
        return False
    return True


def _minus_hits_other(tl, m):
    """-N/-N の出現 m が、自分以外のクリーチャーに届く物か。
    「target creature gets -N/-N」「put … -1/-1 counter(s) on target creature」
    「all creatures get -N/-N」は True、「enters with … counters on it」
    「put a -1/-1 counter on this creature」は None。届くときは対象の句（無ければ ''）を返す。"""
    s0 = max(tl.rfind(ch, 0, m.start()) for ch in '.•:;') + 1
    ctx = tl[s0:m.start()]
    prior = tl[:m.start()]
    after = re.split(r"[.;•]", tl[m.end():], 1)[0]
    ma = re.match(r"\s*counters?\s+among\s+(.*)", after)
    if ma:
        return ma.group(1) if _other_creature_phrase(ma.group(1), prior) else None
    mo = re.match(r"\s*counters?\s+on\s+(.*)", after)
    if mo:
        ph = re.split(r"[,:]| and | for each | until | with ", mo.group(1))[0]
        return mo.group(1) if _other_creature_phrase(ph, prior) else None
    if re.match(r"\s*counters?\b", after) is None:
        mg = re.search(r"\bgets?\s+(?:an additional |twice )?(?:[^.]*? or )?$", ctx)
    else:
        mg = re.search(r"\bgets?\s+(?:an? |an additional )?$", ctx)
    if mg:
        # 主語の句だけを取り出す: コスト（':' まで）・誘発の前置き（when …,）・「then」・
        # 並列の別の主語（and target …）を落とす
        clause = re.split(r":|\bthen\b|\band (?=(?:target|each|all|another|up to)\b)",
                          ctx[:mg.start()])[-1]
        clause = _TRIGGER_LEAD_RE.sub('', clause)
        # オーラの "… as long as it's a Zombie. Otherwise, it gets -3/-3.":
        # 付けた先のクリーチャーが条件を満たさなければ縮む（相手のクリーチャーに付ける用途）
        if (re.match(r"otherwise,?\s+it\s*$", clause.strip())
                and 'enchanted creature' in prior):
            return ''
        return clause if _other_creature_phrase(clause, prior) else None
    return None


def parse(oracle):
    t = strip_reminder(oracle).replace('\n', ' ')
    tl = t.lower()
    # 改行を残した版（文・行の区切りを見る用。文字の位置は tl と同じ）
    tln = strip_reminder(oracle).lower()
    if len(tln) != len(tl):
        tln = tl
    target_types, target_detail = set(), []
    # リスト列挙（A, B, or C）を丸ごと取る（実例: 失せろの
    # 「クリーチャー、エンチャント、PW」の 2 項目以降が消えていた）
    for m in re.finditer(
            rf"target ({TOK}(?:[, ]+(?:or )?{TOK}){{0,10}})", tl):
        phrase = m.group(1).strip().rstrip(',')
        words = [w.strip(',') for w in phrase.split()]
        # 対象になる判定（実例: 熾火心の挑戦者）: 「becomes the target of a spell」
        # ＝"対象に**なる**" 側の句。"target of ..." は対象を**取る**句ではないので
        # 型抽出から除外（counter 役割の逆向き偽陽性）
        if words and words[0] == 'of':
            continue
        if 'spell' in words:
            target_types.add('spell')
            i = words.index('spell')
            q = words[i - 1] if i > 0 else None
            if q == 'creature':
                target_types.add('creature_spell')
            elif q == 'noncreature':
                target_types.add('noncreature_spell')
            target_detail.append({"phrase": ' '.join(words[:i + 1]), "type": "spell",
                                  "qualifier": (q if q not in (None, 'target') else None)})
            # 条件付きカウンター判定（4 類型のうち機械化できる 3 つ）:
            #  (a) 対象制限 = spell の前後に修飾語。前置=「noncreature spell」型／
            #      後置=「spell that targets ...」「spell with mana value ...」型
            #  (b) MV 制限 = "spell with mana value" 型（後置に含まれる）
            #  (c) ソフト  = "unless ... pays" 型（Mana Leak / Force Void 系）
            # 状態依存（"if you control..."）は言い回しが多様なので v1 では拾わない（過小
            # 検出側に倒れる＝条件付きが plain 扱いになるだけ・偽陽性は出ない）。
            # ピッチ等の代替コストは「唱えること」への条件＝打ち消しは無条件（実例: Force of Will）
            # なので、ここでは counter 句の修飾と unless だけを見る。
            _soft  = bool(re.search(r'unless[^.]{0,60}pays?', tl))
            # 後置修飾が unless 節そのもの（"spell unless its controller pays"）の
            # ときは範囲でない。"spell that targets/with mana value/if it's blue" 等の
            # 後置だけを scope に数える
            _scope = ((q not in (None, 'target'))
                      or (i + 1 < len(words) and words[i + 1] != 'unless'))
            if _soft or _scope:
                target_types.add('spell_conditional')
                # 類型分割: 「確定カウンター」の
                # 採点の線も同じ精神＝範囲制限（対象/MV）は確定性を削らない・
                # 支払い回避（unless pays）だけが不確定。soft/scope を別トークンに
                # して機械判定可能にする。既存 spell_conditional は不変（挙動据え置き・
                # 状態依存は v1 同様未捕捉＝過小検出側・ワークシートの目視対象）。
                if _soft:
                    target_types.add('spell_conditional_soft')
                if _scope:
                    target_types.add('spell_conditional_scope')
        else:
            # or/カンマの型別名列挙は broadening＝qualifier に混ぜない。
            # 各セグメントの主型を 1 語ずつ除いた残りだけが qualifier
            # （nonartifact / if it's blue / an opponent controls / 複合型の余り）。
            segs = [s.strip() for s in
                    re.split(r',\s*(?:or\s+)?|\s+or\s+', phrase) if s.strip()]
            seg_prims, leftovers = [], []
            for seg in segs:
                sts = find_types(seg)
                if not sts:
                    leftovers.append(seg)
                    continue
                prim = sts[0]
                seg_prims.append(prim)
                sw = seg.split()
                k = next((i for i, w in enumerate(sw)
                          if re.match(r'^' + prim + r's?$', w)), None)
                if k is not None:
                    sw = sw[:k] + sw[k + 1:]
                if sw:
                    leftovers.append(' '.join(sw))
            if seg_prims:
                target_types.update(seg_prims)
                qual = ' '.join(leftovers).strip()
                detail = {"phrase": phrase, "type": seg_prims[0],
                          "qualifier": qual or None}
                if len(seg_prims) > 1:
                    detail["alts"] = seg_prims[1:]
                target_detail.append(detail)
    if 'any target' in tl:
        target_types.add('any')

    removal = []

    def obj(v, start=0):
        """(第一クラス, 全クラス list|None, 対象句の生テキスト)。リスト列挙に対応。
        start = 探し始める位置（本文に同じ動詞が複数あるとき、先頭の別の句を拾わない）"""
        m = re.search(v + rf' (?:another )?(?:target |all |each |up to \w+ )?'
                          rf'({TOK}(?:[, ]+(?:or )?{TOK}){{0,6}})', tl[start:])
        phrase = m.group(1) if m else ''
        ts = find_types(phrase)
        return (ts[0] if ts else None), (ts if len(ts) > 1 else None), phrase

    # 領域ガード（実例: アガサの魂の大釜）: 「card from a graveyard」等の
    # 墓地/ライブラリ/手札のカード操作は盤面除去でない（bounce/tuck の領域ガードと同じ考え方）。
    ZONE_RE = re.compile(r'\b(cards?|graveyards?|library|hand)\b')

    def _entry(typ, first, alls, **kw):
        e = {"type": typ, "object": first, **kw}
        if alls:
            e["objects"] = alls   # 複数クラス時のみ（幅=異なり数の材料）
        return e

    def _is_targeted(verb):
        """「up to one (other) target creature」も対象を取る（実例: Solitude）"""
        return bool(re.search(
            verb + r' (?:another )?(?:up to \w+ )?(?:other )?target', tl))

    m = re.search(r'destroy (?:another )?(target|all|each|up to)', tl)
    if m:
        o1, oa, oph = obj("destroy")
        if not ZONE_RE.search(oph):
            e = _entry("destroy", o1, oa,
                       targeted=_is_targeted("destroy"), permanent=True)
            _annotate_target_entry(e, tl, tln, m.start(), len("destroy "))
            removal.append(e)
    # 追放: 動詞の出現ごとに対象句を見て、墓地・手札などの領域操作（exile all graveyards /
    # exile target player's graveyard）は飛ばし、盤面の句を拾う（本文の先頭の
    # 「exile this artifact」型の別の句を対象句として読むと領域ガードが効かないため）。
    # ブリンク（追放して戦場に戻す＝除去でない）は項目そのものを付けない:
    # 「until end of turn / until the next」型と「(then) return it/that card/those cards/them
    # to the battlefield」型。アンカー型の「When ~ leaves the battlefield, return the exiled
    # card ...」は代名詞でなく "the exiled card" なのでここに掛からない＝除去のまま。
    # 戻す文が条件で決まるもの（"If the gift wasn't promised, return …"・"If you don't, return …"・
    # "Otherwise …"）は、戦場に残る道があるので除去のまま。追放から戻すまでの間に if / unless /
    # otherwise があれば条件付き（"if you do" は追放が起きた結果の続きで条件に数えない）。
    def _unconditional_return(m):
        k = tl.rfind('exile', 0, m.start())
        between = re.sub(r'\b(?:if|when) you do\b', ' ', tl[max(k, 0):m.start()])
        return not re.search(r'\b(?:if|unless|otherwise)\b', between)
    blink = any(_unconditional_return(m) for m in re.finditer(
        r'return (it|that card|those cards|them) to the battlefield', tl))
    temp_exile = re.search(r'exile[^.]*until (end of turn|the next)', tl)
    if not (blink or temp_exile):
        for m in re.finditer(r'exile (?:another )?(target|all|each|up to)', tl):
            o1, oa, oph = obj("exile", m.start())
            # 領域操作（墓地など）と、自分のパーマネントを追放する句
            # （"exile all other Nightmare tokens you control"）は除去でない
            if ZONE_RE.search(oph) or re.search(r"\byou control\b", oph):
                continue
            # 呪文だけを追放する句（"exile target spell"）は盤面の除去でない
            if (re.search(r"\bspells?\b", oph)
                    and not find_types(re.sub(r"\b(?:[a-z\-]+\s+)?spells?\b", " ", oph))):
                continue
            e = _entry("exile", o1, oa,
                       targeted=_is_targeted("exile"), permanent=True)
            _annotate_target_entry(e, tl, tln, m.start(), len("exile "))
            removal.append(e)
            break
    # ダメージ: 数字つきの文のうち、クリーチャー・PW・バトル・パーマネントに届く最初の文を
    # 採る。プレイヤー・自分にしか届かない物（Adarkar Wastes・Mana Crypt・Guttersnipe …）は
    # 除去でない。
    for m in re.finditer(r'deals? (\d+|x) damage', tl):
        dest = _damage_dest(tl, m.start(), m.end())
        if dest is None:
            continue
        # targeted 判定拡張（実例: 激情）: 「divided as you choose among
        # ... target creatures」の割り振り構文も対象を取る。"any target"・"to target" の 2 パターンに加え
        # 「damage と target が同文内で近接」を捕捉（全体火力 each/all は含まれない）
        dmg_targeted = bool(
            'any target' in tl or 'to target' in tl
            or re.search(r'deals? (?:\d+|x) damage[^.]{0,60}\btargets?\b', tl))
        e = {"type": "damage",
             "amount": (m.group(1).upper() if m.group(1) == 'x' else int(m.group(1))),
             "targeted": dmg_targeted}
        _annotate_damage(e, tln, dest["seg"])
        removal.append(e)
        break
    # 噛みつき・格闘: 数字が無いが、対象のクリーチャー等にパワー分などのダメージを与える。
    # 量は本文から読めないので amount は null（不在は NULL）。
    if not any(r["type"] == "damage" for r in removal):
        mbite = _BITE_EQUAL_RE.search(tl) or _BITE_ORDER_RE.search(tl)
        mfight = None if mbite else _FIGHT_RE.search(tl)
        if mbite or mfight:
            if mbite:
                k = re.search(r"\bdamage (?:equal )?to ", mbite.group(0))
                # 噛みつきの行き先は最後の " to " の後ろ（"damage equal to its power to target …"）
                k = mbite.group(0).rfind(" to ") + 4 if mbite.re is _BITE_EQUAL_RE else k.end()
                pos = mbite.start() + k
            else:
                pos = mfight.start() + len(re.match(r"fights?\s+", mfight.group(0)).group(0))
            e = {"type": "damage", "amount": None, "targeted": True}
            _annotate_damage(e, tln, _object_phrase(tl[pos:_sent_bounds(tln, pos)[1]]))
            removal.append(e)
        else:
            # 「deals that much damage to …」（Galvanic Discharge・Harnessed Lightning 型）:
            # 量は他の効果で決まるので amount は null。行き先の規則は数字つきと同じ。
            # targeted は、行き先の句または代名詞の先行詞に "target" があるとき。
            for m in re.finditer(r'\bdeals? (?:twice )?that much damage', tl):
                tail = re.split(r"[.;•]", tl[m.end():], 1)[0]
                # ダメージ増減の置換（"would deal damage … deals that much damage plus 2 … instead"）は
                # ダメージを与える効果そのものでない
                if (re.match(r"\s+(?:plus|minus)\b", tail) or ' instead' in tail
                        or re.search(r"\bwould (?:deal|be dealt)\b", tl[max(0, m.start() - 200):m.start()])):
                    continue
                dest = _damage_dest(tl, m.start(), m.end(), pw_ok=False)
                if dest is None:
                    continue
                targeted = bool(re.search(r'\btarget\b', tail)) or bool(
                    _DMG_PRONOUN_RE.match(tail)
                    and re.search(r'\btarget\b', tl[max(0, m.start() - 300):m.start()]))
                e = {"type": "damage", "amount": None, "targeted": targeted}
                _annotate_damage(e, tln, dest["seg"])
                removal.append(e)
                break
    # マイナス: 自分以外のクリーチャーに届く -N/-N だけ（自分に置く -1/-1 カウンター・
    # 「enters with six -1/-1 counters on it」は除く）。最初の該当を採る。
    for m in _MINUS_RE.finditer(tl):
        if not (m.group(2) == 'x' or (m.group(2).isdigit() and int(m.group(2)) > 0)):
            continue
        mphrase = _minus_hits_other(tl, m)
        if mphrase is None:
            continue
        # targeted フラグ追加（実例: 税血の収穫者）: 補足規約の
        # 「対象を取る minus は機構不問クエリで 2」の機械化材料
        e = {"type": "minus", "stat": "toughness",
             "amount": (m.group(2).upper() if m.group(2) == 'x' else int(m.group(2))),
             "targeted": bool(re.search(
                 r'target creature[^.]{0,40}gets? [-−]', tl)),
             "permanent": 'until end of turn' not in tl}
        req = _requires(_object_phrase(mphrase))
        if req:
            e['requires'] = req
        if e['amount'] == 'X':
            e['variable'] = True
        else:
            e.update(_upgrade_keys(tln, 'minus', e['amount']))
        removal.append(e)
        break
    for ms in re.finditer(r'(player|opponent)s?\s+sacrifices?', tl):
        # 誘発の条件の中の sacrifice（"Whenever a player sacrifices a creature, …"）は除去でない
        # （条件節は最初のコンマまで）
        a, _b = _sent_bounds(tln, ms.start())
        pre = tl[a:ms.start()]
        if re.match(r"\s*(?:whenever|when|each time|if|as long as)\b", pre) and ',' not in pre:
            continue
        removal.append({"type": "sacrifice", "targeted": 'target player' in tl})
        break
    # bounce=盤面のパーマネントを手札に戻す。所有格 "owner's hand" があるので hand は別途
    # in-check。「target ... card from graveyard/exile ...」＝墓地/追放からの回収はバウンスで
    # ないので、対象句（return target と to の間）に card/graveyard/exile があれば除外。
    # 呪文を手札に戻す（Bilbo's Gambit）は盤面の除去でない。「spell or permanent」のように
    # パーマネントも選べる句、別のモードで permanent を戻す句があれば bounce のまま。
    def _is_board_bounce(group):
        if re.search(r'\b(card|graveyard|exile)\b', group):
            return False
        return not all(re.match(r'(?:[a-z\-]+ )*?spells?\b', alt.strip())
                       for alt in re.split(r',\s*(?:or\s+)?|\s+or\s+', group))
    if 'hand' in tl and any(
            _is_board_bounce(mb.group(1)) for mb in re.finditer(
                r'return target ([a-z\- ]*?) to (?:its owner|their|your)', tl)):
        removal.append({"type": "bounce", "targeted": True, "permanent": False})
    # tuck=対象をライブラリの上/下へ送る or シャッフルして戻す（分類: バウンスより硬い＝除去）。
    # 墓地からの戻し（target ... card ... graveyard）は tuck でないので除外。
    mt = re.search(r'(?:put|shuffle) target ([a-z\- ]*?) '
                   r'(?:on (?:the )?(?:top|bottom) of|into) [^.]*?library', tl)
    if mt and not re.search(r'\b(card|graveyard|exile)\b', mt.group(1)):
        span = tl[mt.start():mt.end()]
        where = 'bottom' if 'bottom' in span else ('top' if 'top' in span else 'shuffle')
        removal.append({"type": "tuck", "targeted": True, "where": where,
                        "permanent": where != 'top'})

    # モード分解（実例: Burst Lightning）: キッカーで効果が伸びる
    # ダメージは「そのマナ込みの別モード」として追加エントリ化。
    # extra_cost = キッカーの点数（実効コスト = cmc + extra_cost）。
    # 本体のダメージがクリーチャー等に届く物（上で項目が付いた物）だけ。「deals 4 damage instead」
    # は行き先が本体と同じ。
    mk = re.search(r'kicker\s*(?:—|-)?\s*((?:\{[^}]+\})+)', tl)
    if mk and 'was kicked' in tl:
        md = re.search(r'kicked[^.]{0,80}?deals? (\d+) damage', tl)
        if md and any(r["type"] == "damage" for r in removal):
            e = {"type": "damage", "amount": int(md.group(1)),
                 "targeted": ('any target' in tl or 'to target' in tl),
                 "extra_cost": _mana_value(mk.group(1))}
            base = next(r for r in removal if r["type"] == "damage")
            if 'object' in base:
                e['object'] = base['object']
            removal.append(e)

    # 固めるオーラ（Pacifism・Arrest・Claustrophobia …）: 付けたクリーチャーを戦場に残したまま
    # 止める。種類 "pacify"（対象を取り、恒久）。
    if _is_pacify_aura(oracle):
        removal.append({"type": "pacify", "targeted": True, "permanent": True})

    # 追加コストの条件化（実例: Bone Splinters）: 採点規約の
    # 「下振れ・対称コスト付き＝条件」の写し。無条件を名乗れなくする印。
    if removal and re.search(r'as an additional cost to cast this (?:spell|card)', tl):
        for e in removal:
            e['add_cost'] = True

    r_types = sorted({r["type"] for r in removal})
    return (sorted(target_types) or None, target_detail or None,
            r_types or None, removal or None)


def _mana_value(sym: str) -> int:
    """'{4}{R}{R}' → 6。数値は加算・色/混成/C は 1 と数える"""
    total = 0
    for s in re.findall(r'\{([^}]+)\}', sym):
        total += int(s) if s.isdigit() else 1
    return total


def _colored_mv(mana_cost: str) -> int:
    """色拘束ぶんの点数（コスト軽減で割り込めない床）。X/Y は数えない"""
    return sum(1 for s in re.findall(r'\{([^}]+)\}', mana_cost or '')
               if not s.isdigit() and s.upper() not in ('X', 'Y'))


def floor_cost(oracle: str, mana_cost: str, cmc) -> float | None:
    """実効コストの床値（実例: 力線の束縛=1）。
    コスト軽減の仕組みはカードに書いてある＝ベストケースは内在。
    軽減なしのカードは None（不在は NULL・番兵禁止）。"""
    if not mana_cost:
        return None
    tl = strip_reminder(oracle or '').replace('\n', ' ').lower()
    red = re.search(r'costs \{(\d+)\} less to cast for each', tl)
    if red:
        if 'basic land type' in tl:   # 版図: 最大 5 タイプ
            return max(_colored_mv(mana_cost),
                       float(cmc or 0) - int(red.group(1)) * 5)
        return float(_colored_mv(mana_cost))   # 親和系: 無制限軽減→色拘束が床
    if re.search(r'\bdelve\b|affinity for', tl):
        return float(_colored_mv(mana_cost))
    # 想起（実例: Solitude が合成順位から落ちた件）:
    # マナの想起コストはその点数が床・ピッチ想起（手札から追放）はマナ 0 が床。
    # カードを失うコストはマナ軸の外＝層3（採用率）が織り込む（層設計文書の分業）。
    me = re.search(r'evoke\s*(?:—|-)?\s*((?:\{[^}]+\})+)', tl)
    if me:
        return float(_mana_value(me.group(1)))
    if re.search(r'evoke\s*(?:—|-)?\s*(?:exile|sacrifice|discard|pay)', tl):
        return 0.0
    # ピッチ呪文（FoW 型: 代替コストで唱える）: 代替文中のマナ記号の合計が床（無ければ 0）
    mp = re.search(r'you may [^.]{0,100}? rather than pay (?:this spell\'s|its) mana cost',
                   tl)
    if mp:
        return float(_mana_value(mp.group(0)))
    return None


DDL = """
ALTER TABLE mtg_cards_v2 ADD COLUMN IF NOT EXISTS target_types  text[];
ALTER TABLE mtg_cards_v2 ADD COLUMN IF NOT EXISTS target        jsonb;
ALTER TABLE mtg_cards_v2 ADD COLUMN IF NOT EXISTS removal_types text[];
ALTER TABLE mtg_cards_v2 ADD COLUMN IF NOT EXISTS removal       jsonb;
ALTER TABLE mtg_cards_v2 ADD COLUMN IF NOT EXISTS floor_cmc     numeric;
"""
IDX = """
CREATE INDEX IF NOT EXISTS mtg_cards_v2_target_types_gin  ON mtg_cards_v2 USING gin (target_types);
CREATE INDEX IF NOT EXISTS mtg_cards_v2_removal_types_gin ON mtg_cards_v2 USING gin (removal_types);
"""


def _arg_value(flag, argv=None):
    """`--flag 値` の値（無ければ None）。argparse を持たない脚本の流儀（migrate_requested と同じ）"""
    argv = list(sys.argv[1:] if argv is None else argv)
    if flag in argv:
        k = argv.index(flag)
        if k + 1 < len(argv):
            return argv[k + 1]
    return None


def _json_text(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True) if v is not None else ''


def _removal_diff_summary(changes):
    """changes = [(id, name, old_types, new_types, old_removal, new_removal)] →
    種類ごとの足された枚数・外された枚数（removal_types の増減）を出力する。"""
    added, dropped = {}, {}
    detail_only = 0
    for _cid, _n, ot, nt, orem, nrem in changes:
        o, n = set(ot or []), set(nt or [])
        for k in n - o:
            added[k] = added.get(k, 0) + 1
        for k in o - n:
            dropped[k] = dropped.get(k, 0) + 1
        if o == n:
            detail_only += 1
    print(f"removal / removal_types が変わる行: {len(changes)} 件")
    for k in sorted(set(added) | set(dropped)):
        print(f"  {k}: 足された {added.get(k, 0)} 枚 / 外された {dropped.get(k, 0)} 枚")
    print(f"  種類は同じで中身（object・amount など）だけ変わる: {detail_only} 件")


def _key_summary(all_removal):
    """全カードの新しい removal の項目について、条件の印が付いた項目の数を鍵ごとに出力する"""
    counts, values = {}, {}
    for rem in all_removal:
        for e in rem or []:
            for k in ('variable', 'upgrade_amount', 'mv_max', 'mv_max_upgrade', 'power_max',
                      'upgrade_by', 'requires', 'object'):
                if k in e:
                    counts[k] = counts.get(k, 0) + 1
            if e['type'] == 'damage' and 'object' in e:
                values[('object', e['object'])] = values.get(('object', e['object']), 0) + 1
            for w in e.get('requires') or []:
                values[('requires', w)] = values.get(('requires', w), 0) + 1
            if 'upgrade_by' in e:
                values[('upgrade_by', e['upgrade_by'])] = values.get(('upgrade_by', e['upgrade_by']), 0) + 1
    print("条件の印が付いた項目の数（全カードの新しい removal・damage の object は damage の項目だけ）:")
    for k in ('variable', 'upgrade_amount', 'upgrade_by', 'mv_max', 'mv_max_upgrade', 'power_max'):
        print(f"  {k}: {counts.get(k, 0)}")
    for grp in ('requires', 'object', 'upgrade_by'):
        items = sorted((v, k[1]) for k, v in values.items() if k[0] == grp)
        print(f"  {grp}: " + ', '.join(f"{name}={n}" for n, name in sorted(items, reverse=True)))


def main():
    dry_run = "--dry-run" in sys.argv[1:]
    report = _arg_value("--report")
    cfg = get_db_config()
    conn = psycopg2.connect(**cfg)
    if dry_run:
        # 書き込まない: 接続そのものを読み取り専用にする（UPDATE・ANALYZE・DDL・索引作成は全て打たない）
        conn.set_session(readonly=True)
    cur = conn.cursor()
    # 通常運転では DDL を打たない（--dry-run のときは --migrate が付いていても打たない）
    require_columns(conn, "mtg_cards_v2",
                    ("target_types", "target", "removal_types", "removal", "floor_cmc"),
                    DDL, migrate=(migrate_requested() and not dry_run), label="DDL")

    # 面対応: 導出入力を「唱えられる面」に限定。単面は結果不変なので、
    # 書き込みは値が実際に変わる行だけ（無駄な全行 UPDATE の物理チャーン回避）。
    cur.execute("""SELECT id, card_name, oracle_text, card_faces_json, mana_cost, cmc,
                          target_types, target, removal_types, removal, floor_cmc
                   FROM mtg_cards_v2""")
    rows = cur.fetchall()
    updates = []
    changes = []        # 除去の列（removal_types / removal）が変わる行
    other_changes = 0   # それ以外の列（target_types / target / floor_cmc）だけが変わる行
    all_new_removal = []
    for cid, name, ot, cfj, mc, cmc, cur_tt, cur_td, cur_rt, cur_rem, cur_fc in rows:
        tt, td, rt, rem = parse(castable_oracle(ot, cfj))
        fc = floor_cost(ot, mc, cmc)
        all_new_removal.append(rem)
        new = (tt or None, td or None, rt or None, rem or None, fc)
        old = (cur_tt or None, cur_td or None, cur_rt or None, cur_rem or None,
               float(cur_fc) if cur_fc is not None else None)
        if new == old:
            continue
        if (new[2], new[3]) != (old[2], old[3]):
            changes.append((cid, name, old[2], new[2], old[3], new[3]))
        else:
            other_changes += 1
        updates.append((tt, Json(td) if td else None, rt,
                        Json(rem) if rem else None, fc, cid))
    print(f"値が変わる行: {len(updates)} 件（他はスキップ）")

    if dry_run:
        _removal_diff_summary(changes)
        _key_summary(all_new_removal)
        print(f"  除去の列は同じで target_types / target / floor_cmc だけ変わる: {other_changes} 件")
        if report:
            with open(report, "w", encoding="utf-8") as f:
                f.write("id\tcard_name\told_removal_types\tnew_removal_types\told_removal\tnew_removal\n")
                for cid, name, ot_, nt_, orem, nrem in sorted(changes, key=lambda r: str(r[1])):
                    f.write("\t".join([
                        str(cid), str(name or ''),
                        ",".join(ot_) if ot_ else '', ",".join(nt_) if nt_ else '',
                        _json_text(orem), _json_text(nrem)]) + "\n")
            print(f"差分を書きました: {report}")
        conn.rollback()
        conn.close()
        return

    execute_batch(cur,
                  "UPDATE mtg_cards_v2 SET target_types=%s, target=%s, removal_types=%s, removal=%s, floor_cmc=%s WHERE id=%s",
                  updates, page_size=1000)
    conn.commit()

    require_indexes(conn, "mtg_cards_v2",
                    ("mtg_cards_v2_target_types_gin", "mtg_cards_v2_removal_types_gin"),
                    IDX, migrate=migrate_requested())
    conn.commit()
    cur.execute("ANALYZE mtg_cards_v2")
    conn.commit()

    cur.execute("SELECT count(*) FROM mtg_cards_v2 WHERE removal_types IS NOT NULL")
    n_removal = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM mtg_cards_v2 WHERE target_types IS NOT NULL")
    n_target = cur.fetchone()[0]
    print(f"populate 完了: 全{len(rows)}件 / removal あり {n_removal} / target あり {n_target}")
    conn.close()


if __name__ == "__main__":
    main()

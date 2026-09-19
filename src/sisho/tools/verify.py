"""verify.py — 答案検査の道具 verify_answer（2026-09-05 Step 2 で mcp_server.py から切り出し）。

登録（server.tool）は mcp_server.py 側。ここは DESCRIPTION と素の関数だけを持つ。
"""
from sisho.db import _db
from sisho.names import face_display
from sisho.toollog import _log_tool


# ─── 答案検査 ────────────────────────
# 書式ベンチの残穴は「道具の返り値に出ないカードをクライアントが記憶で挙げて自分で訳す」型
# （Sonnet medium 10 問で 11 件・Opus low 200 問で 7 答案）。返り値側の同伴（_ja）では
# 届かないので、答案そのものを DB に当てる口を置く。クライアントが最後に一回呼べば届く。

_NAME_CACHE: dict = {"ts": 0.0}
_JA_STOP = {"ショック", "巻き添え", "レベルアップ", "ナズグル", "フラッシュバック", "トランプル", "生け贄",
            "破壊不能", "打ち消し", "呪禁", "瞬速", "警戒", "飛行", "速攻", "接死", "絆魂", "威迫", "到達",
            "護法", "占術", "変身", "追放", "死亡", "召集", "探査", "続唱", "親和", "奇跡", "反復", "予見",
            "待機", "変容", "超過", "明滅", "接合", "倍増", "転生", "消術", "キッカー", "サイクリング",
            "マッドネス", "モーフ", "プロテクション"}


def _names() -> dict:
    """カード名表（1 時間キャッシュ）。ja→正式名・en→(ja or None)・両面は表名も登録。"""
    import time
    if time.time() - _NAME_CACHE["ts"] < 3600 and "ja" in _NAME_CACHE:
        return _NAME_CACHE
    # 面の列で組む。以前は裏面の英語名に表面の日本語名を割り当てていた（《厚かましい借り手/Petty Theft》型の誤接合）。
    #   en_ja: 英語（正式名／表面名／裏面名）→ 同じ粒度の日本語（無ければ None）
    #   ja_full: 日本語（空白抜き）→ 日本語（そのまま）  ja_en: 日本語 → 同じ粒度の英語
    #   裏面名は本物のカード名と同じことがある（prepare 20 枚）→ 裏面は setdefault＝本物のカードが勝つ
    rows = _db("SELECT card_name, japanese_name, digital, name_en_front, name_en_back, name_ja_front, name_ja_back,"
               " mana_cost, cmc, oracle_text FROM mtg_cards_v2", ())
    ja_full, en_ja, ja_en, digital, cost = {}, {}, {}, set(), {}
    for en, ja, dg, enf, enb, jaf, jab, mc, cmc, otext in rows:
        if dg:
            digital.update(x for x in (en, enf, enb) if x)
        # マナ・コストの照合用（正式名と表面名の両方から引ける）。軽減条項＝自分のコストが下がる文だけ
        rec = (mc or "", cmc, _reduction_clause(otext or ""))
        cost[en] = rec
        if enf:
            cost.setdefault(enf, rec)
        en_ja[en] = ja
        en_ja[enf] = jaf
        if enb:
            en_ja.setdefault(enb, jab)
        for j, e in ((ja, en), (jaf, enf), (jab, enb)):
            if j and e:
                ja_full[j.replace(" ", "")] = j
                ja_en.setdefault(j, e)
    # 裸の英語名検出用: 日本語名があり、5 文字以上か空白入り（短い一般語を避ける）
    en_bare = sorted((e for e, j in en_ja.items() if j and (len(e) >= 5 or " " in e)), key=len, reverse=True)
    # 報告専用（書き換えはしない）なので短い名前も拾う。閾値を 4 から 2 へ下げた:
    # 実測で「稲妻を 4 枚、島を 8 枚」型の答案が拾え、技術文書での誤ヒットは 0 件だった。
    ja_bare = sorted((j for j in ja_full.values() if len(j) >= 2 and j not in _JA_STOP), key=len, reverse=True)
    _NAME_CACHE.update({"ts": time.time(), "ja": ja_full, "en": en_ja, "ja_en": ja_en, "en_bare": en_bare, "ja_bare": ja_bare, "digital": digital, "cost": cost})
    return _NAME_CACHE


_RED_PAT = None


def _reduction_clause(oracle: str) -> str:
    """自分の呪文のコストが下がる条項（1 文）を返す。無ければ空。
    対象: 「This spell costs … less to cast」型・親和/召集/即興/探査/現出/徘徊（キーワードで下がる）。
    他人の呪文を安くする文（「Zombie spells you cast cost {1} less」）は拾わない。"""
    import re
    global _RED_PAT
    if _RED_PAT is None:
        _RED_PAT = re.compile(r"(this spell costs [^.]*less to cast[^.]*\.|\b(?:affinity for [^.(]+|convoke|improvise|delve|emerge|prowl)\b[^.\n]*)", re.I)
    m = _RED_PAT.search(oracle)
    return m.group(0).strip()[:160] if m else ""


def _mana_check(fixed: str, cost: dict) -> list[str]:
    """答案の完成形《日本語名/英語名》の近く（同じ文・前 30 字〜後 70 字）にある「N マナ」「{…}」を DB と照合。
    返り値は行の列（食い違い・注意・一致数）。主張が無ければ空＝何も足さない（毎回の税にしない・2026-09-12）。
    判定しない物: X を含むコスト・分割（A // B）・面の名前しか分からないカード。軽減条項のあるカードや
    「軽減・実質・安く・減」を含む文は食い違いにせず、条項の原文を添えて判断を答案側に戻す。"""
    import re
    def _fmt(cmc):
        return str(int(cmc)) if cmc is not None and float(cmc).is_integer() else str(cmc)
    mism, notes, ok = [], [], 0
    seen = set()
    for m in re.finditer(r"《([^《》/]+)/([^《》]+)》", fixed):
        en = m.group(2).strip()
        rec = cost.get(en)
        if not rec:
            continue
        mc, cmc, red = rec
        if "{X}" in mc or " // " in mc or cmc is None:
            continue
        left = fixed[max(0, m.start() - 30):m.start()]
        for sep in ("。", "、", "》", "\n"):          # 前のカードの主張（「《A》は 1 マナ、」）を拾わない
            if sep in left:
                left = left[left.rfind(sep) + 1:]
        right = fixed[m.end():m.end() + 70]
        # 右窓も改行で切る（左窓は最初から切っていた）。実戦で出た誤検知 10 件のうち
        # 9 件がこれ——箇条書きの直後の行の数字を最終行のカードが拾う／カーブ表（「1 マナ: 《A》」の
        # 次の行が「2 マナ: 《B》」）で前の行のカードが次の行のラベルと照合される、の 2 型。
        # 行が変われば別の主張、が日本語の書き方の実態に合う。
        for sep in ("。", "\n"):
            if sep in right:
                right = right[:right.find(sep)]
        # 次のカード名の手前で切る。このとき「N マナの《B》」型は、日本語の語順として
        # 数字が助詞「の」で**後ろの名前に係る**＝B の主張なので、手前の A の窓から外す
        # （追加分。右窓を改行で切っただけでは、同じ行で読点がつながる
        # 「《A》は悪くないが、6 マナの《B》のほうが強い」で A が 6 マナと報告されていた。
        # 一つの数字が A と B の両方に配られ、A 側だけが食い違いになる形）。
        i = right.find("《")
        if i >= 0:
            right = re.sub(r"\d+\s*マナ\s*(?:の|な)?\s*$", "", right[:i])
        window = left + "《》" + right
        # 範囲表記「N から M マナ」（〜・~・- も）は、両端のどちらに当たっても一致とみなす。
        # 単純に findall すると後ろの M だけ拾って「5 から 6 マナ」を 6 マナの主張と読んでいた。
        rng = {int(b): (int(a), int(b)) for a, b in
               re.findall(r"(\d+)\s*(?:から|〜|~|–|-|ー)\s*(\d+)\s*マナ", window)}
        # 「N マナを加える／生み出す」は生み出す量、「N マナ分」は別の数量＝コストの主張ではない
        # （別モデルのレビュー: 《金粉の水蓮/Gilded Lotus》は 3 マナを加える。が
        # CMC 5 に対する食い違いとして誤報されていた）。数字ごとに直後の語で判定する。
        claims = []
        for mm in re.finditer(r"(\d+)\s*マナ", window):
            if re.match(r"\s*(?:を|が|も)?\s*(?:加え|足せ|足し|生み|生む|出せ|出る|出し|得|分|増や)",
                        window[mm.end():mm.end() + 10]):
                continue
            claims.append((int(mm.group(1)), "マナ"))
        claims += [(sym.replace(" ", ""), "記号") for sym in re.findall(r"(?:\{[0-9XWUBRGCSP/]+\})+", window)]
        if not claims:
            continue
        soft = bool(red) or bool(re.search(r"軽減|実質|安く|減|少なく", window))
        for val, kind in claims:
            key = (en, val, kind)
            if key in seen:
                continue
            seen.add(key)
            if kind == "マナ":
                lo, hi = rng.get(val, (val, val))
                if float(cmc).is_integer() and lo <= int(cmc) <= hi:
                    ok += 1
                elif soft:
                    notes.append(f"  - 《{m.group(1)}/{en}》: DB は {mc or '（コスト無し）'}（{_fmt(cmc)} マナ）・答案は {val} マナ"
                                 + (f"＝軽減条項あり「{red}」（軽減後の数字は答案側で確かめる）" if red else "（軽減・実質の文なので判定しない）"))
                else:
                    mism.append(f"  - 《{m.group(1)}/{en}》: DB では {mc or '（コスト無し）'}（{_fmt(cmc)} マナ）・答案は {val} マナ")
            else:
                if not mc:
                    continue
                if val == mc.replace(" ", ""):
                    ok += 1
                elif soft:
                    # 記号側にも軽減の判定を効かせる（マナ側にはあったのに抜けていた）。
                    # 実戦では軽減量を波括弧で書いた「コストが {2} 少なくなる」をコスト主張と読み、
                    # 同じ答案に「軽減条項があるので判定しない」という注意も並んで出ていた。
                    notes.append(f"  - 《{m.group(1)}/{en}》: DB は {mc}・答案は {val}"
                                 + (f"＝軽減条項あり「{red}」（軽減後の数字は答案側で確かめる）" if red
                                    else "（軽減・実質の文なので判定しない）"))
                else:
                    mism.append(f"  - 《{m.group(1)}/{en}》: DB では {mc}・答案は {val}")
    lines = []
    if mism:
        lines.append(f"マナ・コストの食い違い {len(mism)} 件（DB の mana_cost/cmc と答案の数字が合わない＝DB に合わせて直すこと）:")
        lines += mism
    if notes:
        lines.append(f"マナ・コストの注意 {len(notes)} 件（判定せず・条項を添える）:")
        lines += notes
    if ok and not mism and not notes:
        lines.append(f"マナ・コストの照合: {ok} 件一致。")
    elif ok:
        lines.append(f"（他に {ok} 件は DB と一致）")
    return lines


DESCRIPTION = (
    "【答えを出す前の最後の一手・必須】日本語でカード名を含む答えを書き上げたら、送信する前に必ず全文をこれに渡し、返った修正版を答えにする。"
    "答案中のカード名を DB に照合し、(1) 《》の中身が DB に無い名前（自分で訳した名前・誤字・略記）を列挙し、"
    "近い正式名の候補を添える (2) 《英語名》・《日本語名》を完成形《日本語名/英語名》に直した修正版の全文を返す。"
    "**《》に入っていない裸の名前は書き換えない**——DB のカード名と同じ語なら一覧で知らせるので、"
    "カードを指して書いたものは答案側で完成形に直すこと（一般語と同じ形のカード名があり、"
    "どちらの意味で書いたかは機械では決められないため）。"
    "未確認の名前が残っていれば、そのカードを search_mtg_cards で引いてから答えること。"
    "(3) 完成形のカード名の近くにある「N マナ」「{…}」を DB の mana_cost/cmc と照合し、食い違いを列挙する（X 呪文・分割は判定しない・"
    "軽減条項のあるカードは条項を添えて判定しない）。未確認ゼロ・食い違いゼロなら修正版をそのまま答えに使う。Web 不要・DB 直結・1 秒未満。")


def verify_answer(text: str) -> str:
    import re
    _log_tool("verify_answer", {"len": len(text)})
    N = _names()
    ja_full, en_ja, ja_en = N["ja"], N["en"], N["ja_en"]
    # 二重・三重の囲み《《X》》は照合の**前に**畳む。
    # 以前は末尾でだけ畳んでいたため、抽出の正規表現 [^》]+ が開き括弧を中身に含めて
    # 《稲妻 を拾い、(1)「未確認 1 件」と誤報し (2) 修正版も《稲妻》止まりで完成形に上がらなかった。
    # 抽出側の [^《》]+ と合わせて二重（三重以上も）の囲みを掟の内側に戻す。
    n_collapse = 0
    while True:
        text, k = re.subn(r"《《([^《》]*)》》", r"《\1》", text)
        if not k:
            break
        n_collapse += k
    brackets = re.findall(r"《([^《》]+)》", text)
    # 《X》（English）の English（初出添え）を控える＝X が DB に無いとき正式名を引く最強の手がかり
    en_after = {b.strip(): e.strip() for b, e in re.findall(r"《([^《》]+)》\s*[（(]([A-Za-z][^）)]*)[）)]", text)}
    unknown, fixed, n_fix = [], text, 0
    def _disp(ja, en):
        return face_display(en, ja)
    def _pair(ja, en):
        """同じ粒度の対から完成形を作る。正式名「A // B」なら表面の対《表ja/表en》（8/22 の掟）・面の名前ならその面の対。"""
        if " // " in en and ja and " // " in ja:
            return _disp(ja.split(" // ")[0], en.split(" // ")[0])
        return _disp(ja, en.split(" // ")[0] if " // " in en else en)
    def _noja(en):
        return face_display(en, None, en in N.get("digital", ()))
    def _sub_bracket(s, inner, repl):
        """《 inner 》を repl に置き換える。囲みの内側の空白を許す（2026-09-15）。

        中身は strip して照合しているので、《 Lightning Bolt 》のように空白があると
        str.replace が一致せず、**数えたのに直っていない**状態になっていた
        （「機械修正 1 箇所」と報告しつつ本文は空白入りのまま）。
        """
        return re.sub(r"《\s*" + re.escape(inner) + r"\s*》", repl.replace("\\", "\\\\"), s)
    # (0) 「Black Lotus（ブラック・ロータス）」型（英語主・日本語添え）→《ブラック・ロータス》（Black Lotus）
    for e, j in re.findall(r"(?<![A-Za-z《])([A-Z][A-Za-z'’,\- ]{3,}?)\s*[（(]([^（）()A-Za-z]{2,})[）)]", fixed):
        e2 = e.strip()
        if en_ja.get(e2) and en_ja[e2].replace(" ", "") == j.strip().replace(" ", ""):
            fixed = re.sub(re.escape(e) + r"\s*[（(]" + re.escape(j) + r"[）)]", _disp(en_ja[e2], e2), fixed)
            n_fix += 1
    # (1) 《》の中身
    for b in dict.fromkeys(x.strip() for x in brackets):
        key = b.replace(" ", "")
        if "/" in b and " // " not in b:                # 《日本語名/英語名》の完成形＝両半分が対で一致して初めて合格
            jpart, epart = b.split("/", 1)
            jkey, ekey = jpart.strip().replace(" ", ""), epart.strip()
            true_ja = en_ja.get(ekey)
            if true_ja is not None and jkey in (true_ja.replace(" ", ""), true_ja.split(" // ")[0].replace(" ", "")):
                continue
            if ekey in en_ja:                           # 英語半分は正しい・日本語半分が違う（記憶の訳）→ 正しい完成形を第一候補に
                cand = _pair(true_ja, ekey) if true_ja else _noja(ekey)
                unknown.append((b, [cand])); continue
            if jkey in ja_full:                         # 日本語半分は正しい・英語半分が違う
                unknown.append((b, [ja_full[jkey]])); continue
            unknown.append((b, [])); continue            # 両方 DB に無い（カード自体が未収録 or 創作）
        if key in ja_full:                             # 《日本語名》だけ → 完成形へ（同じ粒度の英語と組む）
            ja_name = ja_full[key]
            en_name = ja_en.get(ja_name)
            if en_name:
                new = _sub_bracket(fixed, b, _pair(ja_name, en_name))
                n_fix += (new != fixed); fixed = new
            continue
        if b in en_ja:
            ja = en_ja[b]
            if ja:                                     # 《英語名》→《日本語名/英語名》（面の名前ならその面の対）
                new = _sub_bracket(fixed, b, _pair(ja, b))
                n_fix += (new != fixed); fixed = new
            continue                                   # 日本語版なしの英語名はそのまま
        # DB に無い: 近い正式名を候補として添える（添えられた英語名があればそれが第一候補）
        cands = []
        e = en_after.get(b)
        if e and e in en_ja:
            cands.append(en_ja[e] or _noja(e))
        # 閾値 0.35（0.25 から上げた）。実測: 「カード名」→「カー砦」0.286 という
        # ひな形への誤候補が出ていた一方、正当な誤字は「氷巻きの偵察」→「水巻きの偵察」0.400・
        # 「太陽の指輪」→「太陽の指環」0.500 で、その間に線が引ける。候補が消えても
        # 「未確認」の報告自体は残る＝答案側は search_mtg_cards で引ける（取り逃しは無害）。
        # 候補の照会は 1 件ずつ similarity 走査を掛けるので、未確認が多い答案では
        # 直列に積み上がる（20 件なら 20 回）。verify は答えを出す前に必ず通す道具なので、
        # 送信全体を待たせないよう先頭 5 件までに絞る（残りは名前の列挙だけで十分直せる）。
        if len(unknown) >= 5:
            unknown.append((b, cands)); continue
        cands += [c[0] for c in _db(
            "SELECT japanese_name FROM mtg_cards_v2 WHERE japanese_name IS NOT NULL"
            " AND similarity(japanese_name, %s) > 0.35"
            " ORDER BY similarity(japanese_name, %s) DESC LIMIT 3", (b, b)) if c[0] not in cands]
        unknown.append((b, cands))
    # (2) 裸の英語名（日本語名あり）→《日本語名》（英語名）。《》の中・括弧の中は触らない
    # 日本語版なしの英語名（Volcanic Island 等）は正しい表記なので保護域に入れる（中の Island を拾わない）
    noja_in = [e for e, j in en_ja.items() if not j and (len(e) >= 5 or " " in e) and e in fixed]
    prot_src = r"《[^《》]*》|[（(][^）)]*[）)]|\*[^*\n]+\*"
    if noja_in:
        prot_src += "|" + "|".join(re.escape(e) for e in sorted(noja_in, key=len, reverse=True))
    protected = re.compile(prot_src)
    def _outside(pattern, repl, s):
        out, pos = [], 0
        for m in protected.finditer(s):
            out.append(pattern.sub(repl, s[pos:m.start()])); out.append(m.group(0)); pos = m.end()
        out.append(pattern.sub(repl, s[pos:]))
        return "".join(out)
    # (3) 裸のカード名（日本語・英語とも）は **報告だけ**。書き換えない。
    #
    # 経緯: もとは「4 文字以上でストップリストに無い」日本語名 31,011 語を無条件に置換していた。
    # カード名と同形の一般語が漢字 4〜5 字だけで 1,169 語あり、「決定的瞬間」「環境科学者」が
    # 完成形に化けた＝**存在しないカードへの言及を答案に注入する**事故。そこで同じ日に
    # 「答案が《》で言及済みの名前だけ直す」へ絞ったが、別モデルのレビューで**それでも壊れる**
    # ことが分かった——日本語には単語の区切りが無いので、《島/Island》を書いた答案では
    # 「島国」の「島」まで置換される。英語側も単語境界はあるが一般語と同形のカード名
    # （Consider・Island 等）で同型の事故が起きる（「Consider this option.」が壊れた）。
    #
    # 「その語がカード名として使われているか」は機械では決められない。掟の
    # 「誤発動＝有害・取り逃し＝無害」「誤発動ゼロを試験で縫えないものは入れない」に従い、
    # **裸の名前には一切触れず、見つけたことだけを知らせる**。答案側は誤りなら無視でき、
    # 本物なら完成形に直せる。《》の中の置換（宣言がある）は従来どおり続ける。
    bare = []                       # (裸で出ていた語, 完成形) の対。日本語・英語とも
    for j in N["ja_bare"]:
        if j not in fixed:
            continue
        if _outside(re.compile(re.escape(j)), "\x00", fixed) != fixed:   # 保護域（《》・括弧・斜体）の外にある
            e = ja_en.get(j)
            bare.append((j, _pair(j, e) if e else f"《{j}》"))
    for e in N["en_bare"]:
        if e not in fixed:
            continue
        pat = re.compile(r"(?<![A-Za-z])" + re.escape(e) + r"(?![A-Za-z])")
        if _outside(pat, "\x00", fixed) != fixed:
            bare.append((e, _pair(en_ja[e], e)))
    # 完成形《ja/en》の直後に重複の（en）が残っていれば削る
    fixed = re.sub(r"(《[^》/]+/([^》]+)》)\s*[（(]\2[）)]", r"\1", fixed)
    # 機械修正の途中で二重に囲んだ箇所（《《X》》）が生じていれば戻す（入口の畳みで残った分の保険）
    fixed = re.sub(r"《《([^《》]+)》》", r"《\1》", fixed)
    lines = []
    if unknown:
        lines.append(f"未確認の名前 {len(unknown)} 件（DB のどのカード名にも一致しない＝自分で訳した/誤字/略記の疑い。"
                     "search_mtg_cards で引いて正式名に直してから答えること。略称・省略は冗長でも禁止）:")
        for b, c in unknown:
            lines.append(f"  - 《{b}》 → 候補: " + ("／".join(c) if c else "（近い名前なし・日本語版なしなら英語名のまま）"))
    else:
        lines.append("未確認の名前: なし（《》の中身はすべて DB の正式名）。")
    if bare:
        lines.append(f"裸のカード名 {len(bare)} 件（《》に入っていないが DB のカード名と同じ語。カードを指すなら"
                     "完成形《日本語名/英語名》に書き直すこと。カードでなく普通の語として書いたなら無視してよい"
                     "＝一般語と同じ形のカード名があるので機械では直さない）:")
        for w, disp in sorted(dict(bare).items(), key=lambda kv: len(kv[0]), reverse=True)[:10]:
            lines.append(f"  - 「{w}」 → {disp}")
    if n_collapse:
        lines.append(f"二重の囲み《《…》》を {n_collapse} 箇所 1 重に畳んでから照合した（畳んだ上で完成形に直す）。")
    lines.append(f"機械修正 {n_fix} 箇所（《英語名》・《日本語名》→ 完成形《日本語名/英語名》。《》に入っていない裸の名前は直さず上で知らせるだけ）。")
    # 数値の幻覚ガード（claude.ai のドラフト補助で 3 マナを 2 マナと言った事故）。修正版の文字列は変えない
    lines += _mana_check(fixed, N.get("cost", {}))
    lines.append("---- 修正版（未確認ゼロならこのまま使う） ----")
    lines.append(fixed)
    return "\n".join(lines)

"""bench_jaformat.py — 日本語の問いに《日本語名》で答えたかのベンチ（2026-08-22）。

目的: MCP の instructions（カード名は利用者の言語・《》で囲む・英語は初出に一度）だけで、クライアントが
日本語の問いに《日本語名》で答えるかを、モデル × effort の常設編成（Sonnet medium／Sonnet xhigh／Opus low）
で数字にする。名前忠実度ベンチ（bench_names.py）と同じ器＝ `claude -p` headless・MCP 有りのみ・
組み込み道具は全切り・cwd はリポジトリ外・再開可能。外部課金なし（Claude の枠）。

問いは素の日本語の質問だけ（書式の指示は付けない）。採点は DB の名前表（mtg_cards_v2）で機械裏取り:
  bracket_ok   《》の中身が japanese_name に一致（日本語版なしカードは英語名でも可）
  bracket_bad  《》の中身が DB のどの名前とも一致しない＝誤訳・捏造・表記ゆれ（明細に出す）
  bare_en      《》の外に残った英語カード名（《》直後の括弧＝初出添え、*斜体*と「」＝アーキタイプの呼び名は除外）
  bare_ja      《》の外に裸で書かれた日本語カード名（4 文字以上・ゲーム用語と同字面のものは除外）
  pass         《》が 1 個以上・bracket_bad=0・bare_en=0・bare_ja=0
機械採点は保守的な近似（bare_* は表記揺れを拾えない・短名は見ない）。答案原文は JSON に残るので人の目で補う。

使い方:
  PYTHONPATH=src /mnt/new_hdd/my_rag_env/bin/python src/bench_jaformat.py \
      --lineup sonnet:medium,sonnet:xhigh,opus:low \
      --questions docs/me/bench/jaformat_questions.csv \
      --out docs/me/bench/jaformat_20260822 --parallel 3
  途中で止まっても同じコマンドで再開（完了済みの回答 JSON は飛ばす）。--score-only で採点だけ。
"""
import argparse, csv, json, os, re, subprocess, collections, statistics
from concurrent.futures import ThreadPoolExecutor, as_completed

MCP_ON = {"mcpServers": {"mtg-rag": {
    "command": "/mnt/new_hdd/my_rag_env/bin/python",
    "args": [os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")],   # 自分と同じ src/
    "env": {"PYTHONPATH": "/home/claude/pylibs"}}}}

BUILTIN_TOOLS = ("Bash,Read,Edit,Write,MultiEdit,Glob,Grep,LS,WebSearch,WebFetch,Task,Agent,"
                 "NotebookEdit,NotebookRead,TodoWrite,TodoRead,Skill,KillShell,BashOutput")
RUN_CWD = os.environ.get("BENCH_CWD", "/tmp/claude-1003/-mnt-mtg-rag/bench_cwd")


# ─── 名前表（DB） ─────────────────────────────────────────────────────

def _norm(s):
    return re.sub(r"\s+", "", s).replace("／", "//")


def load_names():
    """戻り: (ja_set, en_set, en_with_ja_set) — 両面・分割は全体名と各面の両方を登録"""
    import psycopg2
    from db_config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG, connect_timeout=5)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT archetype FROM deck_list WHERE archetype IS NOT NULL AND archetype <> ''")
    global ARCHETYPES
    ARCHETYPES = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT card_name, japanese_name FROM mtg_cards_v2")
    ja, en, en_has_ja = set(), set(), set()
    for cn, jn in cur.fetchall():
        parts_en = [cn] + [p.strip() for p in cn.split("//")] if "//" in cn else [cn]
        for p in parts_en:
            en.add(p)
            if jn:
                en_has_ja.add(p)
        if jn:
            parts_ja = [jn] + [p.strip() for p in jn.split("//")] if "//" in jn else [jn]
            for p in parts_ja:
                ja.add(_norm(p))
    conn.close()
    return ja, en, en_has_ja


# ─── 採点 ───────────────────────────────────────────────────────────

ARCHETYPES: set = set()   # load_names で DB から読む（《Amulet Titan》のようなアーキタイプ名の《》入れは別勘定）
BRACKET_RE = re.compile(r"《([^》]+)》")
# 《X》の直後の括弧（初出の英語添え）。全角・半角両方・間に太字の ** や空白が挟まってもよい
# 《》と括弧の間に太字の **・}}・採用数「34」などが最大 12 字挟まっても初出添えと見なす（1 便目の雑音対策）
AFTER_PAREN_RE = re.compile(r"《[^》]+》[^《（(\n]{0,12}[（(][^）)]*[）)]")
BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
# *斜体*（アーキタイプ名）とその直後の括弧、「…」で括った呼び名（アーキタイプの引用）は裸名の対象外
ITALIC_RE = re.compile(r"\*[^*\n]+\*[\s]*(?:[（(][^）)]*[）)])?")
QUOTE_RE = re.compile(r"「[^」\n]{1,40}」")
# 裸日本語名の除外: ゲーム用語と同じ字面のカード名（煙試験で「生け贄」「フラッシュバック」を拾った）
JA_STOP = {"ショック", "巻き添え", "レベルアップ", "ナズグル",   # 1 便目: ショックランド・普通の語・能力語・部族名
           "フラッシュバック", "トランプル", "生け贄", "破壊不能", "打ち消し", "呪禁", "瞬速", "警戒", "飛行",
           "速攻", "接死", "絆魂", "威迫", "到達", "護法", "占術", "変身", "追放", "死亡", "召集", "探査", "続唱",
           "親和", "奇跡", "反復", "予見", "待機", "変容", "超過", "明滅", "接合", "倍増", "転生", "消術",
           "キッカー", "サイクリング", "マッドネス", "モーフ", "プロテクション", "マナ・クリーチャー"}


def score(text, ja, en, en_has_ja):
    brackets = [b.strip() for b in BRACKET_RE.findall(text)]
    ok, bad, arch, en_in, altered = [], [], [], [], []
    for b in brackets:
        nb = _norm(b)
        if "/" in b and " // " not in b:                       # 完成形《日本語名/英語名》（取り決め）
            jp, ep = b.split("/", 1)
            if _norm(jp) in ja and ep.strip() in en:
                ok.append(b); continue
            if ep.strip() in en_has_ja:                          # 英語半分は DB のカード・日本語半分が違う＝DB 由来名の改変（採点の物差し）
                altered.append(b); continue
        if nb in ja or (b in en and b not in en_has_ja):
            ok.append(b)
        elif b in en_has_ja:
            en_in.append(b)        # 日本語名があるのに英語名を《》に入れた（名前は正しい・書式の逸脱）
        elif b in ARCHETYPES:
            arch.append(b)
        elif any((len(n) >= 5 or " " in n) and n in b for n in en_has_ja):
            altered.append(b)      # 《》の中に DB の英語名が入っているのに完成形でない（「/」でなく「、」で繋ぐ等）＝改変（指摘を受けて追加・「/」の有無で逃がさない）
        else:
            bad.append(b)          # DB のどの名前でもない＝創作訳・略記・誤字
    # 《》の外を見る: 《》本体・初出括弧・斜体を消す
    rest = BOLD_RE.sub(r"\1", text)          # 太字記号だけ外す（斜体の判定を壊さないため・先に）
    rest = AFTER_PAREN_RE.sub(" ", rest)
    rest = BRACKET_RE.sub(" ", rest)
    rest = ITALIC_RE.sub(" ", rest)
    rest = QUOTE_RE.sub(" ", rest)
    # 日本語版なしのカードは英語のままが正解＝その名前を先に消す（「Volcanic Island」の中の Island を拾わない）
    for n in sorted((x for x in en if x not in en_has_ja and (len(x) >= 5 or " " in x)), key=len, reverse=True):
        if n in rest:
            rest = rest.replace(n, " ")
    bare_en = sorted({n for n in en_has_ja
                      if (len(n) >= 5 or " " in n) and n in rest     # 先に素の包含で絞る（3 万件の正規表現を毎回組まない）
                      and re.search(r"(?<![A-Za-z])" + re.escape(n) + r"(?![A-Za-z])", rest)})
    # 裸の日本語名: 4 文字以上。rest の空白を潰して探す
    rest_j = re.sub(r"\s+", "", rest)
    bare_ja = sorted({n for n in ja if len(n) >= 4 and n not in JA_STOP and n in rest_j})
    # 長い名前に含まれる短い名前（例: 「稲妻」⊂「稲妻の一撃」）の重複は長い方だけ残す
    bare_ja = [n for n in bare_ja if not any(n != m and n in m for m in bare_ja)]
    bare_en = [n for n in bare_en if not any(n != m and n in m for m in bare_en)]
    return {"n_bracket": len(brackets), "bracket_ok": len(ok), "bracket_bad": bad, "bracket_arch": arch, "bracket_en": en_in,
            "altered": altered,
            "bare_en": bare_en, "bare_ja": bare_ja,
            "pass": bool(brackets) and not bad and not altered and not en_in and not bare_en and not bare_ja}


# ─── 実行 ───────────────────────────────────────────────────────────

def run_one(out_dir, model, effort, qid, question, cfg_on):
    out = os.path.join(out_dir, f"q{qid}_{model}_{effort}.json")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    cmd = ["claude", "-p", question, "--model", model, "--effort", effort,
           "--output-format", "json", "--strict-mcp-config",
           "--disallowedTools", BUILTIN_TOOLS,
           "--mcp-config", cfg_on, "--allowedTools", "mcp__mtg-rag__*"]
    # Stop フック等の器の設定を挿すとき: BENCH_SETTINGS=<settings.json のパス> を環境変数で渡す
    if os.environ.get("BENCH_SETTINGS"):
        cmd += ["--settings", os.environ["BENCH_SETTINGS"]]
    os.makedirs(RUN_CWD, exist_ok=True)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=RUN_CWD)
        if r.returncode == 0 and r.stdout.strip().startswith("{"):
            with open(out + ".tmp", "w", encoding="utf-8") as f:
                f.write(r.stdout)
            os.replace(out + ".tmp", out)
        else:
            print(f"  失敗 q{qid} {model}/{effort}: rc={r.returncode} {r.stderr[-200:]}", flush=True)
    except subprocess.TimeoutExpired:
        print(f"  タイムアウト q{qid} {model}/{effort}", flush=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lineup", default="sonnet:medium,sonnet:xhigh,opus:low")
    ap.add_argument("--questions", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--score-only", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    a.out = os.path.abspath(a.out)
    cfg_on = os.path.join(a.out, "mcp_on.json")
    json.dump(MCP_ON, open(cfg_on, "w"))
    qs = list(csv.DictReader(open(a.questions, encoding="utf-8")))
    lineup = [tuple(x.split(":")) for x in a.lineup.split(",")]

    if not a.score_only:
        jobs = [(m, e, q["id"], q["question"]) for m, e in lineup for q in qs]
        done = 0
        with ThreadPoolExecutor(max_workers=a.parallel) as ex:
            futs = [ex.submit(run_one, a.out, m, e, qid, qt, cfg_on) for m, e, qid, qt in jobs]
            for _ in as_completed(futs):
                done += 1
                print(f"  …{done}/{len(jobs)}", flush=True)

    ja, en, en_has_ja = load_names()
    rows, detail = [], []
    for m, e in lineup:
        agg = collections.Counter()
        meta = []
        for q in qs:
            p = os.path.join(a.out, f"q{q['id']}_{m}_{e}.json")
            try:
                d = json.load(open(p, encoding="utf-8"))
            except Exception:
                agg["missing"] += 1
                continue
            text = d.get("result") or ""
            s = score(text, ja, en, en_has_ja)
            agg["pass"] += s["pass"]
            agg["n_bracket"] += s["n_bracket"]
            agg["bracket_ok"] += s["bracket_ok"]
            agg["bracket_bad"] += len(s["bracket_bad"])
            agg["bare_en"] += len(s["bare_en"])
            agg["bare_ja"] += len(s["bare_ja"])
            agg["no_bracket"] += (s["n_bracket"] == 0)
            agg["no_bad"] += (not s["bracket_bad"] and not s["altered"])
            agg["no_alter"] += (not s["altered"])
            agg["altered"] += len(s["altered"])
            agg["arch"] += len(s["bracket_arch"])
            agg["en_in"] += len(s["bracket_en"])
            meta.append((d.get("num_turns"), d.get("duration_api_ms", 0) / 1000))
            detail.append((m, e, q["id"], s, d.get("num_turns"), round(d.get("duration_api_ms", 0) / 1000)))
        n = len(qs)
        tm = statistics.median([x[0] for x in meta]) if meta else "-"
        sm = round(statistics.median([x[1] for x in meta]), 1) if meta else "-"
        rows.append(f"| {m} | {e} | {agg['pass']}/{n} | **{agg['no_alter']}/{n}** | {agg['no_bad']}/{n} | {agg['bracket_ok']}/{agg['n_bracket']} | {agg['altered']} | {agg['bracket_bad']} | {agg['en_in']} | {agg['arch']} | "
                    f"{agg['bare_en']} | {agg['bare_ja']} | {agg['no_bracket']} | {agg['missing']} | {tm} | {sm} |")
    lines = [f"# 《日本語名》書式ベンチ — {len(qs)} 問・{os.path.basename(a.out)}", "",
             "pass=《》1個以上・《》の中身が全部 DB 一致・《》外に裸の英語名/日本語名なし。**改変ゼロ答案=DB から引いた名前（英語半分が DB に一致）を一字も変えていない（採点の物差し）**。創作訳ゼロ答案=改変もなく DB に無い名前もない（アーキタイプ名は別勘定）。機械採点は近似（答案原文は q*.json）。", "",
             "| model | effort | pass | **改変ゼロ答案** | 創作訳ゼロ答案 | 《》DB一致/《》総数 | 改変（英語半分はDB・日本語半分が違う） | 《》不一致（DBに無い名） | 《》内英語名（日本語名あり） | 《》内アーキタイプ名 | 裸英語名 | 裸日本語名 | 《》ゼロ答案 | 欠落 | ターン中央値 | 秒中央値 |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"] + rows
    lines += ["", "## 明細（pass 以外）", ""]
    for m, e, qid, s, turns, sec in detail:
        if not s["pass"]:
            lines.append(f"- [{m}/{e}] q{qid} 《》{s['bracket_ok']}/{s['n_bracket']} | 改変={s['altered']} | 不一致={s['bracket_bad']} | 《》内英語={s['bracket_en']} | "
                         f"裸英={s['bare_en']} | 裸日={s['bare_ja']} | turns={turns} {sec}s")
    summary = "\n".join(lines)
    open(os.path.join(a.out, "summary.md"), "w", encoding="utf-8").write(summary + "\n")
    with open(os.path.join(a.out, "detail.tsv"), "w", encoding="utf-8") as f:
        for m, e, qid, s, turns, sec in detail:
            f.write("\t".join(str(x) for x in (m, e, qid, s["pass"], s["n_bracket"], s["bracket_ok"],
                                               "|".join(s["bracket_bad"]), "|".join(s["bare_en"]),
                                               "|".join(s["bare_ja"]), turns, sec)) + "\n")
    print(summary)


if __name__ == "__main__":
    main()

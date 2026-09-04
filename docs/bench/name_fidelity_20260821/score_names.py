"""名前忠実度の煙試験・採点（2026-08-21）。
条件 off=Claude(Sonnet) 単体・on=同＋mtg-rag MCP。正解は DB の japanese_name（NULL=日本語版なし）。
判定: correct / wrong（捏造・別名）/ unknown（不明と答えた）/ noja_wrong（日本語版ありなのに「なし」）/ parse_fail。
"""
import csv, json, re, sys, collections
W = "/tmp/claude-1003/-mnt-mtg-rag/25008f6c-85b1-4617-98c4-9498c6ec23e0/scratchpad"

def norm(s):
    if s is None: return None
    s = s.strip().strip("「」『』\"' 。.")
    s = re.sub(r"\s+", "", s)
    s = s.replace("／", "//").replace("//", "//")
    s = re.sub(r"（[ぁ-ん]+）", "", s)
    return s

def parse(result):
    if not result: return None
    m = re.search(r"答え[:：]\s*(.+)", result)
    ans = m.group(1) if m else result.strip().splitlines()[-1]
    return ans.strip()

qs = list(csv.DictReader(open(f"{W}/name_questions.csv", encoding="utf-8")))
rows = []
for i, q in enumerate(qs):
    truth = q["japanese_name"] or None
    for cond in ("off", "on"):
        try:
            d = json.load(open(f"{W}/name_runs/{cond}_{i}.json", encoding="utf-8"))
        except Exception:
            rows.append((q["cat"], q["card_name"], truth, cond, None, "missing", 0)); continue
        ans = parse(d.get("result"))
        turns = d.get("num_turns")
        a = norm(ans) if ans else None
        if a is None:
            verdict = "parse_fail"
        elif "不明" in a or "わかりません" in a or "分かりません" in a:
            verdict = "unknown"
        elif truth is None:
            verdict = "correct" if "日本語版なし" in a or "日本語版は存在しない" in a else "wrong"
        else:
            t = norm(truth)
            if a == t or a == t.split("//")[0]:
                verdict = "correct"
            elif "日本語版なし" in a:
                verdict = "noja_wrong"
            else:
                verdict = "wrong"
        rows.append((q["cat"], q["card_name"], truth, cond, ans, verdict, turns))

# 集計
tab = collections.defaultdict(collections.Counter)
for cat, name, truth, cond, ans, v, turns in rows:
    tab[(cat, cond)][v] += 1
    tab[("ALL", cond)][v] += 1
cats = ["A_no_ja", "B_hob", "C_multi", "D_famous", "E_nonliteral", "ALL"]
print("| カテゴリ | 条件 | 正解 | 捏造/誤り | 不明 | 「なし」誤判定 | 欠落 |")
print("|---|---|---|---|---|---|---|")
for c in cats:
    for cond in ("off", "on"):
        t = tab[(c, cond)]
        print(f"| {c} | {cond} | {t['correct']} | {t['wrong']} | {t['unknown']} | {t['noja_wrong']} | {t['parse_fail']+t['missing']} |")
print()
print("### 明細（誤り・不明のみ）")
for cat, name, truth, cond, ans, v, turns in rows:
    if v != "correct":
        print(f"- [{cond}] {cat} {name} | 正={truth or '日本語版なし'} | 答={ans} | {v} | turns={turns}")
with open(f"{W}/name_results.tsv", "w", encoding="utf-8") as f:
    for r in rows: f.write("\t".join("" if x is None else str(x) for x in r) + "\n")

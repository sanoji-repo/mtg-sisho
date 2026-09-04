"""bench_names.py — 名前忠実度ベンチ（モデル × effort × MCP 有無 の格子・2026-08-21）。

目的: 利用者に「この MCP はどのモデル・どの effort で使うと何%」を数字で書くため、
同じ問題集を `claude -p`（headless）で回して機械採点する。外部課金なし（Claude の枠）。

条件:
  on  = mtg-rag MCP（stdio）を挿す。--allowedTools mcp__mtg-rag__*
  off = MCP 無し。--disallowedTools WebSearch,WebFetch で既定 Web 道具も切る（記憶のみに純化）
正解は DB 由来の問題集 CSV（cat, card_name, japanese_name・NULL=日本語版なし）。

使い方:
  python src/bench_names.py --model opus --efforts low,medium,high,xhigh,max --conds on,off \
      --questions docs/me/name_fidelity_20260821/name_questions.csv \
      --out docs/me/bench/names_opus_20260821 --parallel 4
  途中で止まっても同じコマンドで再開（完了済みの回答 JSON は飛ばす）。
"""
import argparse, csv, json, os, re, subprocess, sys, collections, statistics
from concurrent.futures import ThreadPoolExecutor, as_completed

MCP_ON = {"mcpServers": {"mtg-rag": {
    "command": "/mnt/new_hdd/my_rag_env/bin/python",
    "args": ["/mnt/mtg_rag/src/mcp_server.py"],
    "env": {"PYTHONPATH": "/home/claude/pylibs"}}}}
MCP_OFF = {"mcpServers": {}}

PROMPT = ("次の Magic: The Gathering のカードの日本語の正式名（日本語版カードに印刷されている名前）を"
          "答えてください。日本語版が存在しないカードなら「日本語版なし」と答えてください。"
          "分からない場合は「不明」と答えてください。出力は 1 行だけ・形式は「答え: <名前>」。\n"
          "カード: {card}")


def norm(s):
    if s is None:
        return None
    s = s.strip().strip("「」『』\"' 。.")
    s = re.sub(r"\s+", "", s).replace("／", "//")
    return re.sub(r"（[ぁ-ん]+）", "", s)


def parse(result):
    if not result:
        return None
    m = re.search(r"答え[:：]\s*(.+)", result)
    return (m.group(1) if m else result.strip().splitlines()[-1]).strip()


def judge(truth, ans):
    a = norm(ans) if ans else None
    if a is None:
        return "parse_fail"
    if "不明" in a or "分かりません" in a or "わかりません" in a:
        return "unknown"
    if truth is None:
        return "correct" if ("日本語版なし" in a or "日本語版は存在しない" in a) else "wrong"
    t = norm(truth)
    if a == t or a == t.split("//")[0]:
        return "correct"
    return "noja_wrong" if "日本語版なし" in a else "wrong"


# 既定の組み込み道具は両条件とも切る（2026-08-21 実測: リポジトリ内 cwd だと単体条件が Bash/Read で
# DB やファイルを覗き 6 ターンになった＝「記憶のみ」にならない）。MCP 有りの道具は MCP だけ。
BUILTIN_TOOLS = ("Bash,Read,Edit,Write,MultiEdit,Glob,Grep,LS,WebSearch,WebFetch,Task,Agent,"
                 "NotebookEdit,NotebookRead,TodoWrite,TodoRead,Skill,KillShell,BashOutput")
# cwd はリポジトリ外（CLAUDE.md・フック・.mcp.json を拾わせない）
RUN_CWD = os.environ.get("BENCH_CWD", "/tmp/claude-1003/-mnt-mtg-rag/25008f6c-85b1-4617-98c4-9498c6ec23e0/scratchpad/bench_cwd")


def run_one(out_dir, model, effort, cond, idx, card, cfg_on, cfg_off):
    out = os.path.join(out_dir, f"{effort}_{cond}_{idx}.json")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    cmd = ["claude", "-p", PROMPT.format(card=card), "--model", model, "--effort", effort,
           "--output-format", "json", "--strict-mcp-config",
           "--disallowedTools", BUILTIN_TOOLS]
    if cond == "on":
        cmd += ["--mcp-config", cfg_on, "--allowedTools", "mcp__mtg-rag__*"]
    else:
        cmd += ["--mcp-config", cfg_off]
    os.makedirs(RUN_CWD, exist_ok=True)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=RUN_CWD)
        if r.returncode == 0 and r.stdout.strip().startswith("{"):
            with open(out + ".tmp", "w", encoding="utf-8") as f:
                f.write(r.stdout)
            os.replace(out + ".tmp", out)
    except subprocess.TimeoutExpired:
        pass
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="opus")
    ap.add_argument("--efforts", default="low,medium,high,xhigh,max")
    ap.add_argument("--conds", default="on,off")
    ap.add_argument("--questions", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--score-only", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    a.out = os.path.abspath(a.out)          # cwd を変えて claude を呼ぶので絶対パスに
    cfg_on = os.path.join(a.out, "mcp_on.json")
    cfg_off = os.path.join(a.out, "mcp_off.json")
    json.dump(MCP_ON, open(cfg_on, "w"))
    json.dump(MCP_OFF, open(cfg_off, "w"))
    qs = list(csv.DictReader(open(a.questions, encoding="utf-8")))
    efforts = a.efforts.split(",")
    conds = a.conds.split(",")

    if not a.score_only:
        jobs = [(e, c, i, q["card_name"]) for e in efforts for c in conds for i, q in enumerate(qs)]
        done = 0
        with ThreadPoolExecutor(max_workers=a.parallel) as ex:
            futs = [ex.submit(run_one, a.out, a.model, e, c, i, card, cfg_on, cfg_off)
                    for e, c, i, card in jobs]
            for _ in as_completed(futs):
                done += 1
                if done % 20 == 0:
                    print(f"  …{done}/{len(jobs)}", flush=True)

    # 採点
    tab = collections.defaultdict(collections.Counter)
    meta = collections.defaultdict(list)
    detail = []
    for e in efforts:
        for c in conds:
            for i, q in enumerate(qs):
                truth = q["japanese_name"] or None
                p = os.path.join(a.out, f"{e}_{c}_{i}.json")
                try:
                    d = json.load(open(p, encoding="utf-8"))
                except Exception:
                    tab[(e, c)]["missing"] += 1
                    continue
                ans = parse(d.get("result"))
                v = judge(truth, ans)
                tab[(e, c)][v] += 1
                tab[(e, c, q["cat"])][v] += 1
                meta[(e, c)].append((d.get("num_turns"), d.get("duration_api_ms", 0) / 1000,
                                     (d.get("usage") or {}).get("output_tokens_details", {}).get("thinking_tokens", 0)))
                detail.append((e, c, q["cat"], q["card_name"], truth, ans, v, d.get("num_turns")))
    n = len(qs)
    lines = [f"# 名前忠実度ベンチ — model={a.model}・{n} 問・{os.path.basename(a.out)}", "",
             "| effort | 条件 | 正解 | 捏造/誤り | 不明 | 「なし」誤判定 | 欠落 | ターン中央値 | 秒中央値 | 思考tok中央値 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for e in efforts:
        for c in conds:
            t = tab[(e, c)]
            m = meta[(e, c)]
            tm = statistics.median([x[0] for x in m]) if m else "-"
            sm = round(statistics.median([x[1] for x in m]), 1) if m else "-"
            km = statistics.median([x[2] for x in m]) if m else "-"
            lines.append(f"| {e} | {c} | {t['correct']} ({round(100*t['correct']/n)}%) | {t['wrong']} | {t['unknown']} | "
                         f"{t['noja_wrong']} | {t['missing']+t['parse_fail']} | {tm} | {sm} | {km} |")
    cats = sorted({q["cat"] for q in qs})
    lines += ["", "## カテゴリ別 正解数", "", "| effort | 条件 | " + " | ".join(cats) + " |",
              "|---|---|" + "---|" * len(cats)]
    for e in efforts:
        for c in conds:
            lines.append(f"| {e} | {c} | " + " | ".join(str(tab[(e, c, k)]["correct"]) for k in cats) + " |")
    lines += ["", "## 明細（正解以外）", ""]
    for e, c, cat, name, truth, ans, v, turns in detail:
        if v != "correct":
            lines.append(f"- [{e}/{c}] {cat} {name} | 正={truth or '日本語版なし'} | 答={ans} | {v} | turns={turns}")
    summary = "\n".join(lines)
    open(os.path.join(a.out, "summary.md"), "w", encoding="utf-8").write(summary + "\n")
    with open(os.path.join(a.out, "detail.tsv"), "w", encoding="utf-8") as f:
        for r in detail:
            f.write("\t".join("" if x is None else str(x) for x in r) + "\n")
    print(summary)


if __name__ == "__main__":
    main()

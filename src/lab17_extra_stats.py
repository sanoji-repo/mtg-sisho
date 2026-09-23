#!/usr/bin/env python
"""lab17_extra_stats.py — 17Lands Public Datasets（CC BY 4.0）の「落としていた列」を集計して入れる。

lab17_trial.py はカード × セットの表（limited_card_stats）だけを作り、game_data の main_colors／opp_colors／rank／on_play／
num_turns／num_mulligans と draft_data の pick_maindeck_rate／event_match_wins 等を読み捨てていた。ドラフトの助言に
要る軸（色の相性・ランク帯・取った後にメインへ入る率）がこれで欠けていた。→ 生の行は DB に入れず（封筒裏: ピック 2 億行 × 手持ち ≒ 数十億行）、**集計 5 表**だけを足す。生 gz は手元（--dir）にあるので再取得なし。

表（すべて (expansion, event_type, …) 鍵で UPSERT・冪等）:
  limited_color_stats     セット × デッキの色（main_colors）× splash 有無: games, wins, wr
  limited_matchup_stats   セット × 自分の色 × 相手の色（opp_colors）: games, wins, wr
  limited_format_stats    セット × ランク帯: games, wins, on_play_games, on_play_wins, turns_sum, mulligans_sum（先手勝率・平均ターン・マリガン率は SQL で割る）
  limited_card_rank_stats セット × ランク帯 × カード: gih_games, gih_wins, gp_games, gp_wins（ランク帯別の GIH WR・母数が痩せるので games 列を見る）
  limited_card_pick_stats セット × カード: picks, maindeck_rate（取ったカードがメインに入った率の平均）, sideboard_in_rate, event_wins_sum, event_losses_sum
                          （取った人のドラフト成績の合計＝「このカードを取った人は平均何勝したか」）
rank は帯だけに揃える（bronze…mythic・小文字・古いセットの 'Platinum-4-0-0-0' は先頭の帯・欠けは 'none'）。wr は生成列（wins/games）。
db_card_name は limited_card_stats（同セット・同 card_name）から写す。

使い方: python src/lab17_extra_stats.py --set ECL [--event PremierDraft] [--dir data/17lands] [--no-db] [--migrate]
"""
from __future__ import annotations
import argparse, datetime as dt, os, sys, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

# 置き場所はリポジトリ直下から解く（作者の開発環境の絶対パスを使わない）。
# 生の gz は data/17lands（.gitignore 済み）・報告は logs/（同じく）。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
L17_DIR = os.environ.get("L17_DIR", os.path.join(_REPO_ROOT, "data", "17lands"))


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def game_extra(path: str, chunksize: int = 20000) -> dict[str, pd.DataFrame]:
    header = pd.read_csv(path, nrows=0).columns
    cards = sorted({c[len("deck_"):] for c in header if c.startswith("deck_")})
    oh = [f"opening_hand_{c}" for c in cards]; dr = [f"drawn_{c}" for c in cards]; dk = [f"deck_{c}" for c in cards]
    meta = ["rank", "main_colors", "splash_colors", "opp_colors", "on_play", "num_mulligans", "num_turns", "won"]
    # 古いセット（STX 等）の game_data には main_colors／splash_colors が無い＝色表・相性表はそのセットだけ作らない（欠けは表に出さない）
    has_colors = "main_colors" in header
    if not has_colors:
        meta = [m for m in meta if m not in ("main_colors", "splash_colors")]
        log("  main_colors 列なし（古い書式）＝ color／matchup は作らない")
    dtype = {c: "float32" for c in oh + dr + dk}
    color_acc: dict = {}; match_acc: dict = {}; fmt_acc: dict = {}
    rank_acc: dict[str, dict[str, np.ndarray]] = {}
    n = 0; t0 = time.time()
    for i, ch in enumerate(pd.read_csv(path, usecols=meta + oh + dr + dk, dtype=dtype, chunksize=chunksize)):
        n += len(ch)
        won = ch["won"].astype(str).str.lower().eq("true").to_numpy()
        # rank は新しいセットが 'platinum'、古いセット（AFR 等）が 'Platinum-4-0-0-0'（帯-段-…）＝先頭の帯だけ小文字で揃える。欠けは 'none'
        rank = ch["rank"].fillna("none").astype(str).str.split("-").str[0].str.lower().to_numpy()
        mc = ch["main_colors"].fillna("").astype(str).to_numpy() if has_colors else np.full(len(ch), "", dtype=object)
        splash = (ch["splash_colors"].fillna("").astype(str).str.len().gt(0).to_numpy() if has_colors
                  else np.zeros(len(ch), dtype=bool))
        oc = ch["opp_colors"].fillna("").astype(str).to_numpy()
        on_play = ch["on_play"].astype(str).str.lower().eq("true").to_numpy()
        turns = pd.to_numeric(ch["num_turns"], errors="coerce").fillna(0).to_numpy()
        mull = pd.to_numeric(ch["num_mulligans"], errors="coerce").fillna(0).to_numpy()
        # 色・相性・ランク帯: pandas の groupby で一括
        g = pd.DataFrame({"mc": mc, "sp": splash, "oc": oc, "rk": rank, "won": won, "op": on_play,
                          "opw": on_play & won, "tn": turns, "ml": mull})
        for (a, b), s in g.groupby(["mc", "sp"])["won"].agg(["count", "sum"]).iterrows():
            c = color_acc.setdefault((a, bool(b)), [0, 0]); c[0] += int(s["count"]); c[1] += int(s["sum"])
        for (a, b), s in g.groupby(["mc", "oc"])["won"].agg(["count", "sum"]).iterrows():
            c = match_acc.setdefault((a, b), [0, 0]); c[0] += int(s["count"]); c[1] += int(s["sum"])
        for r, s in g.groupby("rk").agg(games=("won", "count"), wins=("won", "sum"), opg=("op", "sum"),
                                         opw=("opw", "sum"), tn=("tn", "sum"), ml=("ml", "sum")).iterrows():
            c = fmt_acc.setdefault(r, [0] * 6)
            for k, v in enumerate(s.to_numpy()):
                c[k] += int(v)
        # ランク帯 × カード
        OH = ch[oh].to_numpy() > 0; DR = ch[dr].to_numpy() > 0; DK = ch[dk].to_numpy() > 0
        GIH = OH | DR
        for r in np.unique(rank):
            m = rank == r
            acc = rank_acc.setdefault(r, {k: np.zeros(len(cards), dtype=np.int64)
                                          for k in ("gih_games", "gih_wins", "gp_games", "gp_wins")})
            w = won[m][:, None]
            acc["gih_games"] += GIH[m].sum(0); acc["gih_wins"] += (GIH[m] & w).sum(0)
            acc["gp_games"] += DK[m].sum(0); acc["gp_wins"] += (DK[m] & w).sum(0)
        if i % 10 == 0:
            log(f"  game_data {n:,} 行 {time.time()-t0:.0f}s")
    log(f"game_data: {n:,} ゲーム・{time.time()-t0:.0f} 秒")
    color = pd.DataFrame([(a, b, g, w) for (a, b), (g, w) in color_acc.items()] if has_colors else [],
                         columns=["main_colors", "splash", "games", "wins"])
    match = pd.DataFrame([(a, b, g, w) for (a, b), (g, w) in match_acc.items()] if has_colors else [],
                         columns=["main_colors", "opp_colors", "games", "wins"])
    fmt = pd.DataFrame([(r, *v) for r, v in fmt_acc.items()],
                       columns=["rank", "games", "wins", "on_play_games", "on_play_wins", "turns_sum", "mulligans_sum"])
    rows = []
    for r, acc in rank_acc.items():
        for j, c in enumerate(cards):
            if acc["gp_games"][j] or acc["gih_games"][j]:
                rows.append((r, c, int(acc["gih_games"][j]), int(acc["gih_wins"][j]), int(acc["gp_games"][j]), int(acc["gp_wins"][j])))
    card_rank = pd.DataFrame(rows, columns=["rank", "card_name", "gih_games", "gih_wins", "gp_games", "gp_wins"])
    return {"color": color, "match": match, "fmt": fmt, "card_rank": card_rank}


def draft_extra(path: str, chunksize: int = 200000) -> pd.DataFrame:
    usecols = ["draft_id", "pick", "pick_maindeck_rate", "pick_sideboard_in_rate", "event_match_wins", "event_match_losses"]
    acc: dict[str, list] = {}
    n = 0; t0 = time.time()
    for ch in pd.read_csv(path, usecols=usecols, chunksize=chunksize):
        n += len(ch)
        ch = ch.dropna(subset=["pick"])
        md = pd.to_numeric(ch["pick_maindeck_rate"], errors="coerce")
        sb = pd.to_numeric(ch["pick_sideboard_in_rate"], errors="coerce")
        ew = pd.to_numeric(ch["event_match_wins"], errors="coerce")
        el = pd.to_numeric(ch["event_match_losses"], errors="coerce")
        g = pd.DataFrame({"pick": ch["pick"].to_numpy(), "md": md.to_numpy(), "mdn": md.notna().to_numpy(),
                          "sb": sb.to_numpy(), "ew": ew.fillna(0).to_numpy(), "el": el.fillna(0).to_numpy(),
                          "en": (ew.notna() & el.notna()).to_numpy()})
        s = g.groupby("pick").agg(picks=("pick", "count"), md=("md", "sum"), mdn=("mdn", "sum"), sb=("sb", "sum"),
                                   ew=("ew", "sum"), el=("el", "sum"), en=("en", "sum"))
        for name, r in s.iterrows():
            c = acc.setdefault(name, [0, 0.0, 0, 0.0, 0, 0, 0])
            c[0] += int(r["picks"]); c[1] += float(r["md"]); c[2] += int(r["mdn"]); c[3] += float(r["sb"])
            c[4] += int(r["ew"]); c[5] += int(r["el"]); c[6] += int(r["en"])
    log(f"draft_data: {n:,} ピック・{time.time()-t0:.0f} 秒")
    rows = []
    for name, (p, md, mdn, sb, ew, el, en) in acc.items():
        rows.append((name, p, round(md / mdn, 4) if mdn else None, round(sb / mdn, 4) if mdn else None,
                     ew if en else None, el if en else None, en))
    return pd.DataFrame(rows, columns=["card_name", "picks", "maindeck_rate", "sideboard_in_rate",
                                       "event_wins_sum", "event_losses_sum", "event_picks"])


DDL = """
CREATE TABLE IF NOT EXISTS public.limited_color_stats (
  expansion text NOT NULL, event_type text NOT NULL, main_colors text NOT NULL, splash boolean NOT NULL,
  games integer NOT NULL, wins integer NOT NULL,
  wr numeric(6,4) GENERATED ALWAYS AS (round(wins::numeric / nullif(games, 0), 4)) STORED,
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (expansion, event_type, main_colors, splash));
CREATE TABLE IF NOT EXISTS public.limited_matchup_stats (
  expansion text NOT NULL, event_type text NOT NULL, main_colors text NOT NULL, opp_colors text NOT NULL,
  games integer NOT NULL, wins integer NOT NULL,
  wr numeric(6,4) GENERATED ALWAYS AS (round(wins::numeric / nullif(games, 0), 4)) STORED,
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (expansion, event_type, main_colors, opp_colors));
CREATE TABLE IF NOT EXISTS public.limited_format_stats (
  expansion text NOT NULL, event_type text NOT NULL, rank text NOT NULL,
  games integer NOT NULL, wins integer NOT NULL, on_play_games integer NOT NULL, on_play_wins integer NOT NULL,
  turns_sum bigint NOT NULL, mulligans_sum bigint NOT NULL,
  wr numeric(6,4) GENERATED ALWAYS AS (round(wins::numeric / nullif(games, 0), 4)) STORED,
  on_play_wr numeric(6,4) GENERATED ALWAYS AS (round(on_play_wins::numeric / nullif(on_play_games, 0), 4)) STORED,
  avg_turns numeric(6,2) GENERATED ALWAYS AS (round(turns_sum::numeric / nullif(games, 0), 2)) STORED,
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (expansion, event_type, rank));
CREATE TABLE IF NOT EXISTS public.limited_card_rank_stats (
  expansion text NOT NULL, event_type text NOT NULL, rank text NOT NULL, card_name text NOT NULL,
  gih_games integer NOT NULL, gih_wins integer NOT NULL, gp_games integer NOT NULL, gp_wins integer NOT NULL,
  gih_wr numeric(6,4) GENERATED ALWAYS AS (round(gih_wins::numeric / nullif(gih_games, 0), 4)) STORED,
  gp_wr numeric(6,4) GENERATED ALWAYS AS (round(gp_wins::numeric / nullif(gp_games, 0), 4)) STORED,
  db_card_name text, computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (expansion, event_type, rank, card_name));
CREATE TABLE IF NOT EXISTS public.limited_card_pick_stats (
  expansion text NOT NULL, event_type text NOT NULL, card_name text NOT NULL,
  picks integer NOT NULL, maindeck_rate numeric(6,4), sideboard_in_rate numeric(6,4),
  event_wins_sum integer, event_losses_sum integer, event_picks integer NOT NULL,
  avg_event_wins numeric(6,3) GENERATED ALWAYS AS (round(event_wins_sum::numeric / nullif(event_picks, 0), 3)) STORED,
  db_card_name text, computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (expansion, event_type, card_name));
"""


def upsert(conn, table: str, keys: list[str], cols: list[str], df: pd.DataFrame, exp: str, ev: str) -> int:
    from psycopg2.extras import execute_values
    allc = ["expansion", "event_type"] + cols
    vals = [(exp, ev, *[None if (isinstance(v, float) and np.isnan(v)) else (v.item() if hasattr(v, "item") else v) for v in r])
            for r in df[cols].itertuples(index=False)]
    upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in keys) + ", computed_at = now()"
    with conn.cursor() as cur:
        execute_values(cur, f"INSERT INTO public.{table} ({', '.join(allc)}) VALUES %s"
                            f" ON CONFLICT ({', '.join(['expansion', 'event_type'] + keys)}) DO UPDATE SET {upd}", vals)
        cur.execute(f"DELETE FROM public.{table} WHERE expansion = %s AND event_type = %s AND computed_at < now() - interval '1 minute'", (exp, ev))
    return len(vals)


_TABLES = ("limited_color_stats", "limited_matchup_stats", "limited_format_stats",
           "limited_card_rank_stats", "limited_card_pick_stats")


def _verify_schema(conn, migrate: bool) -> None:
    """通常運転では DDL を打たず、5 表が在ることだけ確かめる。

    CREATE TABLE IF NOT EXISTS も空振りでも対象表の ACCESS EXCLUSIVE を要求するので、
    読みの後ろに並ぶと玉突きになる。作るのは --migrate のときだけ・lock_timeout つき。
    """
    from db_config import ddl_cursor, read_cursor
    if migrate:
        with ddl_cursor(conn) as cur:
            cur.execute(DDL)
        return
    with read_cursor(conn) as cur:
        cur.execute("SELECT table_name FROM information_schema.tables"
                    " WHERE table_schema = 'public' AND table_name = ANY(%s)", (list(_TABLES),))
        have = {r[0] for r in cur.fetchall()}
    miss = [t for t in _TABLES if t not in have]
    if miss:
        raise SystemExit("public に表がありません: " + ", ".join(miss) +
                         "\n  初回は --migrate を付けて実行してください（5 表を作ります）。")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True); ap.add_argument("--event", default="PremierDraft")
    ap.add_argument("--dir", default=L17_DIR); ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--migrate", action="store_true",
                    help="5 表を作る（通常運転では DDL を打たない）")
    a = ap.parse_args()
    exp, ev = a.set, a.event
    gpath = os.path.join(a.dir, f"game_data_public.{exp}.{ev}.csv.gz")
    dpath = os.path.join(a.dir, f"draft_data_public.{exp}.{ev}.csv.gz")
    for p in (gpath, dpath):
        if not os.path.exists(p):
            log(f"無い: {p}"); return 2
    log(f"=== {exp} {ev} ===")
    g = game_extra(gpath)
    d = draft_extra(dpath)
    log(f"color {len(g['color'])} 行・matchup {len(g['match'])} 行・format {len(g['fmt'])} 行・card_rank {len(g['card_rank'])} 行・card_pick {len(d)} 行")
    if a.no_db:
        print(g["color"].sort_values("games", ascending=False).head(12).to_string(index=False)); return 0
    import psycopg2
    from db_config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)
    _verify_schema(conn, a.migrate)
    n1 = upsert(conn, "limited_color_stats", ["main_colors", "splash"], ["main_colors", "splash", "games", "wins"], g["color"], exp, ev)
    n2 = upsert(conn, "limited_matchup_stats", ["main_colors", "opp_colors"], ["main_colors", "opp_colors", "games", "wins"], g["match"], exp, ev)
    n3 = upsert(conn, "limited_format_stats", ["rank"], ["rank", "games", "wins", "on_play_games", "on_play_wins", "turns_sum", "mulligans_sum"], g["fmt"], exp, ev)
    n4 = upsert(conn, "limited_card_rank_stats", ["rank", "card_name"], ["rank", "card_name", "gih_games", "gih_wins", "gp_games", "gp_wins"], g["card_rank"], exp, ev)
    n5 = upsert(conn, "limited_card_pick_stats", ["card_name"],
                ["card_name", "picks", "maindeck_rate", "sideboard_in_rate", "event_wins_sum", "event_losses_sum", "event_picks"], d, exp, ev)
    with conn.cursor() as cur:   # db_card_name は limited_card_stats（同セット・同名）から写す
        for t in ("limited_card_rank_stats", "limited_card_pick_stats"):
            cur.execute(f"UPDATE public.{t} x SET db_card_name = l.db_card_name FROM public.limited_card_stats l"
                        f" WHERE l.expansion = x.expansion AND l.event_type = x.event_type AND l.card_name = x.card_name"
                        f" AND x.expansion = %s AND x.event_type = %s AND x.db_card_name IS DISTINCT FROM l.db_card_name", (exp, ev))
    conn.commit(); conn.close()
    log(f"DB: color {n1}・matchup {n2}・format {n3}・card_rank {n4}・card_pick {n5} 行 UPSERT")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""lab17_trial.py — 17Lands Public Datasets（CC BY 4.0）1 セット分の試算。

方針（docs/DATA_SOURCES.md の 17Lands の節）: 横長の生データ（612 万行 × 646 列）を DB に持たず、gz を流し読みして
**カードごとの集計だけ**を作る。目的は (1) 17lands が出している数字（GIH WR・ALSA・ATA）を自分で再現できるか、
(2) カード名が mtg_cards_v2.card_name と一致するか、の二つ。生データを DB に入れるかは、この結果を見てから決める。

定義（17lands の用語・帰属: 17Lands https://www.17lands.com/ ・Public Datasets）:
  GIH WR  Games In Hand Win Rate  = 手札に来た（opening_hand + drawn > 0）ゲームの勝率。tutored は数えない（17lands の定義に合わせる）
  OH WR   Opening Hand Win Rate   = 初手にあったゲームの勝率
  GD WR   Games Drawn Win Rate    = 引いた（drawn > 0・初手を除く）ゲームの勝率
  GP WR   Games Played Win Rate   = メインデッキに入っていた（deck > 0）ゲームの勝率
  ALSA    Average Last Seen At    = パックの中で「最後に見えたピック番号」の平均（1 始まり）。小さいほど早く消える
  ATA     Average Taken At        = 取られたピック番号の平均（1 始まり）
  pick_number は 0 始まりなので +1 して 1 始まりに揃える。

使い方: python src/lab17_trial.py --set LCI --event PremierDraft [--dir data/17lands] [--no-db] [--migrate] [--out 道]
出力: public.limited_card_stats（冪等= (expansion, event_type, card_name) で UPSERT）と logs/17lands_trial_YYYYMMDD.md
生の gz は事前に置いておく（取得は sh/lab17_import_sets.sh が S3 の公開ファイルから行う）。
表と列を作るのは --migrate のときだけ（通常運転では DDL を打たない）。
"""
from __future__ import annotations
import argparse, gzip, os, sys, time, datetime as dt
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from db_config import ddl_cursor, missing_columns   # noqa: E402（置き場所を足した後に読む）

# 置き場所はリポジトリ直下から解く（作者の開発環境の絶対パスを使わない）。
# 生の gz は data/17lands（.gitignore 済み）・報告は logs/（同じく）。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
L17_DIR = os.environ.get("L17_DIR", os.path.join(_REPO_ROOT, "data", "17lands"))

GAME_PREFIXES = ("opening_hand_", "drawn_", "tutored_", "deck_", "sideboard_")

# 表と列の正本（作るのは --migrate のときだけ・通常運転は在るかどうかだけ確かめる）。
# 列の並びは INSERT の順番でもある（cols として使う）。
_REQUIRED_COLUMNS = ("expansion", "event_type", "card_name",
                     "gih_games", "gih_wins", "gih_wr", "oh_games", "oh_wins", "oh_wr",
                     "gd_games", "gd_wins", "gd_wr", "gp_games", "gp_wins", "gp_wr",
                     "seen_packs", "alsa", "taken_count", "ata",
                     "in_cards_v2", "db_card_name", "match_kind")
_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS public.limited_card_stats (
  expansion text NOT NULL, event_type text NOT NULL, card_name text NOT NULL,
  gih_games int, gih_wins int, gih_wr numeric(6,4),
  oh_games int, oh_wins int, oh_wr numeric(6,4),
  gd_games int, gd_wins int, gd_wr numeric(6,4),
  gp_games int, gp_wins int, gp_wr numeric(6,4),
  seen_packs int, alsa numeric(6,2), taken_count int, ata numeric(6,2),
  in_cards_v2 boolean, computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (expansion, event_type, card_name));
ALTER TABLE public.limited_card_stats
  ADD COLUMN IF NOT EXISTS db_card_name text, ADD COLUMN IF NOT EXISTS match_kind text;
GRANT SELECT ON public.limited_card_stats TO readonly_ai;
"""



def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def game_stats(path: str, chunksize: int = 20000) -> pd.DataFrame:
    """game_data → カードごとの GIH/OH/GD/GP のゲーム数と勝ち数。"""
    header = pd.read_csv(path, nrows=0).columns
    cards = sorted({c[len("deck_"):] for c in header if c.startswith("deck_")})
    oh = [f"opening_hand_{c}" for c in cards]; dr = [f"drawn_{c}" for c in cards]; dk = [f"deck_{c}" for c in cards]
    usecols = ["won"] + oh + dr + dk
    dtype = {c: "float32" for c in oh + dr + dk}  # 古いセット（STX 等）は NA 混じり＝int だと読めない。NaN>0=False＝不在扱い
    acc = {k: np.zeros(len(cards), dtype=np.int64) for k in
           ("gih_games", "gih_wins", "oh_games", "oh_wins", "gd_games", "gd_wins", "gp_games", "gp_wins")}
    n_games = 0; n_wins = 0; t0 = time.time()
    for i, ch in enumerate(pd.read_csv(path, usecols=usecols, dtype=dtype, chunksize=chunksize)):
        won = ch["won"].astype(str).str.lower().eq("true").to_numpy()
        OH = ch[oh].to_numpy() > 0; DR = ch[dr].to_numpy() > 0; DK = ch[dk].to_numpy() > 0
        GIH = OH | DR
        acc["gih_games"] += GIH.sum(0); acc["gih_wins"] += (GIH & won[:, None]).sum(0)
        acc["oh_games"] += OH.sum(0);   acc["oh_wins"] += (OH & won[:, None]).sum(0)
        GD = DR & ~OH
        acc["gd_games"] += GD.sum(0);   acc["gd_wins"] += (GD & won[:, None]).sum(0)
        acc["gp_games"] += DK.sum(0);   acc["gp_wins"] += (DK & won[:, None]).sum(0)
        n_games += len(ch); n_wins += int(won.sum())
        if i % 5 == 0:
            log(f"  game_data chunk {i}: {n_games:,} games ({time.time()-t0:.0f}s)")
    df = pd.DataFrame({"card_name": cards, **acc})
    df.attrs["n_games"] = n_games; df.attrs["n_wins"] = n_wins
    return df


def draft_stats(path: str, chunksize: int = 50000) -> pd.DataFrame:
    """draft_data → ALSA（パックごとの最後に見えたピックの平均）と ATA（取られたピックの平均）。
    パックはファイル中で連続している前提（draft_id, pack_number の順）。チャンク境界で割れたパックは持ち越して合流する。"""
    header = pd.read_csv(path, nrows=0).columns
    cards = sorted({c[len("pack_card_"):] for c in header if c.startswith("pack_card_")})
    pc = [f"pack_card_{c}" for c in cards]
    usecols = ["draft_id", "pack_number", "pick_number", "pick"] + pc
    dtype = {c: "float32" for c in pc}  # 同上（NA 対策）
    idx = {c: i for i, c in enumerate(cards)}
    sum_last = np.zeros(len(cards), dtype=np.int64); n_seen = np.zeros(len(cards), dtype=np.int64)
    sum_taken = np.zeros(len(cards), dtype=np.int64); n_taken = np.zeros(len(cards), dtype=np.int64)
    carry_key = None; carry_vec = None          # 境界で割れた最後のパック
    n_rows = 0; n_packs = 0; t0 = time.time(); unknown_picks = 0
    for i, ch in enumerate(pd.read_csv(path, usecols=usecols, dtype=dtype, chunksize=chunksize)):
        n_rows += len(ch)
        # ATA
        pn1 = ch["pick_number"].to_numpy(dtype="float64") + 1
        for name, s in pd.Series(pn1).groupby(ch["pick"].to_numpy()).agg(["sum", "count"]).iterrows():
            j = idx.get(name)
            if j is None:
                unknown_picks += int(s["count"]); continue
            if pd.isna(s["sum"]):
                continue
            sum_taken[j] += int(s["sum"]); n_taken[j] += int(s["count"])
        # ALSA: last seen pick per (draft, pack)
        seen = ch[pc].to_numpy() > 0
        last = np.nan_to_num(seen * pn1[:, None]).astype(np.int64)    # 見えてれば pick 番号、見えてなければ 0（NA も 0＝見えてない扱い・int64 に戻す＝sum_last への += の型を守る）
        key = (ch["draft_id"].astype(str) + "#" + ch["pack_number"].astype(str)).to_numpy()
        # 同じキーが連続する前提で、キーの切れ目ごとに max を取る
        change = np.r_[True, key[1:] != key[:-1]]
        starts = np.flatnonzero(change); ends = np.r_[starts[1:], len(key)]
        for s, e in zip(starts, ends):
            vec = last[s:e].max(0)
            k = key[s]
            if s == 0:
                if carry_key == k:                                    # 前チャンクの尻尾と合流
                    vec = np.maximum(vec, carry_vec)
                elif carry_vec is not None:
                    # 境界がちょうどパックの切れ目に当たった場合＝持ち越した尻尾は
                    # もう続きが来ないので、ここで確定させる。これを忘れると境界ごとに
                    # 1 パック黙って消えた（レビューの指摘・合成 CSV で再現:
                    # 同じ 4 行をチャンク 4 と 2 で読むとパック 2 → 1・ALSA 1.5 → 2.0）
                    sum_last += carry_vec; n_seen += (carry_vec > 0); n_packs += 1
                carry_key = carry_vec = None
            if e == len(key):                                         # このチャンクの尻尾＝次に持ち越す
                carry_key, carry_vec = k, vec
                continue
            sum_last += vec; n_seen += (vec > 0); n_packs += 1
        if i % 10 == 0:
            log(f"  draft_data chunk {i}: {n_rows:,} picks / {n_packs:,} packs ({time.time()-t0:.0f}s)")
    if carry_vec is not None:                                          # 最後のパック
        sum_last += carry_vec; n_seen += (carry_vec > 0); n_packs += 1
    df = pd.DataFrame({"card_name": cards, "seen_packs": n_seen, "alsa_sum": sum_last,
                       "taken_count": n_taken, "ata_sum": sum_taken})
    df.attrs["n_rows"] = n_rows; df.attrs["n_packs"] = n_packs; df.attrs["unknown_picks"] = unknown_picks
    return df


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True); ap.add_argument("--event", default="PremierDraft")
    ap.add_argument("--dir", default=L17_DIR); ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--min-games", type=int, default=500)
    ap.add_argument("--migrate", action="store_true",
                    help="表と列を作る（通常運転では DDL を打たない）")
    ap.add_argument("--out", default=None, help="報告の書き出し先（既定は logs/17lands_trial_<日付>.md）")
    a = ap.parse_args()
    gpath = f"{a.dir}/game_data_public.{a.set}.{a.event}.csv.gz"
    dpath = f"{a.dir}/draft_data_public.{a.set}.{a.event}.csv.gz"
    t0 = time.time()
    log(f"game_data: {gpath} ({os.path.getsize(gpath)/1e6:.0f}MB gz)")
    g = game_stats(gpath)
    log(f"game_data 完了: {g.attrs['n_games']:,} games・全体勝率 {g.attrs['n_wins']/max(g.attrs['n_games'],1):.3f}・カード {len(g)}（{time.time()-t0:.0f}s）")
    t1 = time.time()
    log(f"draft_data: {dpath} ({os.path.getsize(dpath)/1e6:.0f}MB gz)")
    d = draft_stats(dpath)
    log(f"draft_data 完了: {d.attrs['n_rows']:,} picks・{d.attrs['n_packs']:,} packs・pick 名が列に無い {d.attrs['unknown_picks']}（{time.time()-t1:.0f}s）")

    df = g.merge(d, on="card_name", how="outer")
    for k in ("gih", "oh", "gd", "gp"):
        df[f"{k}_wr"] = (df[f"{k}_wins"] / df[f"{k}_games"].where(df[f"{k}_games"] > 0)).round(4)
    df["alsa"] = (df["alsa_sum"] / df["seen_packs"].where(df["seen_packs"] > 0)).round(2)
    df["ata"] = (df["ata_sum"] / df["taken_count"].where(df["taken_count"] > 0)).round(2)
    df.insert(0, "event_type", a.event); df.insert(0, "expansion", a.set)

    # 名前一致（mtg_cards_v2.card_name）
    matched = None
    if not a.no_db:
        import psycopg2
        from db_config import DB_CONFIG
        conn = psycopg2.connect(**DB_CONFIG)
        # 名前照合: 完全一致 → 表面名（両面カードは mtg_cards_v2 が「表 // 裏」・17lands は表面だけ）
        with conn.cursor() as cur:
            cur.execute("SELECT card_name, split_part(card_name, ' // ', 1) FROM mtg_cards_v2")
            rows_ = cur.fetchall()
            names = {r[0] for r in rows_}
            front = {}
            for full, fr in rows_:
                front.setdefault(fr, full)
            cur.execute("SELECT card_name FROM mtg_cards_v2_nonlegal")
            names_nl = {r[0] for r in cur.fetchall()}
        # 17lands の CSV ヘッダは非 ASCII を 1 文字 "?" に潰すことがある（TMT: "Bespoke B?" ← Bespoke Bō・zcat で実確認）。
        # "?" を「非 ASCII 1 文字」として正式名（完全名・表面名）に当て、一意に決まるときだけ採る（誤帰属より取り逃し）。
        import re
        cand_pairs = [(n_, n_) for n_ in names] + list(front.items())
        def resolve(n: str) -> tuple[str | None, str]:
            if n in names: return n, "exact"
            if n in front: return front[n], "front"
            if "?" in n:
                pat = re.compile("^" + re.escape(n).replace(r"\?", r"[^\x00-\x7F]") + "$")
                hits = sorted({full for cand, full in cand_pairs if pat.match(cand)})
                if len(hits) == 1: return hits[0], "mojibake"
            return None, "none"
        res = df["card_name"].map(resolve)
        df["db_card_name"] = res.map(lambda t: t[0]); df["match_kind"] = res.map(lambda t: t[1])
        df["in_cards_v2"] = df["match_kind"].ne("none")
        df["in_nonlegal"] = df["card_name"].isin(names_nl)
        matched = int(df["in_cards_v2"].sum()); n_front = int(df["match_kind"].eq("front").sum())
        n_moji = int(df["match_kind"].eq("mojibake").sum())
        # 書き込み（冪等）
        # 通常運転では DDL を打たない（空振りでも ACCESS EXCLUSIVE を要求し、読みの
        # 後ろに並ぶと玉突きになる）。作るのは --migrate のときだけで、
        # そのときも lock_timeout つき（行列の先頭で粘らない）。
        if a.migrate:
            with ddl_cursor(conn) as cur:
                cur.execute(_SCHEMA_DDL)
        else:
            miss = missing_columns(conn, "limited_card_stats", _REQUIRED_COLUMNS)
            if miss:
                raise SystemExit(
                    "public.limited_card_stats に必要な列がありません: " + ", ".join(miss) +
                    "\n  初回は --migrate を付けて実行してください（表と列を作ります）。")
        with conn.cursor() as cur:
            cols = list(_REQUIRED_COLUMNS)
            rows = [tuple(None if (isinstance(v, float) and np.isnan(v)) else (int(v) if isinstance(v, (np.integer,)) else v)
                          for v in r) for r in df[cols].itertuples(index=False, name=None)]
            cur.executemany(f"""
                INSERT INTO public.limited_card_stats ({', '.join(cols)}) VALUES ({', '.join(['%s']*len(cols))})
                ON CONFLICT (expansion, event_type, card_name) DO UPDATE SET
                  {', '.join(f'{c}=EXCLUDED.{c}' for c in cols[3:])}, computed_at=now()""", rows)
        conn.commit(); conn.close()
        log(f"public.limited_card_stats へ {len(rows)} 行 UPSERT・名前一致 {matched}/{len(df)}（うち表面名で一致 {n_front}・文字化け直し {n_moji}）")

    # 報告（生の数字を残す）
    stamp = dt.date.today().strftime("%Y%m%d")
    # 器（sh/lab17_import_sets.sh）とファイル名でやり取りすると、日付を双方で
    # 別々に求めるので午前 0 時に食い違う。明示的に受け取れるようにした
    out = a.out or os.path.join(os.environ.get("L17_REPORT_DIR", os.path.join(_REPO_ROOT, "logs")),
                                f"17lands_trial_{stamp}.md")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    top = df[df["gih_games"] >= a.min_games].sort_values("gih_wr", ascending=False)
    early = df[df["seen_packs"] >= 200].sort_values("alsa")
    def tbl(x: pd.DataFrame, cols: list[str], n: int = 20) -> str:
        h = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
        return h + "\n".join("| " + " | ".join(str(v) for v in r) + " |" for r in x[cols].head(n).itertuples(index=False, name=None))
    unmatched = df.loc[~df.get("in_cards_v2", pd.Series(False, index=df.index)), "card_name"].tolist() if matched is not None else []
    body = f"""# 17lands 試算 {a.set} {a.event}（{dt.datetime.now():%Y-%m-%d %H:%M}・src/lab17_trial.py）

帰属: データは 17Lands（https://www.17lands.com/）の Public Datasets（CC BY 4.0）。生 CSV は同梱しない・集計のみ。

## 規模と所要
- game_data: {g.attrs['n_games']:,} ゲーム・全体勝率 {g.attrs['n_wins']/max(g.attrs['n_games'],1):.3f}・カード列 {len(g)}・{t1-t0:.0f} 秒
- draft_data: {d.attrs['n_rows']:,} ピック・{d.attrs['n_packs']:,} パック・{time.time()-t1:.0f} 秒（pick 名が列に無い行 {d.attrs['unknown_picks']}）
- 名前一致（mtg_cards_v2.card_name・完全一致 → 表面名 → "?" 化けの一意復元）: {matched if matched is not None else 'DB 未接続'}/{len(df)}（表面名で一致＝両面カード {n_front if matched is not None else '-'}・文字化け直し {n_moji if matched is not None else '-'}）

## GIH WR 上位 20（手札に来たゲーム {a.min_games} 以上）
{tbl(top, ['card_name','gih_games','gih_wr','oh_wr','gd_wr','gp_wr','alsa','ata'])}

## ALSA が小さい順 20（早く消えるカード・見えたパック 200 以上）
{tbl(early, ['card_name','seen_packs','alsa','ata','gih_wr'])}

## 名前が mtg_cards_v2 に無いカード（{len(unmatched)}）
{', '.join(unmatched) if unmatched else '（なし）'}

## 突き合わせの手順（17lands のサイトの数字と）
https://www.17lands.com/card_data?expansion={a.set}&format={a.event} を開き、上位数枚の GIH WR・ALSA・ATA を目で読んで上の表と比べる（API は叩かない掟）。
17lands 側は既定で「直近の期間」や「ユーザー勝率の帯」で絞れるので、絞り無し（全期間・全ユーザー）に合わせる。

## 再現
`python src/lab17_trial.py --set {a.set} --event {a.event}` → public.limited_card_stats に UPSERT。
SQL 例: `SELECT card_name, gih_games, gih_wr, alsa, ata FROM public.limited_card_stats WHERE expansion='{a.set}' AND gih_games>=500 ORDER BY gih_wr DESC LIMIT 20;`
"""
    with open(out, "w") as f:
        f.write(body)
    log(f"報告: {out}（合計 {time.time()-t0:.0f} 秒）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

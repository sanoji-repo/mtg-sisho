"""
daily_set_watch.py — Scryfall・17lands の毎日監視
============================================================================
発売日の登録を待たず、毎日これだけやる:

  1. Scryfall 同期（無条件・毎日）: oracle_cards bulk（jsonl.gz・24MB 級）を取得して
     sync_oracle_cards.py --apply（冪等・変更行だけ UPDATE）。新セット・アルケミーの
     突然の追加・禁止改定・オラクル訂正が全部翌朝には入る。
  2. /sets 報告（1 コール）: 45 日以内に発売が来るセットの接近と card_count、
     21 日以内に発売済みセットの日本語名の欠けを報告。
  3. 17lands 見張り: 候補セット（expansion 等・発売 -7〜+90 日）の PremierDraft ファイルを
     S3 に HEAD。未取り込みで現れていたら sh/17lands_premier_sets.tsv に行を足して
     sh/lab17_import_sets.sh <CODE> で初回の取り込み（DB に居るセットはスクリプト側が skip＝冪等）。
     取り込み済みセットのファイル更新（シーズン中は成長する）は報告だけ＝再取り込みは手で判断。

ログ: logs/set_watch.log（このファイル自身の観測）。夜間ドライバから呼ぶときは標準出力も別ログへ渡す。
使い方: python src/daily_set_watch.py [--dry-run]（--dry-run は同期と取り込みをしない）
"""

import argparse
import gzip
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATE = REPO / "state" / "daily_set_watch_state.json"
TSV17 = REPO / "sh" / "17lands_premier_sets.tsv"
WATCH_LOG = REPO / "logs" / "set_watch.log"
CACHE = Path.home() / ".cache" / "mtg_sisho"
PYTHON = sys.executable
UA = "mtg-sisho set-watch/1.0 (educational project; contact via GitHub)"
S3 = "https://17lands-public.s3.amazonaws.com/analysis_data"
# 17lands にドラフトが立ち得る set_type（アルケミー・token 等は対象外）
DRAFTABLE_TYPES = {"expansion", "masters", "draft_innovation", "core"}


def http_json(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def http_head(url: str, timeout: int = 20):
    """存在すれば (Last-Modified, Content-Length)、404 なら None。"""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.headers.get("Last-Modified", ""), int(r.headers.get("Content-Length") or 0)
    except urllib.error.HTTPError as e:
        if e.code in (403, 404):  # S3 の非公開/不在は 403 も返す
            return None
        raise


def log_line(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    WATCH_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(WATCH_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def download_bulk() -> Path:
    """oracle_cards bulk（jsonl.gz のみ）→ JSON 配列に変換して返す。"""
    idx = http_json("https://api.scryfall.com/bulk-data")
    entry = next(x for x in idx["data"] if x["type"] == "oracle_cards")
    dest = CACHE / "oracle_cards_latest.json"
    CACHE.mkdir(parents=True, exist_ok=True)
    log_line(f"bulk 取得: oracle_cards updated_at={entry['updated_at']}"
             f"（圧縮 {entry.get('compressed_size', 0)/1e6:.1f} MB）")
    req = urllib.request.Request(entry["jsonl_download_uri"], headers={"User-Agent": UA})
    gz = CACHE / "oracle_cards_latest.jsonl.gz"
    tmp = gz.with_suffix(".part")
    with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.rename(gz)
    with gzip.open(gz, "rt", encoding="utf-8") as fin, open(dest, "w", encoding="utf-8") as fout:
        fout.write("[")
        for i, line in enumerate(filter(str.strip, fin)):
            if i:
                fout.write(",")
            fout.write(line.rstrip("\n"))
        fout.write("]")
    return dest


# 発売前の先行収録（sync_oracle_cards.py の --preview-days）と同じ窓・同じ種類。
PREVIEW_DAYS = 45
PREVIEW_TYPES = {"expansion", "core", "commander", "draft_innovation", "masters"}


def scryfall_ja_count(code: str) -> int | str:
    """e:<code> lang:ja の印刷数（1 コール・該当なしの 404 は 0）。失敗は文字列で返す（呼び出し元の処理は止めない）。"""
    import time
    import urllib.parse
    time.sleep(0.2)   # Scryfall の推奨間隔（50〜100ms 以上）を守る
    url = ("https://api.scryfall.com/cards/search?unique=prints&q="
           + urllib.parse.quote(f"e:{code} lang:ja"))
    try:
        return int(http_json(url).get("total_cards", 0))
    except urllib.error.HTTPError as e:
        return 0 if e.code == 404 else f"取得失敗 HTTP {e.code}"
    except Exception as e:
        return f"取得失敗 {type(e).__name__}"


def run_sync(bulk: Path) -> int:
    cmd = [PYTHON, str(REPO / "src" / "sync_oracle_cards.py"),
           "--bulk", str(bulk), "--apply", "--ids-out", str(CACHE / "sync_ids.txt"),
           "--preview-days", str(PREVIEW_DAYS)]
    rc = subprocess.run(cmd, cwd=str(REPO / "src")).returncode
    log_line(f"sync_oracle_cards 完了 rc={rc}")
    return rc


def ja_report(codes) -> None:
    import psycopg2
    sys.path.insert(0, str(REPO / "src"))
    from db_config import DB_CONFIG
    with psycopg2.connect(**DB_CONFIG) as conn, conn.cursor() as cur:
        for code in codes:
            cur.execute("SELECT count(*), count(japanese_name) FROM mtg_cards_v2 WHERE set_code = %s",
                        (code,))
            total, ja = cur.fetchone()
            if total:
                log_line(f"日本語名: {code} = {total} 枚中あり {ja}・欠け {total - ja}")


def db_17lands_sets() -> set:
    import psycopg2
    sys.path.insert(0, str(REPO / "src"))
    from db_config import DB_CONFIG
    with psycopg2.connect(**DB_CONFIG) as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT expansion FROM public.limited_card_stats "
                    "WHERE event_type = 'PremierDraft'")
        return {r[0] for r in cur.fetchall()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="観測と報告のみ（同期・取り込みをしない）")
    args = ap.parse_args()
    today = date.today()
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}

    # --- 1. Scryfall 同期（無条件） ---
    if not args.dry_run:
        try:
            run_sync(download_bulk())
        except Exception as e:
            log_line(f"Scryfall 同期失敗 {type(e).__name__}: {e}（次の夜に再挑戦）")

    # --- 2. /sets 報告 ---
    try:
        sets = http_json("https://api.scryfall.com/sets")["data"]
    except Exception as e:
        log_line(f"/sets 取得失敗 {type(e).__name__}（今夜の報告は無し）")
        sets = []
    recent_codes = []
    for x in sets:
        rel = x.get("released_at")
        if not rel:
            continue
        d = date.fromisoformat(rel)
        if today < d <= today + timedelta(days=45) and x.get("set_type") != "token":
            log_line(f"接近: {x['code']} {x['name']}（{rel}・あと {(d - today).days} 日・"
                     f"card_count {x.get('card_count', 0)}）")
            # Scryfall に日本語版の印刷が何枚載ったかを数える（報告だけ）。
            # 日本語名は発売後に extract_japanese.py を手で走らせて埋める＝載り始めた日が走らせ時。
            if x.get("set_type") in PREVIEW_TYPES:
                log_line(f"日本語版の印刷（Scryfall）: {x['code']} = {scryfall_ja_count(x['code'])} 枚")
        if today - timedelta(days=21) <= d <= today and x.get("set_type") in DRAFTABLE_TYPES | {"commander"}:
            recent_codes.append(x["code"])
    if recent_codes:
        ja_report(recent_codes)

    # --- 3. 17lands 見張り ---
    candidates = [x for x in sets
                  if x.get("released_at") and x.get("set_type") in DRAFTABLE_TYPES
                  and today - timedelta(days=90) <= date.fromisoformat(x["released_at"])
                  <= today + timedelta(days=7)]
    have = db_17lands_sets()
    st17 = state.setdefault("17lands", {})
    for x in candidates:
        code = x["code"].upper()
        gurl = f"{S3}/game_data/game_data_public.{code}.PremierDraft.csv.gz"
        durl = f"{S3}/draft_data/draft_data_public.{code}.PremierDraft.csv.gz"
        head = http_head(gurl)
        if head is None:
            continue  # まだ 17lands に無い（毎晩見てるだけ・報告は現れた夜に出る）
        lm, size = head
        prev = st17.get(code, {})
        if code in have:
            if prev.get("imported_lm") and lm != prev["imported_lm"]:
                log_line(f"17lands 更新あり: {code}（取り込み時 {prev['imported_lm']} → 現在 {lm}・"
                         f"{size/1e6:.0f} MB）＝再取り込みは手で判断")
        else:
            log_line(f"17lands に出現: {code}（{lm}・{size/1e6:.0f} MB）→ 初回の取り込み")
            if not args.dry_run:
                if not any(f"\t{gurl}" in l or l.endswith(gurl)
                           for l in TSV17.read_text(encoding="utf-8").splitlines()):
                    with open(TSV17, "a", encoding="utf-8") as f:
                        f.write(f"{x['name']}\t{lm}\t{durl}\t{gurl}\n")
                rc = subprocess.run(["bash", str(REPO / "sh" / "lab17_import_sets.sh"), code],
                                    env={**os.environ, "PYBIN": PYTHON}).returncode
                log_line(f"lab17_import_sets {code} 完了 rc={rc}")
                if rc == 0:
                    st17[code] = {"imported_lm": lm}
        st17.setdefault(code, {})["last_lm"] = lm

    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

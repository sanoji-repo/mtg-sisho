#!/usr/bin/env python
"""check_freshness.py — データ鮮度の見張り（禁止改定・ルール改定を
取りこぼさないため）。

DB に入っている各棚の版と、世の中の最新版を突き合わせて報告する。**報せるだけ**——
取り込みは自動でやらない（LLM 出力と同じで、外から来るデータは人が GO を出してから
検証つきで入れる）。夜間 cron の末尾に足して WORKLOG 的に流すのが想定運用。

見るもの:
  1. Scryfall bulk（oracle_cards）の updated_at  vs  ローカル bulk ファイルの mtime
     → legalities（禁止改定）と oracle テキストの鮮度。反映便は update_oracle.py。
  2. mtg_rules.source_version  vs  WotC 総合ルールページの最終更新（best-effort・
     取れなければ「手動確認」と出す）→ エキスパンションごとの CR 改定。
  3. card_rulings.source_version → 裁定の取得日（古くなったら import_rulings.py 再走）。

終了コード: 0=全部新鮮 / 1=要更新あり / 2=判定不能あり（ネットワーク等）
"""
import datetime
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg2
from db_config import DB_CONFIG

BULK_LOCAL = "/mnt/new_hdd/oracle_cards.json"
ALL_CARDS_LOCAL = "/mnt/new_hdd/all_cards_scryfall.json"
STALE = []
UNKNOWN = []


def report(name: str, local: str, remote: str, ok: bool | None, hint: str) -> None:
    mark = "新鮮" if ok else ("要更新" if ok is False else "判定不能")
    print(f"[{mark}] {name}: DB/ローカル={local} / 最新={remote}")
    if ok is False:
        STALE.append(name)
        print(f"        → 反映便: {hint}")
    elif ok is None:
        UNKNOWN.append(name)
        print(f"        → {hint}")


def main() -> int:
    today = datetime.date.today()

    # 1. Scryfall bulk（legalities・oracle テキストの源）
    try:
        # Scryfall API は User-Agent/Accept 必須（素の urllib は 400 になる・実測）
        req = urllib.request.Request(
            "https://api.scryfall.com/bulk-data",
            headers={"User-Agent": "mtg-rag-freshness-check/1.0",
                     "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            bulk = {b["type"]: b for b in json.load(r)["data"]}
        remote_oracle = bulk["oracle_cards"]["updated_at"][:10]
    except Exception as e:
        remote_oracle = None
        report("Scryfall bulk", "-", "-", None, f"API 到達不能: {e}")
    if remote_oracle:
        for path, label, hint in (
                (BULK_LOCAL, "oracle_cards（legalities/オラクル）",
                 "新版 DL → src/update_oracle.py（禁止改定はここで反映）"),
                (ALL_CARDS_LOCAL, "all_cards（日本語印刷/セット）",
                 "新版 DL → src/extract_japanese.py ほか enrich 群")):
            if os.path.exists(path):
                local = datetime.date.fromtimestamp(
                    os.path.getmtime(path)).isoformat()
                report(label, local, remote_oracle, local >= remote_oracle, hint)
            else:
                report(label, "ファイルなし", remote_oracle, False, hint)

    # 2〜3. DB の版
    conn = psycopg2.connect(**DB_CONFIG)
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(source_version) FROM mtg_rules")
        rules_ver = (cur.fetchone()[0] or datetime.date(1900, 1, 1))
        cur.execute("SELECT MAX(source_version) FROM card_rulings")
        rulings_ver = (cur.fetchone()[0] or datetime.date(1900, 1, 1))
    conn.close()

    # CR の最新版日付は WotC 側の構造が不安定なので自動判定しない（誤報より沈黙）。
    # 90 日超えたら「エキスパンション 1 つ分は経過＝手動確認」を促す。
    age = (today - rules_ver).days
    report("総合ルール（mtg_rules）", rules_ver.isoformat(),
           "不明（WotC ページは手動確認）",
           None if age <= 90 else False,
           f"取得から {age} 日。新セット発売時は CR 改定あり"
           "＝ magic.wizards.com/rules を確認して import 便を再走")

    age_r = (today - rulings_ver).days
    report("公式裁定（card_rulings）", rulings_ver.isoformat(), "-",
           True if age_r <= 45 else False,
           f"取得から {age_r} 日。src/import_rulings.py 再走で追いつく")

    print()
    if STALE:
        print(f"要更新 {len(STALE)} 件: {', '.join(STALE)}")
        return 1
    if UNKNOWN:
        print("判定不能あり（手動確認を推奨）")
        return 2
    print("全棚新鮮。")
    return 0


if __name__ == "__main__":
    sys.exit(main())

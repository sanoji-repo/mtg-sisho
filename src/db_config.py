"""
db_config.py — DB 接続設定の一元管理
====================================
パスワード等の機密情報は **このファイルには書かない**。
リポジトリ直下の .env（.gitignore 済み）または環境変数から読み込む。
.env の書き方は .env.example を参照。

使い方:
    # 通常（フラグファイルで Primary/Standby を自動切り替え）
    from db_config import get_db_config
    conn = psycopg2.connect(**get_db_config())

    # 単純なスクリプト（Primary 固定でよい場合）
    from db_config import DB_CONFIG
    conn = psycopg2.connect(**DB_CONFIG)

    # Standby を明示的に使う場合（例: ベンチマーク）
    from db_config import DB_CONFIG_STANDBY
"""

import os
from contextlib import contextmanager


# ─── .env の読み込み（依存ライブラリなしの簡易パーサ）─────────

def _load_dotenv(path: str = None) -> None:
    """リポジトリ直下の .env を読み、未設定の環境変数だけを補う。

    既存の環境変数は上書きしない（OS の環境変数を優先）。
    .env が無ければ何もしない（その場合は環境変数のみで動く）。
    """
    if path is None:
        # コードは src/ 配下・.env はリポジトリ直下（2026-07-24 の配置替えに追随。
        # 旧配置=同階層もフォールバックで見る＝将来また動かしても静かに壊れない）
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(os.path.dirname(here), ".env")
        if not os.path.exists(path):
            path = os.path.join(here, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


# ─── 接続設定 ─────────────────────────────────────────────────

_COMMON = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "dbname":   os.environ.get("DB_NAME", "rag_dev"),
    "user":     os.environ.get("DB_USER", "devuser"),
    "password": os.environ.get("DB_PASSWORD", ""),
}

DB_CONFIG_PRIMARY = {**_COMMON, "port": int(os.environ.get("DB_PORT", "5435"))}
DB_CONFIG_STANDBY = {**_COMMON, "port": int(os.environ.get("DB_PORT_STANDBY", "5436"))}

# reembed・共起集計等の重い更新処理中に作成するフラグファイル。
# 既定はリポジトリ直下（2026-09-05 Step 3 で作者の開発環境の絶対パス /mnt/mtg_rag/.primary_updating から置き換え）。
# この脚本は sh/ からも素で使われるので sisho 包みを import せず自前で位置を解く。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLAG_FILE = os.environ.get("DB_FLAG_FILE", os.path.join(_REPO_ROOT, ".primary_updating"))


def get_db_config() -> dict:
    """フラグファイルが存在する場合は Standby を使用する。

    reembed や共起集計等の更新処理中に自動的に Standby へ切り替わる。
    """
    if os.path.exists(FLAG_FILE):
        print(f"  [INFO] {FLAG_FILE} を検出 → Standby ({DB_CONFIG_STANDBY['port']}) を使用")
        return DB_CONFIG_STANDBY
    return DB_CONFIG_PRIMARY


# 後方互換: 既存スクリプトは DB_CONFIG を import している
DB_CONFIG = DB_CONFIG_PRIMARY


# ─── スクレイパ用の接続と読み取り（2026-09-18）─────────────────
#
# HTTP を叩いている間トランザクションを開けたままにすると、読み取りでも
# deck_list の ACCESS SHARE を握り続ける（psycopg2 は既定で自動コミット
# しないので、最初の SELECT でトランザクションが開いたままになる）。
# そこへ別のスクレイパが DDL を打つと ACCESS EXCLUSIVE 待ちになり、ロック待ちは
# 先着順なので、その後ろに来た INSERT や SELECT まで並ぶ。
# 2026-09-18 実測: 行列 2 分 10 秒・一晩の待ち合計 77.6 分（pg_blocking_pids で
# 連鎖を確認: ある取り込みの idle in transaction → 別の取り込みの ALTER →
# 三つ目の取り込みの SELECT）。
# 対策は二段。read_cursor が読みのトランザクションを必ず閉じ、
# connect_scrape が「トランザクション中に居座る」を時間で切る網を張る。

# 網の長さ。書き込みの一まとまり（1 デッキ分の INSERT 一式 → commit）は
# DB の中だけで済むので、ここに 60 秒かかる筋書きは無い＝将来また HTTP を
# トランザクションの中に入れてしまったときだけ鳴る。
SCRAPE_IDLE_IN_TX_TIMEOUT = os.environ.get("SCRAPE_IDLE_IN_TX_TIMEOUT", "60s")


def connect_scrape(**overrides):
    """スクレイパ用の接続（Primary 固定・トランザクション中の居座りを時間で切る）。

    居座りで切られた接続は次の問い合わせで psycopg2 の例外になる＝その取り込みが
    失敗して終わり、ログに残る。黙って全員を待たせるより、
    うるさく失敗する方を選ぶ（誤発動=有害・取り逃し=無害の非対称）。
    """
    import psycopg2
    cfg = {**DB_CONFIG_PRIMARY, **overrides}
    opts = str(cfg.get("options") or "")
    cfg["options"] = (
        f"{opts} -c idle_in_transaction_session_timeout={SCRAPE_IDLE_IN_TX_TIMEOUT}"
    ).strip()
    return psycopg2.connect(**cfg)


@contextmanager
def read_cursor(conn):
    """読み取り専用の問い合わせ用カーソル。抜けるときに必ずトランザクションを閉じる。

    これを通して読めば、この後どれだけ長く HTTP を叩いても deck_list の
    ロックを握ったままにならない。書き込みには使わない（rollback するので
    書いた分が消える）。
    """
    cur = conn.cursor()
    try:
        yield cur
    finally:
        cur.close()
        conn.rollback()


# ─── DDL とスキーマ確認の作法（2026-09-18）────────────────────────
#
# 掟: 通常運転では DDL を打たない。打つときは行列の先頭で粘らない。
#
# 理由。ADD COLUMN IF NOT EXISTS も CREATE INDEX IF NOT EXISTS も、結果が空振りでも
# 対象表の ACCESS EXCLUSIVE（索引は SHARE）を要求する。誰かが長いトランザクションを
# 開けていると DDL はそこで待ち、PostgreSQL のロック待ちは先着順なので、後から来た
# INSERT や SELECT まで DDL の後ろに並ぶ。2026-07-13 に一度これで渋滞し（手で打った
# ALTER が 90 分居座った読みに堰き止められ後続が玉突き）、2026-09-18 には毎晩の取り込みの中で
# 毎晩起きていたことが分かった（一晩の待ち合計 77.6 分）。7/13 の教訓は recompute の
# TRUNCATE にだけ入っていて、他の 20 本には入っていなかった。
#
# 使い方:
#   require_columns(conn, "mtg_cards_v2", ("draw_count","draw_x"), DDL_SQL,
#                   migrate=migrate_requested())     # 足りなければ止まる／--migrate で作る
#   with ddl_cursor(conn) as cur: cur.execute(...)   # DDL を打つときは必ずこれ経由

DDL_LOCK_TIMEOUT = os.environ.get("DDL_LOCK_TIMEOUT", "5s")


def migrate_requested(argv=None) -> bool:
    """コマンド行に --migrate があるか（argparse を持たない脚本でも使えるように素で見る）。"""
    import sys
    return "--migrate" in (argv if argv is not None else sys.argv[1:])


@contextmanager
def ddl_cursor(conn, timeout=None):
    """DDL 用のカーソル。lock_timeout を張って渡し、抜けるときに commit する。

    ロックが取れなければ自分が失敗する＝後ろに並んだ人を巻き込まない。失敗は
    「何が起きたか・次に何をすればよいか」を書いて止まる（黙って待たない）。
    """
    import psycopg2
    cur = conn.cursor()
    try:
        cur.execute(f"SET lock_timeout = '{timeout or DDL_LOCK_TIMEOUT}'")
        yield cur
        conn.commit()
    except psycopg2.errors.LockNotAvailable:
        conn.rollback()
        raise SystemExit(
            f"DDL のロックが {timeout or DDL_LOCK_TIMEOUT} で取れませんでした。"
            "\n  対象の表に長いトランザクションを開けている接続があります。"
            "\n  見る: bin/dbq \"SELECT pid, state, now()-xact_start AS age, left(query,60)"
            " FROM pg_stat_activity WHERE datname='rag_dev' ORDER BY xact_start\""
            "\n  待ってから、または窓を外して再実行してください（DDL_LOCK_TIMEOUT で伸ばせます）。"
        )
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            cur.execute("RESET lock_timeout")
            conn.commit()
        except Exception:
            pass
        cur.close()


def missing_columns(conn, table, columns):
    """在るべき列のうち無い物を順番どおりに返す（読むだけ・トランザクションを残さない）。"""
    with read_cursor(conn) as cur:
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = %s", (table,))
        have = {r[0] for r in cur.fetchall()}
    return [c for c in columns if c not in have]


def missing_indexes(conn, table, names):
    """在るべき索引のうち無い物を返す。"""
    with read_cursor(conn) as cur:
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = %s", (table,))
        have = {r[0] for r in cur.fetchall()}
    return [n for n in names if n not in have]


def require_columns(conn, table, columns, ddl, migrate=False, label=None):
    """通常運転では DDL を打たず、列が在ることだけ確かめる。

    足りないとき: migrate が真なら ddl を打つ（lock_timeout つき）。偽なら
    何が足りないかと直し方を言って止まる——黙って進むと、列の無いまま UPDATE が
    落ちるか別の列に書く事故になる（取り逃し=無害・誤発動=有害の非対称）。
    返り値: DDL を打ったかどうか。
    """
    missing = missing_columns(conn, table, columns)
    if not missing:
        return False
    if not migrate:
        raise SystemExit(
            f"{table} に必要な列がありません: {', '.join(missing)}"
            f"\n  列を作るのは移行の仕事です。作ってよければ --migrate を付けて再実行してください"
            f"（{label or '起動時の DDL'} を毎回打つのは 2026-09-18 にやめました）。"
        )
    with ddl_cursor(conn) as cur:
        cur.execute(ddl)
    print(f"  [移行] {table} に列を追加しました: {', '.join(missing)}", flush=True)
    return True


def require_indexes(conn, table, names, ddl, migrate=False):
    """索引は答えを変えないので、無くても止めない（--migrate のときだけ作る）。"""
    missing = missing_indexes(conn, table, names)
    if not missing:
        return False
    if not migrate:
        print(f"  [警告] {table} の索引がありません: {', '.join(missing)}"
              f"（速さだけの問題・作るなら --migrate）", flush=True)
        return False
    with ddl_cursor(conn) as cur:
        cur.execute(ddl)
    print(f"  [移行] {table} に索引を作りました: {', '.join(missing)}", flush=True)
    return True

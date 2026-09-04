"""conftest.py — pytest 共通設定とフィクスチャ（Step 1）。

DB 接続情報は db_config.py の既定および環境変数（.env）に従い、秘密情報は直接書き込まない。
"""
import os
import sys
import pytest

# src/ を sys.path の先頭に追加
_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)


def pytest_addoption(parser):
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Commander Spellbook API などの外部ネットワーク実呼び試験を実行する",
    )


def is_db_available() -> bool:
    """PostgreSQL DB へ接続可能か判定（タイムアウト 2 秒）。"""
    try:
        import psycopg2
        from db_config import get_db_config

        cfg = dict(get_db_config())
        cfg.setdefault("connect_timeout", 2)
        conn = psycopg2.connect(**cfg)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
            return True
        finally:
            conn.close()
    except Exception:
        return False


DB_AVAILABLE = is_db_available()
requires_db = pytest.mark.skipif(
    not DB_AVAILABLE,
    reason="PostgreSQL データベースに接続できないためスキップ（DB 稼働・認証が必要）",
)

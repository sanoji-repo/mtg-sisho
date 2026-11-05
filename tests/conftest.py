"""conftest.py — pytest 共通設定とフィクスチャ。

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

# MTG_REQUIRE_DB=1 のとき、DB に繋がらないことを「飛ばし」でなく「失敗」にする
# （DB の要る試験が黙って全部飛んで、緑に見えるのを防ぐ口）。既定（未設定）では飛ばす。
REQUIRE_DB = os.environ.get("MTG_REQUIRE_DB", "").strip() == "1"


@pytest.fixture
def _db_must_connect():
    pytest.fail("MTG_REQUIRE_DB=1 なのに PostgreSQL に接続できません（DB 稼働・認証を確認）", pytrace=False)


if REQUIRE_DB and not DB_AVAILABLE:
    # usefixtures の印は pytestmark にも @ にも使える。fixture の側で落とす
    requires_db = pytest.mark.usefixtures("_db_must_connect")
else:
    requires_db = pytest.mark.skipif(
        not DB_AVAILABLE,
        reason="PostgreSQL データベースに接続できないためスキップ（DB 稼働・認証が必要）",
    )


@pytest.fixture(autouse=True)
def isolate_tool_log(tmp_path, monkeypatch):
    """道具ログを試験ごとの tmp_path へ逃がす。

    試験は道具を実呼びするので、既定の出力先（リポジトリ直下 logs/mcp_tools.log）に試験由来の行が
    混ざる。sisho.toollog.TOOL_LOG は import 時に環境変数を読んで固定される
    ので、環境変数だけでなくモジュール属性そのものを差し替える。
    """
    log = tmp_path / "mcp_tools.log"
    monkeypatch.setenv("MCP_TOOL_LOG", str(log))
    import sisho.toollog
    monkeypatch.setattr(sisho.toollog, "TOOL_LOG", str(log))
    yield

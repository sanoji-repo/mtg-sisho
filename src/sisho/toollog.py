"""toollog.py — 道具の呼び出し履歴（2026-09-05 Step 2 で mcp_server.py から切り出し）。

出力先は環境変数 MCP_TOOL_LOG（既定はリポジトリ直下の logs/mcp_tools.log・2026-09-05 Step 3 で
作者の工場の絶対パス /mnt/mtg_rag/logs/mcp_tools.log から置き換え。箱は .env で明示している）。
"""
import json
import os

from sisho.paths import repo_path


TOOL_LOG = os.environ.get(
    "MCP_TOOL_LOG", repo_path("logs", "mcp_tools.log"))
TOOL_LOG_MAX = int(os.environ.get("MCP_TOOL_LOG_MAX", "200"))   # 引数の記録の上限（ベンチは 2000 にして SQL の表名まで採る・2026-09-03）


def _log_tool(name: str, args: dict) -> None:
    """道具の呼び出し履歴（2026-08-11・「彼はどう MCP を使ったか」に query_log だけでは
    答えられなかった観測穴の修理）。search 以外はローカル DB 直結で足跡が無かった。"""
    import datetime
    try:
        os.makedirs(os.path.dirname(TOOL_LOG), exist_ok=True)
        with open(TOOL_LOG, "a", encoding="utf-8") as f:
            ts = datetime.datetime.now().strftime("%m-%d %H:%M:%S")
            arg_s = json.dumps(args, ensure_ascii=False)[:TOOL_LOG_MAX]
            f.write(f"{ts}\t{name}\t{arg_s}\n")
    except Exception:
        pass                     # ログ失敗で道具を殺さない

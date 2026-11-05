"""sisho — MTG Sisho（MCP サーバー）の中身を役目ごとに分けた包み。

mcp_server.py には instructions・server の生成・道具の登録一覧・__main__ だけを置き、
実装は DB 層（db）・道具ログ（toollog）・レート制限（ratelimit）・受付（gate）・各道具（tools/*）へ分ける。
ここには何も置かない（再輸出もしない）＝どのモジュールを使うかを呼ぶ側が名指しする。
"""

"""sisho — MTG Sisho（MCP サーバー）の中身を役目ごとに分けた包み。

mcp_server.py が 1,473 行の一枚板だったのを、DB 層・道具ログ・レート制限・
道具へ分けた。mcp_server.py に残るのは
instructions・server の生成・道具の登録一覧・__main__ だけ。ここには何も置かない
（再輸出もしない）＝どのモジュールを使うかを呼ぶ側が名指しする。
"""

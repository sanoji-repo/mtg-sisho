"""sisho.tools — 道具ごとのモジュール（2026-09-05 Step 2 で probability・verify、Step 3 で残り 7 本）。

各モジュールは道具の説明（`DESCRIPTION`・脳に届く契約）と素の関数を持ち、
登録（server.tool）は mcp_server.py 側で行う＝登録の順番が一箇所で読める。
1 モジュールに道具が 2 本ある rules・sql は、説明を道具ごとの名前
（LOOKUP_MTG_RULE_DESCRIPTION 等）で持つ。
"""

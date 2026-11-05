#!/usr/bin/env python3
"""コードの文字列（docstring を除く）に開発の記録の番号（`#` に 3〜4 桁）を書かない、の網。

文字列はコードなので、コメントと違って公開用の整形では落とせず、MCP の返り値なら利用者の AI にまで届く。
番号はコメントへ書く（同じ行の末尾でよい）。docstring はコメントと同じく整形の対象なので、ここでは見ない。

走らせ方: pytest tests/test_no_ledger_numbers_in_strings.py -v
"""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER_NO = re.compile(r"#\d{3,4}\b")


def _docstring_nodes(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            b = getattr(node, "body", None)
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                out.add(id(b[0].value))
    return out


def test_no_ledger_numbers_in_code_strings():
    hits = []
    for top in ("src", "tests"):
        for path in sorted((ROOT / top).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            docs = _docstring_nodes(tree)
            for node in ast.walk(tree):   # f 文字列の中の定数も ast.Constant として出てくる
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs \
                        and LEDGER_NO.search(node.value):
                    hits.append(f"{path.relative_to(ROOT)}:{node.lineno} {node.value[:60]!r}")
    assert not hits, "文字列に台帳の番号がある（番号はコメントへ移す）:\n" + "\n".join(hits)

"""paths.py — リポジトリ直下の在り処を 1 箇所で解決する。

旧実装は既定値に作者の開発環境の絶対パス（/mnt/mtg_rag/…）を直書きしていた。配備先
（公開サーバー /opt/mtg-sisho）には存在しない場所なので、環境変数での上書きに頼るしかなかった。
自分の位置（src/sisho/paths.py）から 2 つ上がリポジトリ直下＝どこに置かれても自分で分かる。

注意: src/db_config.py は sh/ からも使われる素の脚本なので、この包みを import せず
自前で os.path.dirname する（同じ規則を 2 箇所に書いているのは意図的）。
"""
import os

# src/sisho/paths.py → src/sisho → src → リポジトリ直下
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def repo_path(*parts: str) -> str:
    """リポジトリ直下からの相対パスを絶対パスにする。"""
    return os.path.join(REPO_ROOT, *parts)

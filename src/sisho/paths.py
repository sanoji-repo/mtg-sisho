"""paths.py — リポジトリ直下の在り処を 1 箇所で解決する。

既定値を絶対パスで決め打ちすると、配備先ごとに環境変数での上書きが要る。
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

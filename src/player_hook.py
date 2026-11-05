"""player_hook.py — プレイヤー名の引き当ての口（取得の脚本から呼ぶ）。

取得の脚本（Moxfield・MTGO・MTGTop8）は、デッキごとの（取得元・名前）を resolve_player() に渡す。

- 環境変数 PLAYER_RESOLVER が空（公開の既定）: 何もしない。名前はその場で捨て、どこにも残さない。
  deck_list に player_id の列も書かない（公開側の deck_list にはこの列が無い）。
- PLAYER_RESOLVER が Python のファイルを指す: そのファイルの resolve_player_id(cur, source, name) を
  デッキの INSERT と同じトランザクションで呼び、返った ID を deck_list.player_id に書く。
  名前と ID の対応表は、その実装を持つ側（開発側の DB）にだけ置く。

指した先が読めないときは止める（設定の取り違えで、黙って名前を捨て続けないため）。
名前はログにも出さない。

背景: プレイヤー名は deck_list に持たず、開発側だけの players 表へ隔離する。Moxfield の作者名は、
運営と合意した利用の条件（ユーザー名が出ないこと）に直結する＝公開側へ名前も player_id も流さない。
開発側の実装は、この repo の外にある運用の仕組みの Python ファイル（PLAYER_RESOLVER が指す）。
"""
from __future__ import annotations

import importlib.util
import os

_UNSET = object()
_resolver = _UNSET


def _load():
    global _resolver
    if _resolver is _UNSET:
        path = os.environ.get("PLAYER_RESOLVER", "").strip()
        if not path:
            _resolver = None
        else:
            spec = importlib.util.spec_from_file_location("_player_resolver", path)
            if spec is None or spec.loader is None:
                raise SystemExit(f"PLAYER_RESOLVER の先を読めません: {path}")
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
            except OSError as e:
                raise SystemExit(f"PLAYER_RESOLVER の先を読めません: {path}（{e.__class__.__name__}）") from None
            fn = getattr(mod, "resolve_player_id", None)
            if not callable(fn):
                raise SystemExit(f"PLAYER_RESOLVER の先に resolve_player_id がありません: {path}")
            _resolver = fn
    return _resolver


def enabled() -> bool:
    """引き当ての口が働くか（PLAYER_RESOLVER が指されているか）。"""
    return _load() is not None


class PlayerResolveError(RuntimeError):
    """引き当てに失敗した。名前も、元の例外の本文も含めない（DB のエラーの詳細には名前が入りうる）。"""


def resolve_player(cur, source: str, name: str | None) -> int | None:
    """（取得元・名前）を ID に引き当てる。口が空なら None（名前は捨てる）。

    引き当ての中で例外が起きたら、名前を含まない PlayerResolveError に置き換えて投げ直す。
    元の例外は連鎖にも残さない（except の外で投げる＝__context__ も付かない）。保存は止まる
    （None にして黙って保存を続けない）。"""
    fn = _load()
    if fn is None:
        return None
    try:
        return fn(cur, source, name)
    except Exception as e:  # noqa: BLE001（どの例外でも本文を外へ出さない）
        kind = type(e).__name__
    raise PlayerResolveError(f"プレイヤー名の引き当てに失敗しました（取得元 {source}・{kind}）")


def deck_columns(cols: list[str], vals: list, player_id: int | None) -> tuple[list[str], list]:
    """deck_list の INSERT の列と値に、口が働くときだけ player_id を足す。"""
    if enabled():
        return cols + ["player_id"], vals + [player_id]
    return cols, vals


def _reset_for_tests() -> None:
    """試験で PLAYER_RESOLVER を差し替えたあと、読み込みをやり直すため。"""
    global _resolver
    _resolver = _UNSET

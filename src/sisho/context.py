"""context.py — 要求ごとの文脈変数（contextvars）。

gate と toollog/combos 間の循環参照を防ぐため分離（2026-09-07 小片 8）。
"""
import contextvars

#: 現在の接続札（先頭 8 字。旧パスなら "legacy"、未設定なら空文字 ""）
CURRENT_FUDA: contextvars.ContextVar[str] = contextvars.ContextVar("CURRENT_FUDA", default="")

#: 現在の接続元 IP（旧パスでのレート制限の細分化等に使用。未設定なら空文字 ""）
CURRENT_CLIENT_IP: contextvars.ContextVar[str] = contextvars.ContextVar("CURRENT_CLIENT_IP", default="")


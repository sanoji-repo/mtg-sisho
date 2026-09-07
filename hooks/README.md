# Claude Code 向け: 回答前の強制検証（Stop フック）

脚本は同じディレクトリの `sisho_stop_verify.py`、設定例は `settings.example.json`。

LLM はプロンプト指示のみでは 97〜99% 程度の遵守率にとどまる（[docs/bench/README.md](../docs/bench/README.md) 参照）。完全性を期すには、クライアント側で「回答生成 → 機械検証 → 不合格なら再生成指示 → 最終出力」を強制する仕組みが必要である。
`hooks/sisho_stop_verify.py` は Claude Code の Stop フック（応答終了直前に外部スクリプトを割り込ませ、出力を差し戻して修正続行させる仕組み）に対応しており、
モデルの最終出力テキストを `verify_answer` と同等のロジックで検証し、DB に存在しない名称や書式の崩れがあれば理由と候補をフィードバックして再生成させる。
なお、2 回目の再試行でも不合格の場合は、無限ループを防ぐためそのまま出力させる設計となっている。設定例は `hooks/settings.example.json` を参照。
`claude -p`（headless モード）での動作も確認済み（2026-08-23）。※claude.ai の Web インターフェースには適用不可。

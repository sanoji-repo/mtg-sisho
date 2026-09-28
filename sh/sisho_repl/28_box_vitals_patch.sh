#!/bin/bash
# 【注記】適用済みの一回物。結果を表示するだけで判定して止まらない＝もう一度使うなら、期待値を外れたら exit 1 と控えへの戻しを先に足すこと。
# 28_box_vitals_patch.sh — 公開サーバーの ~/bin/vitals.sh（OS ユーザー sanoji）の DB 接続を readonly_ai から見張り専用のロール sanoji へ移す。
# 28_box_hide_activity.sql（ロール sanoji を作り pg_stat_activity を客から隠す）の直後に流す:
#   ssh sisho bash -s < 28_box_vitals_patch.sh
# 変えるのは 3 つ: 接続（-h localhost -U readonly_ai → Unix ソケットの peer・-h /var/run/postgresql を明示＝
# Debian の psql は -h が無いと postgresql.conf を読みに行き、640 にしてあるので sanoji では
# 『Invalid data directory for cluster 18 main』で落ちる）・MCP 客の数え方
# （usename=current_user → 'readonly_ai'）・その説明のコメント。控えは vitals.sh.bak_20260926。何度流しても同じ結果。
set -euo pipefail
f="$HOME/bin/vitals.sh"
[ -f "$f.bak_20260926" ] || cp -p "$f" "$f.bak_20260926"
sed -i \
  -e 's/psql -h localhost -U readonly_ai -d rag_sisho/psql -h \/var\/run\/postgresql -d rag_sisho/g' \
  -e 's/psql -d rag_sisho -Atc/psql -h \/var\/run\/postgresql -d rag_sisho -Atc/g' \
  -e "s/where usename=current_user and backend_type='client backend'/where usename='readonly_ai' and backend_type='client backend'/" \
  -e 's/^# 同一ロール readonly_ai の行は pg_stat_activity で伏せられない＝MCP 客の client backend を直接数えられる$/# 2026-09-26: 見張りは DB ロール sanoji（pg_monitor・Unix ソケットの peer）で読む＝客（readonly_ai）の client backend を名指しで数える/' \
  "$f"
bash -n "$f"
echo "readonly_ai の残り: $(grep -c 'U readonly_ai' "$f") 行（0 が正）"
echo "名指しの数え方: $(grep -c "usename='readonly_ai'" "$f") 行（1 が正）"
psql -h /var/run/postgresql -d rag_sisho -Atc "select current_user, count(*) filter (where usename='readonly_ai' and backend_type='client backend'), (select count(*) from pg_stat_subscription where worker_type='apply') from pg_stat_activity"

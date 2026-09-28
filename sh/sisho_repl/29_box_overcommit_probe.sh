#!/bin/bash
# 【注記】適用済みの一回物。結果を表示するだけで判定して止まらない＝もう一度使うなら、期待値を外れたら exit 1 と控えへの戻しを先に足すこと。
# 29_box_overcommit_probe.sh — 公開サーバーで vm.overcommit_memory=2 が「確保の瞬間に断る」ことを数秒だけ確かめる。
# 流し方（root で）: ssh sisho 'sudo bash -s' < 29_box_overcommit_probe.sh
#
# 手順: 今の値を控える → 上限（CommitLimit）を「今の貸し出し済み Committed_AS + 約 570MB」まで絞る → mode 2 →
#   900MB の値を作る SQL → 「out of memory」で断られるか → 必ず元の値に戻す（trap・失敗しても戻る）。
# 絞っている間は他のプロセスの確保も断られうるので、絞る時間は SQL 1 本の数秒だけにする。
set -u
old_mode=$(sysctl -n vm.overcommit_memory); old_ratio=$(sysctl -n vm.overcommit_ratio)
restore() { sysctl -q -w vm.overcommit_memory="$old_mode" vm.overcommit_ratio="$old_ratio"; echo "戻した: mode=$(sysctl -n vm.overcommit_memory) ratio=$(sysctl -n vm.overcommit_ratio)"; }
trap restore EXIT

mem=$(awk '/^MemTotal:/{print $2}' /proc/meminfo); swap=$(awk '/^SwapTotal:/{print $2}' /proc/meminfo)
used=$(awk '/^Committed_AS:/{print $2}' /proc/meminfo)
ratio=$(( ( (used + 583680 - swap) * 100 + mem - 1 ) / mem ))   # 切り上げ
echo "前: mode=$old_mode ratio=$old_ratio Committed_AS=$((used/1024))MB → 試験の ratio=$ratio"
sysctl -q -w vm.overcommit_ratio="$ratio" vm.overcommit_memory=2
echo "試験中: CommitLimit=$(( $(awk '/^CommitLimit:/{print $2}' /proc/meminfo) / 1024 ))MB Committed_AS=$(( $(awk '/^Committed_AS:/{print $2}' /proc/meminfo) / 1024 ))MB"
t0=$(date +%s.%N)
out=$(timeout 20 sudo -u postgres psql -d rag_sisho -Atc "SELECT length(repeat('x', 900000000))" 2>&1)
echo "900MB の SQL: ${out}（$(awk -v a="$t0" -v b="$(date +%s.%N)" 'BEGIN{printf "%.2f", b-a}') 秒）"

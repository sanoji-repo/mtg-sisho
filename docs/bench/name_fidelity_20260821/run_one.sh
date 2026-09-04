#!/usr/bin/env bash
# 使い方: run_one.sh "<cond>\t<idx>\t<card_name>"（1 行丸ごと）
W=/tmp/claude-1003/-mnt-mtg-rag/25008f6c-85b1-4617-98c4-9498c6ec23e0/scratchpad
IFS=$'\t' read -r COND IDX CARD <<< "$1"
OUT=$W/name_runs/${COND}_${IDX}.json
[ -s "$OUT" ] && exit 0
P="次の Magic: The Gathering のカードの日本語の正式名（日本語版カードに印刷されている名前）を答えてください。日本語版が存在しないカードなら「日本語版なし」と答えてください。分からない場合は「不明」と答えてください。出力は 1 行だけ・形式は「答え: <名前>」。
カード: ${CARD}"
if [ "$COND" = on ]; then
  EXTRA=(--mcp-config $W/mcp_on.json --strict-mcp-config --allowedTools "mcp__mtg-rag__*")
else
  EXTRA=(--mcp-config $W/mcp_off.json --strict-mcp-config)
fi
cd $W && timeout 180 claude -p "$P" --model sonnet --output-format json "${EXTRA[@]}" > "$OUT.tmp" 2>/dev/null && mv "$OUT.tmp" "$OUT"
echo "done $COND $IDX $CARD"

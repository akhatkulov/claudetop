#!/usr/bin/env bash
# Claude Code statusLine uchun — bir qatorli usage/limit xulosasini chiqaradi.
# Claude Code stdin'ga sessiya JSON'ini yuboradi; biz uni iste'mol qilamiz
# (o'zimiz ~/.claude fayllaridan o'qiymiz).
#
# settings.json (~/.claude/settings.json) ga qo'shing:
#   {
#     "statusLine": {
#       "type": "command",
#       "command": "/ABSOLUTE/PATH/claudetop/statusline/claudetop-statusline.sh"
#     }
#   }
cat >/dev/null 2>&1 || true   # stdin JSON'ini drenajlaymiz

SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
CC="$(dirname "$(dirname "$SELF")")/claudetop"

if [[ -x "$CC" ]]; then
  "$CC" --compact 2>/dev/null
elif command -v claudetop >/dev/null 2>&1; then
  claudetop --compact 2>/dev/null
fi

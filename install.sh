#!/usr/bin/env bash
# claudetop'ni ~/.local/bin ga symlink qilib o'rnatadi (sudo shart emas).
set -euo pipefail

SELF="$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")"
SRC_DIR="$(dirname "$SELF")"
SRC="$SRC_DIR/claudetop"
BIN_DIR="${CLAUDETOP_BIN:-$HOME/.local/bin}"
DEST="$BIN_DIR/claudetop"

chmod +x "$SRC" "$SRC_DIR/lib/engine.py"
mkdir -p "$BIN_DIR"
ln -sf "$SRC" "$DEST"
echo "✔ symlink: $DEST → $SRC"

case ":$PATH:" in
  *":$BIN_DIR:"*) echo "✔ $BIN_DIR PATH'da — endi 'claudetop' deb chaqirsangiz bo'ladi" ;;
  *) echo "⚠ $BIN_DIR PATH'da emas. Shell profilingizga qo'shing:"
     echo "    export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

COMP="$SRC_DIR/completions/claudetop.bash"
if [[ -f "$COMP" ]]; then
  echo "ℹ tab-completion uchun profilingizga qo'shing:  source \"$COMP\""
fi

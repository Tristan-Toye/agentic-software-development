#!/usr/bin/env bash
# Add a second (or third) Claude login the runner can switch to when an
# account hits its usage limit. Each login lives in its own config dir
# (CLAUDE_CONFIG_DIR, the documented way to run several accounts side by
# side); this script makes that dir share everything but the login with
# ~/.claude, and registers it in ~/.agentic/accounts.json.
#
#   setup_claude_account.sh NAME [DIR]     # DIR defaults to ~/.claude-NAME
#   setup_claude_account.sh --list
#
# Shared by symlink: settings.json, CLAUDE.md, RTK.md, plugins/, hooks/,
# commands/, skills/, agents/ — so the plugin, hooks and settings are the
# same on every account. Not shared: the login (.credentials.json and the
# Keychain entry, both keyed to the dir), .claude.json, projects/ (session
# history), and caches.
#
# After it runs, log in once: `CLAUDE_CONFIG_DIR=DIR claude`, then /login
# with the other account. The runner then switches by itself; for an
# interactive session use the `claude-NAME` alias it prints.
set -euo pipefail

REG="$HOME/.agentic/accounts.json"
if [[ "${1:-}" == "--list" ]]; then
  [[ -f "$REG" ]] && cat "$REG" || echo "no accounts registered (one account: ~/.claude)"
  exit 0
fi
NAME="${1:?usage: setup_claude_account.sh NAME [DIR]}"
[[ "$NAME" =~ ^[a-z0-9_-]+$ ]] || { echo "NAME must be lower-case letters, digits, - or _" >&2; exit 2; }
DIR="${2:-$HOME/.claude-$NAME}"
SRC="$HOME/.claude"

mkdir -p "$DIR"
for item in settings.json CLAUDE.md RTK.md plugins hooks commands skills agents; do
  [[ -e "$SRC/$item" ]] || continue
  if [[ -e "$DIR/$item" && ! -L "$DIR/$item" ]]; then
    echo "keep   $DIR/$item (exists, not a link)"
  else
    ln -sfn "$SRC/$item" "$DIR/$item"
    echo "link   $DIR/$item -> $SRC/$item"
  fi
done

mkdir -p "$(dirname "$REG")"
python3 - "$REG" "$NAME" "$DIR" <<'PY'
import json, os, sys
reg, name, d = sys.argv[1:]
try:
    j = json.load(open(reg))
except (OSError, ValueError):
    j = {}
acc = j.get("accounts") or [{"name": "main", "dir": "~/.claude"}]
acc = [a for a in acc if a.get("name") != name] + [{"name": name, "dir": d.replace(os.path.expanduser("~"), "~", 1)}]
j["accounts"] = acc
json.dump(j, open(reg, "w"), indent=2)
print("registered:", ", ".join(f"{a['name']} ({a['dir']})" for a in acc))
PY

cat <<EOF

Next, once:  CLAUDE_CONFIG_DIR=$DIR claude      then /login with the other account, then /exit.
Alias for interactive use (add to ~/.zshrc):  alias claude-$NAME='CLAUDE_CONFIG_DIR=$DIR claude'
The runner (work-on) now switches to the other account when one hits its limit.
EOF

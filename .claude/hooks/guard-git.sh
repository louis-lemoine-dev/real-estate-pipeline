#!/bin/bash
# Safety net behind the permission rules: blocks risky git/gh commands in any spelling.
INPUT=$(cat)
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
[ -z "$CMD" ] && exit 0

block() { echo "Blocked: $1" >&2; exit 2; }

if echo "$CMD" | grep -Eq 'gh[[:space:]]+pr[[:space:]]+merge.*--admin'; then
  block "never merge with --admin (see CLAUDE.md hard rules)."
fi

if echo "$CMD" | grep -Eq 'git[[:space:]].*add[[:space:]]+(-A|--all|\.)([[:space:]]|$)'; then
  block "never use 'git add -A' or 'git add .'; name the paths."
fi

if echo "$CMD" | grep -Eq 'git[[:space:]]+(.*[[:space:]])?(commit|push)([[:space:]]|$)'; then
  BRANCH=$(git -C "${CLAUDE_PROJECT_DIR:-.}" symbolic-ref --short HEAD 2>/dev/null)
  if [ "$BRANCH" = "main" ]; then
    block "you are on main. Create a branch first (git switch -c <prefix>/<name>) and run commit/push as its own command."
  fi
fi

exit 0

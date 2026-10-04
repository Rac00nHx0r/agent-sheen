#!/usr/bin/env bash
# One-time setup: creates the GitHub repo, stores your two secrets, and tests the connection.
# Needs the GitHub CLI (https://cli.github.com) signed in:  gh auth login
set -euo pipefail
cd "$(dirname "$0")"

REPO="${1:-agent-sheen}"
VISIBILITY="${VISIBILITY:-public}"   # public = free unlimited Actions minutes. Logs contain no mail content.
ADDRESS="${ICLOUD_ADDRESS:-agentsheen@icloud.com}"

command -v gh >/dev/null || { echo "Install the GitHub CLI first: https://cli.github.com"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "Run 'gh auth login' first."; exit 1; }
command -v python3 >/dev/null && python3 -m unittest discover -s tests >/dev/null 2>&1 \
  && echo "Local tests passed." || echo "(Skipping local tests.)"

if [ ! -d .git ]; then git init -q -b main && git add -A && git commit -q -m "Agent Sheen cloud responder"; fi
if ! gh repo view "$REPO" >/dev/null 2>&1; then
  gh repo create "$REPO" "--$VISIBILITY" --source=. --remote=origin --push
else
  echo "Repo $REPO already exists; reusing it."
fi
FULL="$(gh repo view "$REPO" --json nameWithOwner -q .nameWithOwner)"

# Mail older than this is never answered (keeps old test mail out). 2 hours back catches anything recent.
if date -u -v-2H +%Y-%m-%dT%H:%M:%SZ >/dev/null 2>&1; then
  SINCE="$(date -u -v-2H +%Y-%m-%dT%H:%M:%SZ)"      # macOS
else
  SINCE="$(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%SZ)"   # Linux
fi
gh variable set ICLOUD_ADDRESS --repo "$FULL" --body "$ADDRESS"
gh variable set IGNORE_BEFORE  --repo "$FULL" --body "$SINCE"

echo
echo "Create an app-specific password at https://account.apple.com (Sign-In and Security > App-Specific Passwords)"
echo "while signed in as $ADDRESS. Paste it below (nothing is shown as you type)."
read -rsp "iCloud app-specific password: " PW; echo
[ -n "$PW" ] && printf '%s' "$PW" | gh secret set ICLOUD_APP_PASSWORD --repo "$FULL"
read -rsp "Anthropic API key (sk-ant-...): " KEY; echo
[ -n "$KEY" ] && printf '%s' "$KEY" | gh secret set ANTHROPIC_API_KEY --repo "$FULL"
unset PW KEY

echo
echo "Testing the connection (check mode)..."
gh workflow run agent-sheen.yml --repo "$FULL" -f mode=check
sleep 8
gh run watch --repo "$FULL" --exit-status "$(gh run list --repo "$FULL" --workflow agent-sheen.yml --limit 1 --json databaseId -q '.[0].databaseId')" \
  && echo "All good. Agent Sheen now checks the mailbox every ~5 minutes, even with your Mac off." \
  || echo "The check failed. Open the run log: gh run view --repo $FULL --log"

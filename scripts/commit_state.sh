#!/usr/bin/env bash
# Commit state/ and push, rebasing on top of concurrent pushes.
set -u
msg="${1:-state update}"
git config user.name "ainews-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add state
if git diff --cached --quiet; then
  echo "state unchanged"
  exit 0
fi
git commit -q -m "$msg"
branch="${GITHUB_REF_NAME:-$(git rev-parse --abbrev-ref HEAD)}"
for i in 1 2 3 4; do
  if git pull -q --rebase -X theirs origin "$branch" && git push -q origin "HEAD:$branch"; then
    echo "state pushed"
    exit 0
  fi
  sleep $((i * 3))
done
echo "::error::could not push state"
exit 1

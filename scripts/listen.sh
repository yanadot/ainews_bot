#!/usr/bin/env bash
# After the digest, stay up for LISTEN_MINUTES and react to Telegram within a minute:
# a picked topic is written right away, «В канал» publishes right away.
# GitHub runs the 10-minute approvals schedule only every few hours, hence this window.
set -u
end=$(( $(date +%s) + ${LISTEN_MINUTES:-120} * 60 ))
while [ "$(date +%s)" -lt "$end" ]; do
  LONG_POLL=50 GITHUB_OUTPUT=/dev/null python3 scripts/approvals.py || true
  if python3 -c "import json,sys; sys.exit(0 if json.load(open('state/write_job.json')).get('topics') else 1)"; then
    if bash scripts/write_posts.sh; then
      python3 scripts/send_drafts.py || true
    fi
  fi
  bash scripts/commit_state.sh "state - listen" || true
done

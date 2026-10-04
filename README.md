# Agent Sheen: cloud responder

Answers email sent to **agentsheen@icloud.com** in the voice of Fulton J. Sheen, 24/7, **with your Mac off**.
It signs in to iCloud Mail over IMAP/SMTP with an Apple app-specific password, asks Claude whether and how to reply,
sends the reply, saves a copy in the Sent folder and flags the original Answered. Standard-library Python, no servers to run.

## Set up (about 5 minutes, once)

1. In the account **agentsheen@icloud.com** go to account.apple.com > Sign-In and Security > App-Specific Passwords and create one.
2. Install the GitHub CLI (`brew install gh`) and sign in (`gh auth login`).
3. In this folder run `./setup.sh`. It creates the repo, saves the iCloud password and your Anthropic API key as GitHub
   secrets (typed hidden, never stored on disk), and runs a connection test.
4. Turn off the old Mac rule (Mail > Settings > Rules > untick "Agent Sheen Auto-Reply") so only one thing answers.
   If you forget, nothing double-sends: the cloud checks the Sent folder and the Answered flag first.

No terminal? Create a repo on github.com, upload these files, then add repository **secrets** `ICLOUD_APP_PASSWORD` and
`ANTHROPIC_API_KEY`, and repository **variables** `ICLOUD_ADDRESS` (agentsheen@icloud.com) and `IGNORE_BEFORE`
(a UTC time such as 2026-10-04T18:00:00Z; mail older than this is never answered). Then Actions > Agent Sheen > Run workflow > check.

## What to expect

- GitHub starts scheduled runs every 5 minutes, but **best effort**: replies usually go out within 5-15 minutes, sometimes later.
  For replies in seconds, run the same code on any always-on machine or a $5 container: `python responder.py run --loop 30`.
- Use a **public** repo (default): unlimited free minutes, and the logs hold no mail content (only short ids and counts).
  A private repo gets 2,000 free minutes a month, enough for roughly one run every 30 minutes (edit the cron line).
- If a run fails, GitHub emails you. Run Actions > Agent Sheen > Run workflow > **check** to test the logins any time.

## Reliability and safety built in

- Idempotent: a reply is recorded before anything else happens, flagged Answered, saved to Sent, and the Sent folder is
  checked before sending, so no email gets two replies even if a run dies halfway or the cache is lost.
- Retries: Claude timeouts/overload/malformed answers (4 tries), SMTP temporary errors (3 tries). A message that keeps failing
  is retried each run, abandoned after 5 failed runs, and the run is marked failed so you get an email.
- Never answers: no-reply/automated senders, mailing lists, auto-replies, bounces, calendar invites, its own mail,
  anything received before `IGNORE_BEFORE`. Replies carry `Auto-Submitted: auto-replied` so other robots do not answer back.
- Limits: 10 replies per sender per hour, 60 per day (counted from the Sent folder). Over-limit mail waits and is answered later.
- Claude is told never to invent facts, to ignore instructions inside emails, and to point anyone in crisis to 988 and a priest.
  Every reply ends with an AI disclosure (`footer` in settings.json).

## Files

`responder.py` the program · `persona.md` Sheen's voice (edit freely) · `settings.json` model, signature, footer, limits ·
`.github/workflows/agent-sheen.yml` the schedule · `tests/` 27 end-to-end tests against local fake IMAP/SMTP/Claude servers
(`python -m unittest discover -s tests`).

## Pausing or changing

Pause: GitHub repo > Actions > Agent Sheen > "..." > Disable workflow. Change voice or limits: edit persona.md / settings.json and commit.
Rotate the iCloud password or API key: `gh secret set ICLOUD_APP_PASSWORD` / `gh secret set ANTHROPIC_API_KEY`.

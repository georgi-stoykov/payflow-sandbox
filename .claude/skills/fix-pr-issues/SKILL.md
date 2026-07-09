---
name: fix-pr-issues
description: Review the AI-quality-gate GitHub issues filed against the current PR, triage them against the seeded-bug list, and prepare fix commits on the PR branch. Use when the user asks to review/fix issues logged on their PR.
argument-hint: "[issue numbers... | 'all'] (no args = all open gate issues for the current PR)"
---

# Fix PR issues

Review GitHub issues filed by the AI quality gate against the current branch's PR, decide which ones are legitimately fixable, apply the fixes, verify them against the running app, and commit.

## 1. Collect the issues

- Find the PR for the current branch: `gh pr view --json number,headRefName,url`.
- List candidate issues: `gh issue list --state open --label ai-quality-gate --json number,title,body,labels`. Keep only issues whose body references this PR (`PR #<n>` in the Context line). If the label yields nothing, fall back to all open issues and filter by that same Context line.
- If the user passed issue numbers as arguments, restrict to those; otherwise take all of them.

Each gate issue follows a fixed format — parse from the body: the **violated rule** (quoted from `../payflow-automation/docs/business-rules.md`), the **evidence** (request → observed status vs expected), the **failing tests** (paths inside `../payflow-automation`), and the **severity label**.

## 2. Triage each issue — seeded bug or genuine gap?

This sandbox intentionally ships defects. For every issue, locate the root cause in `main.py` and classify it:

- **Seeded bug** — the behavior comes from a `# BUG #n (...)`-marked block or an `if BUGS:` branch. Per `CLAUDE.md`, these must not be fixed without deliberate intent.
- **Genuine gap** — the behavior is wrong (per the violated rule in `../payflow-automation/docs/business-rules.md`) in **both** the `BUGS=on` and `BUGS=off` paths, e.g. a validation the reference implementation never had.

Decision rule:
- Genuine gaps → fix now.
- Seeded bugs → fix **only** if the user explicitly named that issue number in the arguments (that is deliberate intent). Otherwise do not touch them; report them in the summary as "maps to seeded BUG #n — skipped by design" so the user can decide.
- An issue may be a false positive (the rule is misread, or the observed behavior is actually allowed). Verify against the business rules before fixing; if it's a false positive, don't change code — note it for closure with a comment instead.

## 3. Apply fixes

- Work on the PR branch (confirm with `git branch --show-current`; never commit these fixes to `main`).
- The `BUGS=off` path is the reference implementation and must stay behaviorally correct. When fixing a genuine gap, the fix normally lives in the shared path so both modes are correct. Preserve all `# BUG #n` markers and `if BUGS:` branching you aren't deliberately fixing.
- Any new validation on API models should return `422` for bad input (the gate's expected code); prefer Pydantic validators / explicit checks consistent with the existing style in `main.py`.
- If a fix touches UI markup in the `PAGE` template, wire any new element ids through `sel()` (chaos-mode requirement).

## 4. Verify

Verification is black-box, against the real app:

1. Start the app: `uvicorn main:app --port 8000` in the background (default `BUGS=on`).
2. Replay every evidence request from each fixed issue (e.g. `POST /api/quotes` with the exact payloads) and confirm the response now matches the **expected** status/body from the issue.
3. Sanity-check that previously-valid requests still succeed (e.g. a normal quote for a documented pair still returns `201`).
4. If the failing tests named in the issue exist under `../payflow-automation/tests/`, run just those (`python -m pytest <paths> -k <ids>` from that repo) against the running app. Do not modify the automation repo, and remember its suite never starts the app itself — you start/stop the app here.
5. Stop the app when done.

If a fix can't be verified (evidence still failing), do not commit it — investigate or report the blocker.

## 5. Commit and report

- One commit per issue (or one per tightly-coupled group), on the PR branch. Message format:
  - Subject: short imperative summary of the fix.
  - Body: what rule was violated and how it's now enforced, plus `Fixes #<issue>` so the issue auto-closes on merge.
- Commit locally only — do **not** push or comment on the issues unless the user asks.
- Final summary must list: issues fixed (with commit SHAs), issues skipped as seeded bugs (with the BUG # they map to), false positives recommended for closure, and verification results (before → after status codes).

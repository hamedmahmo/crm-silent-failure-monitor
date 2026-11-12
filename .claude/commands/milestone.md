---
description: Check and record a finished milestone, then propose a commit message
argument-hint: [milestone name or short description]
---

Close out this milestone: $ARGUMENTS

1. Run `make lint` and `make test`. If either fails, stop and report the failures with the output. Don't claim the milestone is done.
2. Run `git status` and `git diff --stat` to see what changed since the last commit. Read the diffs for anything you didn't write in this session.
3. Check the definition of done in `CLAUDE.md` and list each item as met or not met, with the evidence.
4. If this milestone involved a choice someone might later question (a library, a data rule, a trade-off), add an entry at the top of `docs/decisions.md` using the template at the bottom of that file. Use today's date.
5. If commands, setup or outputs changed, update `docs/runbook.md`.
6. Write a short summary for me: what was built, what was tested, what is still open.
7. Propose one conventional commit message (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`) with a body of a few plain lines. Don't commit unless I ask.

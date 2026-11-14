Get up to date with the team before working:
1. `git pull --rebase` (stash first if there are local changes, and tell me).
2. Read `docs/STATUS.md`, `docs/DECISIONS.md`, `docs/IDEAS.md` and `git log --oneline -15`.
3. Valio: `git fetch` and read the top of `docs/HANDOFF-rasmus-claude.md` on branch `coord/rasmus-claude`
   (`git show origin/coord/rasmus-claude:docs/HANDOFF-rasmus-claude.md`). List any branch it asks me to review.
   Never merge that branch into `main`.
4. If `contracts/` changed since my last commit, summarise what changed and what it means for my module.
5. Give me a 5-line summary: what changed, what's blocked, what I should do next.

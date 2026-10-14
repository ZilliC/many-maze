# Reglas comunes para subagentes de arreglo

FIX RULES. Repo /Users/macmini/Downloads/many-maze, base branch `validacion-qa`. Python: /Users/macmini/Downloads/many-maze/.venv/bin/python; ruff: .venv/bin/ruff. ALWAYS QT_QPA_PLATFORM=offscreen and MANYMAZE_SETTINGS=<temp .ini> for pytest/scripts (never touch the user's real settings).
- Work in your own worktree: `git -C /Users/macmini/Downloads/many-maze worktree add ../many-maze-auto-<topic> -b auto/<topic> validacion-qa`; verify imports come from the worktree. Never edit files outside it (~/mANY-MAZE-QA and ~/Downloads/Ratas are read-only for you).
- Finding details and evidence: ~/mANY-MAZE-QA/2026-10-11_2224/bitacora/HALLAZGOS.md (search the H-### id) and salidas/<block>/.
- For each finding: first a failing test (next to similar tests), then the fix, matching surrounding style and the plain-language message style. One commit per finding, message starting with the H-### id, ending with a blank line then `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. If a finding is intended behaviour or needs a product decision, don't change it; explain.
- Tests of what you touched after each fix; at the end the full suite ONCE (`.venv/bin/python -m pytest -q`, ~6 min) + `ruff check .`.
- Only if all green: in /Users/macmini/Downloads/many-maze `git merge --no-ff auto/<topic>` (resolve only trivial conflicts and re-run affected tests; else abort and report; on index.lock wait 30 s and retry), `git push origin validacion-qa` (never force), remove the worktree, `git branch -d auto/<topic>`. If not green, don't merge; report.
RETURN ≤10 lines: per finding fixed (commit) / not changed (why); test counts; merge commit + push result.

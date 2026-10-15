# Reglas comunes para subagentes de QA (sin computer use)

CONTEXT: Repo /Users/macmini/Downloads/many-maze (branch validacion-qa; DO NOT commit, merge or push). QA folder: QA=~/mANY-MAZE-QA/2026-10-11_2224 (bitacora/ has BITACORA.md, HALLAZGOS.md, COBERTURA.md — continue them, following their existing numbering H-###; check the last H-### before adding).

You are the QA EXECUTOR: read qa/PLAN_PRUEBA_INTEGRAL.md fully (rules §1, computer-use tips §2, launch §3.3, conventions §4, priorities §5.1), then your block file in qa/bloques/. You ONLY observe and record; you do NOT fix code. Pasada 1 = only the P1 cases in §5.1. Do NOT download the pose model. Check qa/HALLAZGOS_PREVIOS.md and HALLAZGOS.md before opening a finding (cite the existing ID instead of duplicating; many were fixed on 2026-10-13/14 — see `git log --since=2026-10-13` — record those as `OK H-### (ya no ocurre)`).
- Computer-use tools are NOT available. Drive the app's real classes offscreen (QT_QPA_PLATFORM=offscreen; MainWindow, pages, dialogs; QTest clicks/keys; intercept QMessageBox/QFileDialog), following earlier blocks' scripts in $QA/salidas/B03/, B05/, B14/. Only the purely visual (look, legibility, native dialogs) is BLOQUEADO "sin computer use" — never fake GUI results. Use the CURRENT repo code (validacion-qa HEAD), not dist.
- ALWAYS export MANYMAZE_SETTINGS="$QA/salidas/ajustes_<BLOCK>.ini" before creating any QApplication / running app code, so the user's real settings (com.manymaze.mANY-MAZE) are never touched.
- Own output folder $QA/salidas/<BLOCK>/. Python: /Users/macmini/Downloads/many-maze/.venv/bin/python. Never delete anything in ~/Downloads/Ratas. Kill only processes you started.
- Append to the shared $QA bitacora files (BITACORA.md rows + block summary, HALLAZGOS.md with full template fields, COBERTURA.md IDs).

RETURN (≤10 lines): cases run with status counts (OK/FALLA/RARO/BLOQUEADO/N/A), new findings with severity and one-line description each, files written.

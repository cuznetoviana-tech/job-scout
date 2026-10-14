# Job Scout

AI agents that search the web for full-time jobs for soon-to-graduate students.

## How it works
- `agents.py` — three **scout** agents search the web in parallel (new-grad programs, job boards,
  local/niche employers). A **matcher** agent then merges their findings, scores each job 0–100
  against your profile, and flags things to double-check.
  The **application helper** reads one posting and writes an application kit (cover letter, resume
  tweaks, draft answers, checklist) for you to review and submit yourself. Your uploaded resume PDF is
  stored in `my_files/` on your computer only.
- `app.py` — a small Flask web server that runs the agents and serves the page.
- `static/index.html` — the website: profile form, live progress, job cards with Save / Mark applied.

## Run it (Windows PowerShell)
```
cd "$HOME\Desktop\job-scout"
.\.venv\Scripts\python app.py
```
Then open http://localhost:5000. Press `Ctrl+C` in the terminal to stop it.

## First-time setup (already done on this computer)
```
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```
Put your API key in `.env`: `ANTHROPIC_API_KEY=sk-ant-...`. Never share this file or upload it to GitHub
(`.gitignore` already keeps it out).

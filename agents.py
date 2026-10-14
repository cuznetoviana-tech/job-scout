"""The job-hunting agents.

How it works:
  1. Three "scout" agents search the live web at the same time, each from a
     different angle (new-grad programs, job boards, local/niche employers).
  2. A "matcher" agent reads everything the scouts found, removes duplicates,
     scores each job against your profile, and explains why it fits.
  3. An "application helper" agent reads one job posting and prepares an
     application kit (cover letter, resume tweaks, draft answers) for you to
     review and submit yourself.
"""

import base64
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic
from pydantic import BaseModel

MODEL = "claude-opus-5"
# If Claude declines a request, the API automatically retries it on a fallback model.
BETAS = ["server-side-fallback-2026-07-01"]

# Your uploaded resume is kept here, on your computer only (my_files/ is in .gitignore).
RESUME_PDF = Path(__file__).parent / "my_files" / "resume.pdf"

_client = None


def get_client() -> anthropic.Anthropic:
    """Create the API client on first use, so the site still starts before a key is added."""
    global _client
    if _client is None:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("No API key yet. Paste your key into the .env file, then restart app.py.")
        _client = anthropic.Anthropic()
    return _client


# Shown in the "Type of job" dropdowns; the matcher labels every job with one of these.
JOB_TYPES = [
    "Software & Engineering",
    "Data & Analytics",
    "Business & Consulting",
    "Finance & Accounting",
    "Marketing & Communications",
    "Sales & Customer Success",
    "Product & Design",
    "Operations & Supply Chain",
    "Healthcare & Life Sciences",
    "Science & Research",
    "Education",
    "Government & Public Policy",
    "Nonprofit & Social Impact",
    "Media & Creative",
    "Other",
]

SCOUTS = {
    "New-grad programs": (
        "Find new-grad, early-career, rotational, and 'university graduate' full-time "
        "programs at companies that hire people with this background. Check company "
        "career pages directly."
    ),
    "Job boards": (
        "Find entry-level full-time postings on public job boards and applicant tracking "
        "systems (e.g. Greenhouse, Lever, Ashby, Workday, LinkedIn, Indeed, Wellfound)."
    ),
    "Local & niche": (
        "Find entry-level full-time roles at employers in the person's preferred "
        "locations, plus industry-specific job boards, nonprofits, government, and "
        "smaller companies that the big boards tend to miss."
    ),
}

WEB_SEARCH = {"type": "web_search_20260209", "name": "web_search"}
WEB_FETCH = {"type": "web_fetch_20260209", "name": "web_fetch"}


class Job(BaseModel):
    title: str
    company: str
    location: str
    url: str
    posted: str
    deadline: str
    fit_score: int
    why_it_fits: str
    watch_out: str
    source_scout: str
    category: str


class JobList(BaseModel):
    jobs: list[Job]


class ResumeTweak(BaseModel):
    where: str
    suggestion: str
    why: str


class DraftAnswer(BaseModel):
    question: str
    answer: str


class ApplicationKit(BaseModel):
    role_summary: str
    still_open: str
    your_strengths: list[str]
    gaps: list[str]
    resume_tweaks: list[ResumeTweak]
    cover_letter: str
    answers: list[DraftAnswer]
    before_you_submit: list[str]


def profile_to_text(p: dict) -> str:
    fields = [
        ("Name", "name"),
        ("Type of job wanted", "job_type"),
        ("Degree / major", "major"),
        ("School", "school"),
        ("Graduation date", "grad_date"),
        ("Target roles", "roles"),
        ("Skills", "skills"),
        ("Preferred locations", "locations"),
        ("Remote OK", "remote"),
        ("Needs visa sponsorship", "visa"),
        ("Industries of interest", "industries"),
        ("Anything else", "notes"),
        ("Resume (pasted text)", "resume"),
    ]
    return "\n".join(f"{label}: {p[key]}" for label, key in fields if p.get(key))


def resume_blocks() -> list[dict]:
    """The uploaded resume PDF as a content block Claude can read, or nothing if none uploaded."""
    if not RESUME_PDF.exists():
        return []
    data = base64.standard_b64encode(RESUME_PDF.read_bytes()).decode()
    return [{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}}]


def _text_of(response) -> str:
    return "\n".join(b.text for b in response.content if b.type == "text")


def _run_with_web_tools(prompt: str, tools: list[dict]):
    """One agent turn with web tools. A long search can pause; resend so it can continue."""
    messages = [{"role": "user", "content": prompt}]
    response = None
    for _ in range(5):
        response = get_client().beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=BETAS,
            fallbacks="default",
            thinking={"type": "adaptive"},
            tools=tools,
            messages=messages,
        )
        if response.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": response.content}]
    return response


# ---------------------------------------------------------------- job search


def run_scout(name: str, focus: str, profile: str, log) -> str:
    log(f"🔎 {name} scout started searching")
    prompt = f"""You are a job-search scout helping a soon-to-graduate student find a full-time job.

Your angle: {focus}

Student profile:
{profile}

Search the web and find up to 10 real, currently open, full-time, entry-level positions that fit this
person. Only include postings you actually found, with the direct link to the posting (not a search page).
Skip internships, senior roles, and postings that look closed or older than about two months.

For each job list: title, company, location, direct URL, posted date (or "unknown"),
application deadline (or "unknown"), and one line on why it could fit."""

    response = _run_with_web_tools(prompt, [{**WEB_SEARCH, "max_uses": 8}])
    if response.stop_reason == "refusal":
        log(f"⚠️ {name} scout couldn't complete its search")
        return ""
    log(f"✅ {name} scout finished")
    return _text_of(response)


def run_matcher(profile: str, scout_reports: dict[str, str], log) -> list[dict]:
    log("🧠 Matcher is ranking what the scouts found")
    reports = "\n\n".join(
        f"<report scout=\"{name}\">\n{text}\n</report>" for name, text in scout_reports.items() if text
    )
    resume = resume_blocks()
    prompt = f"""You are a career advisor. Several scouts searched the web for full-time jobs for this student.
Combine their findings into one list.
{"The attached PDF is the student's resume; use it when judging fit." if resume else ""}

Student profile:
{profile}

Scout reports:
{reports}

Rules:
- Merge duplicates (same role at the same company) into one entry.
- Drop anything that is not a full-time, entry-level role, or that has no direct posting URL.
- fit_score is 0-100: how well the job matches the student's major, skills, experience, roles, and location.
- why_it_fits: 1-2 sentences, specific to this student, written directly to them ("your degree", "fits your
  graduation date"). Never refer to the student by name or with he/she/they.
- watch_out: anything to double-check (e.g. requires 2+ years, visa, relocation, unclear dates), or "" if nothing.
  Also written to the student as "you".
- posted and deadline: just a date (e.g. "7 December 2026") or exactly "unknown" - no notes or comments.
  If a date is uncertain, use "unknown" and explain in watch_out. Never invent URLs or details.
- source_scout: which scout found it.
- category: exactly one of: {", ".join(JOB_TYPES)}.
- Sort by fit_score, highest first."""

    response = get_client().beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        betas=BETAS,
        fallbacks="default",
        thinking={"type": "adaptive"},
        output_format=JobList,
        messages=[{"role": "user", "content": [*resume, {"type": "text", "text": prompt}]}],
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        log("⚠️ Matcher couldn't rank the results")
        return []
    jobs = [j.model_dump() for j in response.parsed_output.jobs]
    log(f"🎉 Done — found {len(jobs)} jobs")
    return jobs


def run_search(profile_dict: dict, log) -> list[dict]:
    profile = profile_to_text(profile_dict)
    with ThreadPoolExecutor(max_workers=len(SCOUTS)) as pool:
        futures = {
            name: pool.submit(run_scout, name, focus, profile, log) for name, focus in SCOUTS.items()
        }
        reports = {}
        for name, future in futures.items():
            try:
                reports[name] = future.result()
            except anthropic.APIError as e:
                log(f"⚠️ {name} scout hit an error: {e.message}")
                reports[name] = ""

    if not any(reports.values()):
        raise RuntimeError("None of the scouts returned results. Check your API key and try again.")
    return run_matcher(profile, reports, log)


# ---------------------------------------------------------------- application helper


def prep_application(job: dict, profile_dict: dict, log) -> dict:
    resume = resume_blocks()
    if not resume and not profile_dict.get("resume"):
        raise RuntimeError(
            "Add your resume first (upload a PDF or paste it in the form), so the helper has something to work from."
        )
    profile = profile_to_text(profile_dict)

    log("📖 Reading the job posting and researching the company")
    research_prompt = f"""Research this job posting for a student who is about to apply.

Job: {job.get("title")} at {job.get("company")} ({job.get("location")})
Posting URL: {job.get("url")}

Fetch the posting URL and, if useful, search the web. Report:
1. Whether the posting still looks open (or closed / couldn't tell), and the deadline if stated.
2. The main responsibilities.
3. Required and preferred qualifications, word for word where possible.
4. Any application questions or required materials the posting mentions.
5. 3-5 specific facts about the company (mission, products, recent news, values) that would make a
   cover letter feel personal. Only include facts you actually found."""

    research = _run_with_web_tools(research_prompt, [{**WEB_FETCH, "max_uses": 3}, {**WEB_SEARCH, "max_uses": 4}])
    notes = _text_of(research) if research.stop_reason != "refusal" else ""
    if not notes:
        notes = "(The posting couldn't be read. Work from the job details above and say so in still_open.)"

    log("✍️ Writing your cover letter, resume tweaks, and answers")
    kit_prompt = f"""You are a career coach helping a soon-to-graduate student apply for this job.
{"The attached PDF is the student's resume." if resume else ""}

Job: {job.get("title")} at {job.get("company")} ({job.get("location")})
Posting URL: {job.get("url")}

Research notes on the posting and company:
{notes}

Student profile:
{profile}

Prepare an application kit the student will review and submit themselves.
- role_summary: 2-3 sentences on what the job is and what they're really looking for.
- still_open: "Looks open", "Looks closed", or "Couldn't tell", plus the deadline if known.
- your_strengths: 3-5 specific things from the student's background that match this job.
- gaps: requirements the student doesn't clearly meet, each with a short honest tip for addressing it.
- resume_tweaks: 3-6 concrete edits (which section/bullet, the suggested rewording, why it helps for this job).
- cover_letter: a complete, warm, specific cover letter (250-350 words) that uses the company facts.
  Sign it with the student's name if known.
- answers: draft answers (80-150 words) to the application questions found in the posting; if none were
  found, answer "Why are you interested in this role?" and "Why do you want to work at {job.get("company")}?".
- before_you_submit: a short checklist of things to verify or attach before submitting.

Write every section except the cover letter directly to the student ("you", "your"); never refer to them
in the third person or with he/she/they.

Honesty rules: never invent experience, grades, skills, or numbers the student doesn't have. If something
would help but you don't know it, leave a placeholder in [square brackets] for the student to fill in."""

    response = get_client().beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        betas=BETAS,
        fallbacks="default",
        thinking={"type": "adaptive"},
        output_format=ApplicationKit,
        messages=[{"role": "user", "content": [*resume, {"type": "text", "text": kit_prompt}]}],
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RuntimeError("The helper couldn't prepare this application. Try again or pick another job.")
    log("🎉 Your application kit is ready")
    return response.parsed_output.model_dump()

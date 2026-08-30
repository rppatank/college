"""agents/agent.py — LLM-powered agentic layer for College Triage (Phase 2).

Wraps the Ollama-compatible HTTP API at the zenmux endpoint.

IMPORTANT — model quirk: the configured model (dots-studio/dots3-note-prev) is
a reasoning model. Its reply comes back in the "reasoning" field (not "content"),
and it thinks out loud before answering. Two consequences:

  1. chat() must read "reasoning" as a fallback for "content".
  2. For structured JSON output we keep the gap-detection in code (deterministic)
     and use the LLM only for free-form text: wording enhancement and rationale.

Config is read from .env (gitignored):
  OLLAMA_API_KEY, ollama_url, model
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- config

def _load_env() -> dict:
    env: dict[str, str] = {}
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    for k in ("OLLAMA_API_KEY", "ollama_url", "model"):
        if k not in env and os.environ.get(k):
            env[k] = os.environ[k]
    return env


_ENV = _load_env()
BASE_URL = _ENV.get("ollama_url", "https://zenmux.ai/api/v1")
TOKEN = _ENV.get("OLLAMA_API_KEY", "")
MODEL = _ENV.get("model", "dots-studio/dots3-note-prev")


# ---------------------------------------------------------------- low-level

def chat(messages: list[dict], max_tokens: int = 500, temperature: float = 0.7) -> str:
    """Call the LLM chat-completion endpoint. Returns the assistant's text.

    Falls back gracefully (returns "") when the endpoint is unreachable so
    callers can use hardcoded defaults.
    """
    if not TOKEN:
        return ""
    url = f"{BASE_URL}/chat/completions"
    body = {
        "model": MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError) as e:
        print(f"[agent] LLM call failed: {e}", file=sys.stderr)
        return ""
    choices = resp.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    # Reasoning models put the reply in "reasoning"; some use "content".
    return (msg.get("content") or msg.get("reasoning") or "").strip()


# ---------------------------------------------------------------- gap detection

# Deterministic gap detection — reliable and fast.
# Each entry: (field, predicate, question_text, reason)
_GAPS = [
    (
        "gpa",
        lambda a: a.get("gpa") is None or not (0 < float(a.get("gpa", 0)) <= 4.0),
        "I need a valid unweighted GPA between 0 and 4. What's yours?",
        "GPA is missing or out of range",
    ),
    (
        "budget",
        lambda a: a.get("budget") is None or float(a.get("budget", 0)) <= 0,
        "What's your maximum annual budget for college costs (before aid)?",
        "Budget is missing",
    ),
    (
        "majors",
        lambda a: len(str(a.get("majors", "")).strip()) < 3,
        "What majors or fields of study are you interested in? Be specific.",
        "Majors field is too vague",
    ),
    (
        "residency",
        lambda a: not a.get("residency"),
        "Do you have residency in any other states -- e.g. where your parents or "
        "grandparents live? This affects in-state tuition at public universities.",
        "No additional residency states given",
    ),
]


def detect_gaps(answers: dict) -> list[dict]:
    """Return follow-up questions for genuinely missing or unclear answers.

    Uses deterministic rules first (reliable), then optionally asks the LLM to
    rephrase the wording for a more natural conversational tone.
    """
    followups = []
    for field, predicate, text, reason in _GAPS:
        if predicate(answers):
            followups.append({"id": field, "text": text, "reason": reason})

    if not followups:
        return []

    # Optional: ask the LLM to polish the wording (fire-and-forget; fall back
    # to the deterministic text if the LLM is unavailable).
    polished = _llm_enhance_questions(followups, answers)
    return polished if polished else followups


def _llm_enhance_questions(followups: list[dict], answers: dict) -> list[dict]:
    """Ask the LLM to rephrase follow-up questions more naturally."""
    prompt = f"""Rephrase these follow-up questions for a friendly college-admissions
conversation with a high school student. Keep the same field IDs and reasons.
Return ONLY a JSON array, no prose.

Student's current answers:
{json.dumps(answers, indent=2, default=str)}

Questions to rephrase:
{json.dumps(followups, indent=2)}

Return exactly:
[{{"id":"<same>","text":"<rephrased>","reason":"<same>"}}]"""

    raw = chat([{"role": "user", "content": prompt}], max_tokens=500, temperature=0.3)
    if not raw:
        return []

    # Extract JSON array from the response.
    raw = raw.strip()
    for fence in ("```json", "```"):
        if raw.startswith(fence):
            raw = raw[len(fence):].lstrip()
    if raw.endswith("```"):
        raw = raw[:-3].strip()
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        result = json.loads(raw[start : end + 1])
        if isinstance(result, list) and all("id" in r and "text" in r for r in result):
            return result
    except json.JSONDecodeError:
        pass
    return []


# ---------------------------------------------------------------- profile

def summarize_profile(answers: dict) -> str:
    """Produce a one-paragraph human-readable summary of the student's profile."""
    prompt = f"""Summarize this college applicant's profile in 2-3 sentences for a college counselor:

{json.dumps(answers, indent=2, default=str)}

Focus on: academic strength, test status, location/residency constraints, budget, and intended majors."""

    raw = chat([{"role": "user", "content": prompt}], max_tokens=200, temperature=0.5)
    return raw or "(summary unavailable)"


# ---------------------------------------------------------------- rationale

def _clean_rationale(text: str) -> str:
    """Strip common reasoning-model thinking preamble from a rationale.

    Reasoning models sometimes start by restating the task before answering
    (e.g. "First, I need to write a concise rationale..."). Remove that noise.
    """
    if not text:
        return ""
    text = text.strip()
    # Drop leading phrases where the model restates the task rather than answering.
    for prefix in (
        "first, i need to",
        "first, i must",
        "first, let me",
        "first, the",
        "first, we",
        "first, i'll",
        "first, i will",
        "first, i should",
        "first, i want to",
        "first, i'd like to",
        "to write a",
        "let me write",
        "i need to write",
        "i need to create",
        "i need to produce",
        "i should write",
        "i should produce",
        "i will write",
        "i will create",
        "i will produce",
        "i'll write",
        "i'll create",
        "i'll produce",
    ):
        if text.lower().startswith(prefix):
            # Find the first sentence boundary after the preamble.
            for sep in (". ", "? ", "! "):
                idx = text.find(sep, len(prefix))
                if idx != -1:
                    return text[idx + 2:].strip()
            # No sentence boundary found — strip the preamble line.
            lines = text.split("\n", 1)
            return lines[1].strip() if len(lines) > 1 else text[len(prefix):].strip()
    return text


def generate_rationale(college: dict, profile: dict) -> str:
    """Generate a 2-3 sentence rationale for why this college fits (or doesn't)."""
    prompt = f"""You are a college admissions counselor. Write a concise 2-3 sentence rationale explaining why this college is a good or poor fit for this student.

STUDENT:
- GPA: {profile.get("gpa")}
- SAT/ACT: {profile.get("sat_total") or profile.get("act_total") or "no test score"}
- Home state: {profile.get("home_state")}
- Budget: ${profile.get("max_annual_cost", "unspecified")}
- Interested majors: {", ".join(profile.get("interested_cips", []) or ["unspecified"])}

COLLEGE:
- Name: {college.get("name")}
- Acceptance rate: {college.get("acceptance_rate")}
- SAT bar (mid-50 midpoint): {college.get("sat_bar")}
- Effective annual cost: ${college.get("effective_cost", "unknown")}
- Tier: {college.get("tier")}
- Fit score: {college.get("fit_score")}
- Application: RD {college.get("rd_deadline", "?")}, Early {college.get("early_deadline", "none")} ({college.get("ea_ed_type", "")})

Write the rationale focusing on academic fit, cost, and any notable factors. Be honest — if the college is a stretch, say so."""

    raw = chat([{"role": "user", "content": prompt}], max_tokens=500, temperature=0.5)
    return _clean_rationale(raw) or ""


def generate_list_summary(final, profile: dict) -> str:
    """Generate a short summary of the whole triaged list."""
    rows = []
    for _, r in final.iterrows():
        rows.append(
            f"- {r['name']} ({r['tier']}): AR={r.get('acceptance_rate')}, "
            f"fit={r.get('fit_score'):.2f}"
        )
    prompt = f"""You are a college admissions counselor. Write a 3-4 sentence summary of this student's college list.

STUDENT:
{json.dumps(profile, indent=2, default=str)}

LIST:
{chr(10).join(rows)}

Highlight the stretches, the likely reaches, and the solid safeties."""

    raw = chat([{"role": "user", "content": prompt}], max_tokens=250, temperature=0.5)
    return raw or ""
"""CrewAI task templates for discovery, research, and pitch phases."""

from __future__ import annotations

import json

from crewai import Agent, Task

from leadforge.config_loader import AppConfig, resolve_industries, resolve_location
from leadforge.models import DiscoveredLead


def discovery_task(
    agent: Agent,
    config: AppConfig,
) -> Task:
    location = resolve_location(config)
    industries = resolve_industries(config)
    zip_code = config.icp.get("zip_code", "")
    pain_signals = config.icp.get("pain_signals", [])
    gmin = config.guardrails.discovery_target_min
    gmax = config.guardrails.discovery_target_max
    local_goal = max(1, gmin // 2)
    remote_goal = max(1, gmin - local_goal)

    return Task(
        description=f"""
Find between {gmin} and {gmax} real US small businesses matching this ICP:
- Location focus: {location}
- Zip code anchor: {zip_code or "not provided; use location text"}
- Industries: {", ".join(industries)}
- Size: roughly 2–25 employees (owner-operated SMBs)
- Pain signals to prioritize: {", ".join(pain_signals)}

Search strategy (use web search tools only):
1. Return a balanced list:
   - At least {local_goal} LOCAL leads within roughly 100 miles of the zip/location.
   - At least {remote_goal} REMOTE-READY US leads from strong trade markets outside the local radius.
2. Prioritize trades in this order when available: HVAC, plumbing, electrical,
   roofing, garage doors, pest control, general contractors, auto repair.
3. Search directories, public maps-style listings, trade associations, websites,
   public reviews, "hiring dispatcher", "hiring office admin", "contact email",
   "[industry] near {zip_code or location}", and "[industry] scheduling reviews".
4. Prefer businesses with a verified website, phone number, and a public signal
   of admin strain, missed calls, scheduling friction, estimate delays, or weak online booking.
5. Source contact info only when public: business email, phone, LinkedIn company page,
   or public owner/manager LinkedIn snippets.

Output ONLY a JSON array (no markdown prose outside the array). Each object:
{{
  "company": "Business Name",
  "industry": "HVAC|plumbing|etc",
  "location": "City, ST",
  "lead_scope": "local|remote",
  "website": "https://... or null",
  "email": "public email or null",
  "phone": "public phone or null",
  "linkedin_url": "public LinkedIn URL or null",
  "pain_signals": ["specific public signal 1", "specific public signal 2"],
  "source_urls": ["url1", "url2"],
  "discovery_notes": "2-4 specific sentences explaining why they fit, what evidence was found, and source confidence"
}}

Do not invent businesses. If uncertain, lower confidence in notes and cite weak sources.
""",
        expected_output=f"A JSON array of {gmin}-{gmax} lead objects with the exact keys above.",
        agent=agent,
    )


def research_task(agent: Agent, lead: DiscoveredLead) -> Task:
    lead_json = lead.model_dump_json(indent=2)
    return Task(
        description=f"""
Research this single lead using PUBLIC data only (search + fetch_public_page):

{lead_json}

Tasks:
1. If website exists, fetch and summarize services, service area, team size hints,
   booking/contact flows, form friction, offers, and operational maturity.
2. Search public reviews/snippets. Extract themes around scheduling, communication,
   response time, estimates, billing, no-shows, emergency calls, and missed appointments.
3. Search owner/manager signals from About pages, LinkedIn snippets, local press,
   license records, BBB, chamber pages, public directory profiles, and state records.
4. Source contact info when public: business email, phone, contact page, LinkedIn URL.
   Never invent emails. If only a form exists, explain that in contact_notes.
5. Search recent news, posts, job listings, and review trends. Note hiring for
   dispatcher/admin, expansion, storm/seasonal demand, ownership changes, or weak follow-up.
6. Produce a sales-ready Barren fit analysis tying public facts to automation:
   missed-call capture, booking/intake, dispatch, estimate follow-up, invoicing,
   compliance paperwork, review follow-up, and owner time savings.

Output ONLY a JSON object:
{{
  "company": "...",
  "industry": "...",
  "location": "...",
  "lead_scope": "local|remote",
  "owner_name": "name or null",
  "owner_signals": ["how we inferred owner"],
  "contact_email": "email or null",
  "contact_phone": "phone or null",
  "linkedin_url": "LinkedIn URL or null",
  "contact_notes": "where contact info came from and confidence level",
  "website_summary": "detailed paragraph with services, booking/contact friction, and operational clues",
  "review_summary": "detailed paragraph with review themes and public evidence",
  "recent_news": ["recent public signal 1", "recent public signal 2"],
  "pains": ["specific pain1", "specific pain2", "specific pain3", "specific pain4"],
  "public_facts": ["fact1", "fact2", "fact3", "fact4"],
  "barren_fit_analysis": "5-8 persuasive, source-grounded sentences with concrete Barren value propositions",
  "source_urls": ["url1", "url2"]
}}

Make this useful to a salesperson tomorrow morning. Avoid generic language.
If evidence is weak, say exactly what is weak in contact_notes or public_facts.
""",
        expected_output="A single JSON object with research fields for this lead.",
        agent=agent,
    )


def pitch_task(
    agent: Agent,
    research_json: str,
    *,
    human_review: bool,
) -> Task:
    review_flag = "true" if human_review else "false"
    return Task(
        description=f"""
Using this research dossier, draft a Barren Business Development pitch.

Research:
{research_json}

Barren automates back-office: scheduling, invoicing, compliance paperwork — so owners
focus on trade work, not admin.

Output ONLY a JSON object:
{{
  "company": "...",
  "owner": "name or Unknown",
  "email": "public email or null",
  "phone": "public phone or null",
  "linkedin_url": "public LinkedIn URL or null",
  "industry": "...",
  "location": "...",
  "lead_scope": "local|remote",
  "pains": "detailed comma-separated pain summary with source-grounded specifics",
  "barren_fit": "6-9 specific, convincing sentences on why Barren fits this company",
  "draft_email": "300-500 words, natural, personalized, specific, compelling, one clear CTA",
  "pitch_angle": "one-line hook for calls",
  "specific_value_props": ["value prop 1", "value prop 2", "value prop 3", "value prop 4"],
  "recommended_next_step": "specific action before outreach",
  "confidence": "low|medium|high",
  "human_review_required": {review_flag},
  "research_sources": ["url1"]
}}

The email should read like a thoughtful operator wrote it after reviewing public facts.
Reference concrete facts from the dossier, connect them to Barren's back-office automation,
and show empathy for a trade owner balancing jobs, calls, estimates, invoices, and paperwork.
Do not overclaim, do not mention private knowledge, and do not pretend to have spoken with them.
Use one low-friction CTA. If owner is unknown, write to the owner/operator naturally.
""",
        expected_output="A single JSON object with pitch fields ready for CRM export.",
        agent=agent,
    )


def batch_pitch_task(
    agent: Agent,
    research_batch: list[dict],
    *,
    human_review: bool,
) -> Task:
    """Optional batch pitch for cost savings on small models."""
    review_flag = "true" if human_review else "false"
    return Task(
        description=f"""
For each research dossier below, output one pitch JSON object.
Return ONLY a JSON array with one object per lead.

Dossiers:
{json.dumps(research_batch, indent=2)}

Each object keys: company, owner, email, phone, linkedin_url, industry, location,
lead_scope, pains, barren_fit, draft_email, pitch_angle, specific_value_props,
recommended_next_step, confidence (low|medium|high), human_review_required ({review_flag}),
research_sources.

Keep each draft_email 300-500 words, source-grounded, natural, and truthful.
""",
        expected_output="JSON array of pitch objects, one per input dossier.",
        agent=agent,
    )

"""Demand-side prompts. The shop-side prompts in ``leadforge/tasks.py`` stay put.

Three prompts, one per graph phase, all pointed at homeowner demand rather than
at trades businesses:

- ``demand_discovery_prompt`` — find property owners who publicly asked for work.
- ``demand_research_prompt`` — understand the *request*, not the person.
- ``demand_pitch_prompt`` — draft the reply a shop could send the homeowner.

Each returns text that instructs the exact JSON keys of the reused
``leadforge.models`` contracts, so the graph keeps parsing into
``DiscoveredLead``, ``LeadResearch``, and ``PitchOutput`` and the CSV contract
never forks. The mapping onto those business-shaped fields is spelled out in
every prompt: ``company`` carries a household label, ``owner`` the posting
name, ``pains`` the request itself.
"""

from __future__ import annotations

from leadforge.config_loader import AppConfig, resolve_industries, resolve_location
from leadforge.models import DiscoveredLead

TRADES = ("HVAC", "plumbing", "electrical", "roofing")


def _trade_list(config: AppConfig) -> str:
    configured = [
        trade
        for trade in resolve_industries(config)
        if trade.strip().lower() in {t.lower() for t in TRADES}
    ]
    return ", ".join(configured or TRADES)


def demand_discovery_prompt(config: AppConfig) -> str:
    """Find homeowners who publicly asked for trade work near a zip."""
    location = resolve_location(config)
    zip_code = config.icp.get("zip_code", "")
    guardrails = config.guardrails
    low, high = guardrails.discovery_target_min, guardrails.discovery_target_max

    return f"""
Find between {low} and {high} homeowners or property owners who have PUBLICLY
asked for trade work near {location}{f" (zip anchor {zip_code})" if zip_code else ""}.

You are looking for demand, not suppliers. A result is only valid if a property
owner has publicly said they need work done.

Trades in scope: {_trade_list(config)}.

VALID public sources:
- Public community board and neighbourhood posts asking for a recommendation
  or for someone available to do the work.
- Public question-and-answer threads describing a failure and asking what to do.
- Public reviews or comments that describe a live unresolved problem.
- Public permit filings that show work planned or underway at a property.
- Public posts that mention weather damage to a property.

NOT valid, and must never appear in your output:
- Trades businesses, contractors, shops, or their websites. If the subject sells
  the service rather than needs it, discard it.
- Business directories, listing sites, aggregator profiles, or lead-broker pages.
- Anything behind a login, paywall, private group, or membership wall.
- Any customer list, CRM record, or purchased data. You have no access to these
  and must not pretend to.
- Contact details that were not published by the person themselves.

Weather is context, not demand. A storm or freeze in the area supports a lead
that already has a public request behind it. It is never a lead on its own.

For each homeowner, output ONE object. The keys are shaped for a shared export
contract, so map them exactly as described:

{{
  "company": "a household label, e.g. 'Household — J. Doe (85001)'. Never a business name",
  "industry": "the trade needed: {_trade_list(config)}",
  "location": "City, ST ZIP",
  "lead_scope": "local",
  "website": null,
  "email": "only if the person published it themselves, else null",
  "phone": "only if the person published it themselves, else null",
  "linkedin_url": null,
  "pain_signals": ["the public request, quoted or closely paraphrased", "any corroborating public signal"],
  "source_urls": ["the public URL each signal came from"],
  "discovery_notes": "2-4 sentences: what work is needed, how urgent it reads, how many separate public signals agree, and how confident the sourcing is"
}}

Output ONLY a JSON array of these objects, with no prose outside the array.

Do not invent people, posts, or URLs. A lead with weak sourcing should say so
in discovery_notes rather than be dressed up. Fewer real leads beats more
invented ones.
""".strip()


def demand_research_prompt(lead: DiscoveredLead) -> str:
    """Understand one public request. Do not profile the person who made it."""
    return f"""
Research this ONE public request for trade work, using PUBLIC sources only.

{lead.model_dump_json(indent=2)}

This subject is a private individual, not a business. That changes what you are
allowed to do:

DO look for:
- What work is being asked for, in the person's own public words.
- How urgent it reads — emergency, this week, planning ahead.
- Whether other public signals describe the same problem, and how recent.
- Public property context: property type, approximate age or size if a public
  permit or listing states it, prior permitted work on the property.
- Weather or seasonal conditions in the area that make the request plausible.
- Whether the request already appears resolved, which would make it stale.

DO NOT, under any circumstances:
- Build a personal profile. No employer, income, family, household members,
  social accounts, or anything about the person beyond the request itself.
- Search for contact details the person did not publish alongside the request.
- Use any source behind a login, paywall, or private group.
- Infer or invent an email address or phone number.

Output ONLY a JSON object:
{{
  "company": "the same household label you were given",
  "industry": "the trade needed",
  "location": "City, ST ZIP",
  "lead_scope": "local",
  "owner_name": "the posting name exactly as published, or null",
  "owner_signals": ["how the posting name was published"],
  "contact_email": "only if the person published it, else null",
  "contact_phone": "only if the person published it, else null",
  "linkedin_url": null,
  "contact_notes": "where the contact route came from, or that only the public post exists",
  "website_summary": "the public property context you found, or that none is public",
  "review_summary": "how the request reads: urgency, scope, and any corroborating public signal",
  "recent_news": ["recent public signal about this request or the local conditions"],
  "pains": ["the specific problem stated", "knock-on problems the person mentioned"],
  "public_facts": ["fact drawn from a public source", "another"],
  "barren_fit_analysis": "3-6 sentences on why a local shop in this trade is a good match for THIS job: scope, urgency, and what a first visit would need to cover",
  "source_urls": ["url1", "url2"]
}}

If the request looks stale or already resolved, say so plainly in
review_summary. A stale lead wastes a dispatcher's morning.
""".strip()


def demand_pitch_prompt(research_json: str, *, human_review: bool = True) -> str:
    """Draft the message the SHOP would send the homeowner."""
    review_flag = "true" if human_review else "false"
    return f"""
Draft the message a local trade shop would send to this homeowner in response
to their public request.

You are writing AS the shop, TO the homeowner. Not the other way round, and not
about software.

Research:
{research_json}

The message must:
- Open by referencing the public post plainly and without being creepy. The
  person posted in public; say where you saw it and move on.
- Show you read the actual problem. Name the specific failure they described.
- Say what a first visit would cover and roughly how the work usually goes.
- Give one clear, low-friction next step — a time window, or a reply to confirm.
- Stay 120-200 words. A homeowner with a leaking water heater will not read 500.
- Be plain and human. No hype, no pressure, no fake scarcity, no invented
  credentials, discounts, or guarantees.

The message must NOT:
- Claim prior contact, a referral, or a relationship that does not exist.
- Mention any information that was not public.
- Promise a price. An estimate needs a look at the job first.

Output ONLY a JSON object:
{{
  "company": "the household label",
  "owner": "the homeowner's posting name, or Unknown",
  "email": "public email or null",
  "phone": "public phone or null",
  "linkedin_url": null,
  "industry": "the trade needed",
  "location": "City, ST ZIP",
  "lead_scope": "local",
  "pains": "the problem as stated, comma separated and specific",
  "barren_fit": "4-6 sentences on why this shop suits this job: trade match, urgency fit, and what the first visit covers",
  "draft_email": "the 120-200 word message to the homeowner, one clear next step",
  "pitch_angle": "one line a dispatcher could say on the phone",
  "specific_value_props": ["concrete thing the shop does for this job", "another", "another"],
  "recommended_next_step": "what a human should verify before this goes out",
  "confidence": "low|medium|high",
  "human_review_required": {review_flag},
  "research_sources": ["url1"]
}}

This is a draft for a human to read, edit, and decide on. Nothing is sent
automatically. Write it as if the shop owner will read it before the homeowner
ever does, because they will.
""".strip()

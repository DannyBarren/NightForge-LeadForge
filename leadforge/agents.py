"""CrewAI agent definitions for LeadForge overnight pipeline."""

from __future__ import annotations

from crewai import Agent

from leadforge.config_loader import AppConfig, resolve_industries, resolve_location
from leadforge.llm_factory import build_llm
from leadforge.tools import build_research_tools, build_search_tools


def lead_discovery_agent(config: AppConfig) -> Agent:
    """Finds US trades/SMB leads matching ICP via public web search."""
    location = resolve_location(config)
    industries = ", ".join(resolve_industries(config))
    pains = ", ".join(config.icp.get("pain_signals", []))
    value_prop = config.project.get("value_prop", "").strip()

    return Agent(
        role="Lead Discovery Specialist",
        goal=(
            f"Discover {config.guardrails.discovery_target_min}-"
            f"{config.guardrails.discovery_target_max} real US trades businesses "
            f"across a local radius around {location} plus high-quality remote-ready national leads "
            f"({industries}) showing admin/back-office pain signals."
        ),
        backstory=(
            "You are a B2B prospecting researcher for Barren Business Development. "
            "You only use public web search — business listings, reviews, job posts, "
            "and company websites. You never scrape behind logins or violate site terms. "
            f"Barren helps owners by: {value_prop} "
            "Target owners drowning in scheduling, invoicing, compliance paperwork, "
            f"missed calls, and estimate follow-up. Prioritized pains: {pains}. "
            "Capture public contact details and source URLs when available."
        ),
        tools=build_search_tools(config),
        llm=build_llm(config),
        verbose=True,
        allow_delegation=False,
        max_iter=10,
        max_rpm=10,
    )


def research_agent(config: AppConfig) -> Agent:
    """Researches one lead: website, reviews, owner signals — public data only."""
    return Agent(
        role="SMB Research Analyst",
        goal=(
            "Gather public facts about one trades/SMB lead: owner name signals, "
            "contact details, review themes, website capabilities, recent signals, "
            "and operational pain points."
        ),
        backstory=(
            "You compile ethical, public-only dossiers for outbound strategy. "
            "Use search for reviews and news; use fetch_public_page for the company site. "
            "Note hiring for admin/dispatcher, scheduling complaints, manual processes, "
            "missing online booking, poor response-time reviews, missed-call signals, "
            "and contact pages. Every conclusion should help a salesperson prioritize outreach. "
            "Cite source URLs."
        ),
        tools=build_research_tools(config),
        llm=build_llm(config),
        verbose=True,
        allow_delegation=False,
        max_iter=8,
        max_rpm=12,
    )


def pitch_strategist_agent(config: AppConfig) -> Agent:
    """Drafts personalized Barren pitch from research dossier."""
    value_prop = config.project.get("value_prop", "").strip()

    return Agent(
        role="Barren Pitch Strategist",
        goal=(
            "Turn research into a personalized Barren pitch: hook, email draft, "
            "and angle focused on automating back-office so owners focus on trade work."
        ),
        backstory=(
            "You write respectful, detailed cold outreach for US trade business owners. "
            "No hype, no false claims — tie pains to Barren's automation value. "
            f"Core value: {value_prop} "
            "Emails should feel researched and natural, usually 300-500 words, "
            "with one clear CTA and multiple specific public signals from research."
        ),
        tools=[],  # synthesis only — keeps cost low
        llm=build_llm(config),
        verbose=True,
        allow_delegation=False,
        max_iter=4,
        max_rpm=10,
    )

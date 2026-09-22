"""Demand-side discovery: find the homeowner who needs the work.

The CrewAI prototype in ``leadforge/`` finds trades *shops* to sell software to.
This package points the same machinery the other way: public posts from
property owners who have said, out loud and in public, that they need HVAC,
plumbing, electrical, or roofing work done.

That flip changes what "research" means, and the prompts here reflect it. A
business is a fair subject for a dossier. A private individual who posted that
their water heater is leaking is not. The prompts ask about the *request* — the
trade, the urgency, the property — and explicitly refuse to compile a personal
profile of the person who made it.

Boundaries:

- Public posts only. No login walls, no CRM pull, no purchased lists.
- Weather is a flag on a signal, never a source of demand on its own.
- ``weather.py`` is a fixture. No NOAA, no OpenWeather, no HTTP at all.
- Nothing here contacts anyone. The pitch stage drafts a message a shop could
  send, and a human decides whether it ever goes out.
"""

from __future__ import annotations

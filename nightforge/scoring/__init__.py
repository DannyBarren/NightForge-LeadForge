"""Lead scoring — the spine, not the model.

Signals in, ranked and explained leads out. Nothing here is learned: the bands
come from a handful of written-down rules in ``heuristic``, and the assigner
reads a small in-memory roster. That is deliberate. A rule you can read in the
``reasons`` list is auditable by the person who has to act on the lead; a tree
is not, and there is no labelled history to train one on yet.

Boundaries this package holds:

- It never writes to a vendor, never touches a calendar, and never sends
  anything. The most it does is suggest a zone and a unit.
- ``human_review_required`` is ``True`` on every scored lead and cannot be set
  false.
- It does not fork ``PitchOutput`` or touch ``EXPORT_COLUMNS``. Research export
  and lead scoring are separate contracts.
"""

from __future__ import annotations

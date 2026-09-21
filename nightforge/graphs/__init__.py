"""LangGraph graphs for NightForge.

Kept import-light on purpose: importing this package must not pull LangGraph or
any vendor SDK. Import the specific graph module you need.

Graphs never import a Jobber, Housecall Pro, or other vendor SDK directly. They
talk to the interface in ``nightforge.adapters.base`` so the vendor stays
swappable and testable.
"""

from __future__ import annotations

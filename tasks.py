"""Root shim — see leadforge/tasks.py for implementations."""

from leadforge.tasks import batch_pitch_task, discovery_task, pitch_task, research_task

__all__ = ["discovery_task", "research_task", "pitch_task", "batch_pitch_task"]

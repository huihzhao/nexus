"""Memory module — DPM (Deterministic Projection Memory).

EventLog: append-only event log (SQLite + FTS5, syncs to Greenfield)
CuratedMemory: derived view (MEMORY.md + USER.md)
EventLogCompactor: auto-compact event log → CuratedMemory

Architecture based on: "Stateless Decision Memory for Enterprise AI Agents"
(arXiv:2604.20158)
"""

from .event_log import EventLog, Event
from .curated import CuratedMemory
from .compactor import EventLogCompactor

__all__ = ["EventLog", "Event", "CuratedMemory", "EventLogCompactor"]

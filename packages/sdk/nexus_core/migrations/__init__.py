"""One-shot migrations for SDK schema changes.

Modules here are designed to run once per agent_id. They are
**idempotent** via flag files so re-running the migration after
success is a no-op.

Currently:

* :mod:`memory_to_facts` — Phase D 续 #2: convert legacy
  ``MemoryProvider`` entries into typed ``Fact`` rows.
"""

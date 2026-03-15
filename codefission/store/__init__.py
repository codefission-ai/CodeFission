"""store — data access layer (DB, git, AI subprocess, OS processes).

Low-level operations that touch external systems. Each file owns one
concern: trees.py (SQL), settings.py (SQL), git.py (git commands),
ai.py (agentbridge), actions.py (audit log), processes.py (OS PIDs),
summary.py (auto-naming).

store/ is called by orchestrator/. Never calls orchestrator/ or handlers/.
"""

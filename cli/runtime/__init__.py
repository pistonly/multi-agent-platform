"""CLI runtime helpers — sub-app-free runtime code shared by command modules.

Hosts the experiment-execution lock manager (`run_lock`) plus any future
runtime-side helpers that don't belong to a single resource sub-app.
"""

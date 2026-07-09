"""cli.commands — sub-app command modules (arch experiment 0519e2a3 PR3+).

Each module in this package owns one Typer ``*_app`` sub-application
(``agent_app``, ``topic_app``, etc.) and registers its commands. The
parent ``cli/main.py`` only does ``app.add_typer(..., name=...)`` to
expose them under the user-visible ``map <name> ...`` path.

Helpers (``_run``, ``_client_ctx``, ``_print_json``, ``_cli_options``)
live in ``cli/main.py`` and are imported lazily inside command bodies
to keep ``cli.main`` importable without instantiating the sub-apps
(avoids the circular import: ``cli.main → cli.commands.agent →
cli.main._run``).
"""

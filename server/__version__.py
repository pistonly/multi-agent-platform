"""Single source of truth for the server package version.

eng experiment (55634575) PR7: this module exists so ``server.main``,
the SDK, and CLI all read the same string. ``pyproject.toml`` is the
release-of-record (it's what ``uv build`` ships), but Python imports
can't easily read TOML, so we mirror the version here and pin the
invariant with a unit test (``test_eng_version_single_source``).

Release workflow: bump this string + the ``[project] version = ...``
line in ``pyproject.toml`` in the same commit. The test fails if the
two drift.
"""

__version__ = "0.16.1"

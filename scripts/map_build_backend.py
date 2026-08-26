"""PEP 517 backend: copy alembic into server/_migrate, then delegate to setuptools.

T29: setuptools package-data only ships files inside a Python package. The
canonical alembic tree lives at the repo root (so ``alembic upgrade`` from a
checkout / Docker image keeps working, and so we never add alembic/__init__.py
which would shadow the third-party ``alembic`` package). This hook copies that
tree into ``server/_migrate`` immediately before sdist/wheel so the files land
in the wheel next to ``server/web_dist``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import build_meta as _backend

# PEP 517 backend-path is ``scripts/``; repo root is its parent.
_ROOT = Path(__file__).resolve().parent.parent


def _prepare_wheel_assets() -> None:
    dest = _ROOT / "server" / "_migrate"
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_ROOT / "alembic.ini", dest / "alembic.ini")
    alembic_dest = dest / "alembic"
    if alembic_dest.exists():
        shutil.rmtree(alembic_dest)
    shutil.copytree(
        _ROOT / "alembic",
        alembic_dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def build_sdist(sdist_directory: str, config_settings=None):
    _prepare_wheel_assets()
    return _backend.build_sdist(sdist_directory, config_settings)


def build_wheel(wheel_directory: str, config_settings=None, metadata_directory=None):
    _prepare_wheel_assets()
    return _backend.build_wheel(wheel_directory, config_settings, metadata_directory)


def prepare_metadata_for_build_wheel(metadata_directory: str, config_settings=None):
    return _backend.prepare_metadata_for_build_wheel(metadata_directory, config_settings)


def get_requires_for_build_wheel(config_settings=None):
    return _backend.get_requires_for_build_wheel(config_settings)


def get_requires_for_build_sdist(config_settings=None):
    return _backend.get_requires_for_build_sdist(config_settings)


def get_requires_for_build_editable(config_settings=None):
    getter = getattr(_backend, "get_requires_for_build_editable", None)
    if getter is None:
        return []
    return getter(config_settings)


def prepare_metadata_for_build_editable(metadata_directory: str, config_settings=None):
    prepare = getattr(_backend, "prepare_metadata_for_build_editable", None)
    if prepare is None:
        raise AttributeError("setuptools build_meta has no prepare_metadata_for_build_editable")
    return prepare(metadata_directory, config_settings)


def build_editable(wheel_directory: str, config_settings=None, metadata_directory=None):
    # Editable installs use the repo-root alembic.ini; skip the copy so a
    # develop checkout does not materialize a duplicate tree under server/.
    build = getattr(_backend, "build_editable", None)
    if build is None:
        raise AttributeError("setuptools build_meta has no build_editable")
    return build(wheel_directory, config_settings, metadata_directory)

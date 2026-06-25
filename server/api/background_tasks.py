from __future__ import annotations

from contextvars import ContextVar

from fastapi import BackgroundTasks

_current_background_tasks: ContextVar[BackgroundTasks | None] = ContextVar(
    "current_background_tasks",
    default=None,
)


def bind_background_tasks(background_tasks: BackgroundTasks) -> None:
    _current_background_tasks.set(background_tasks)


def get_background_tasks() -> BackgroundTasks | None:
    return _current_background_tasks.get()

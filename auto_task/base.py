"""Common utilities for registering automation tasks."""

from __future__ import annotations

from typing import Any, Dict, Type


TASK_REGISTRY: Dict[str, Type["BaseAutoTask"]] = {}


def register_task(task_type: str):
    """Decorator used by tasks to register themselves in the registry."""

    def _decorator(cls: Type["BaseAutoTask"]):
        if task_type in TASK_REGISTRY:
            raise ValueError(f"Task type '{task_type}' already registered")
        cls.task_type = task_type
        TASK_REGISTRY[task_type] = cls
        return cls

    return _decorator


class BaseAutoTask:
    """Base class describing the interface of a background task."""

    task_type = "base"

    def __init__(self, payload: Any, raw_task: Dict[str, Any]):
        self.payload = payload or {}
        self.raw_task = raw_task or {}

    def run(self) -> Any:
        """Execute the task and optionally return a JSON-serializable result."""

        raise NotImplementedError


class TaskDeletedError(Exception):
    """Raised when a task was deleted during processing."""


__all__ = ["BaseAutoTask", "register_task", "TASK_REGISTRY", "TaskDeletedError"]

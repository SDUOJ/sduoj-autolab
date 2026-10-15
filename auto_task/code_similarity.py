"""Compatibility registration for the durable JPlag workflow."""

from __future__ import annotations

import logging
from typing import Any, Dict

from .base import BaseAutoTask, register_task

logger = logging.getLogger(__name__)


@register_task("code_similarity")
class CodeSimilarityTask(BaseAutoTask):
    """Execute JPlag within an already claimed durable task."""

    def run(self) -> Dict[str, Any]:
        from .entities import TaskJob
        from .runtime import Context
        from .plagiarism import scan
        from .store import transaction
        with transaction() as session:
            row = session.query(TaskJob).filter_by(id=self.raw_task["task_id"], status="running").one()
            job = {column.name: getattr(row, column.name) for column in TaskJob.__table__.columns}
        return scan(Context(job), self.payload)


__all__ = ["CodeSimilarityTask"]

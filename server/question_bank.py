"""Batch intake writes directly to the existing OJ question/group tables."""
from typing import List
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from auth import cover_header, manager
from ser.auto_task_ai import Question
from auto_task.store import transaction
from auto_task.native_bank import save_batch
from utils import api_response

router = APIRouter(prefix="/question-bank", tags=["question-bank"])


class BatchInput(BaseModel):
    title: str = Field(..., min_length=1, max_length=55)
    operation_key: str = Field(..., min_length=1, max_length=96)
    questions: List[Question] = Field(..., min_items=1, max_items=200)
    score: int = Field(10, ge=0, le=10000)


@router.post("/batches")
@api_response()
def create_batch(body: BatchInput, user=Depends(cover_header)):
    manager(user)
    with transaction() as session:
        return save_batch(session, user, body.title, body.operation_key,
                          [q.dict() for q in body.questions], body.score)


@router.get('/groups/{gid}/questions')
@api_response()
def group_questions(gid: int, user=Depends(cover_header)):
    import json
    from fastapi import HTTPException
    from auth import is_manager
    from db import ProblemGroup
    from auto_task.native_bank import read_native
    with transaction() as session:
        group = session.query(ProblemGroup).filter_by(gid=gid).first()
        if not group:
            raise HTTPException(404, '题组不存在')
        is_manager(group, user)
        if group.type not in (0, 1):
            raise HTTPException(400, '编程题请使用编程题页面')
        rows = []
        for entry in json.loads(group.problemInfo):
            binding = {'type': 'objective' if group.type == 0 else 'subjective', 'pid': str(entry['pid']), 'group_id': gid}
            source = read_native(session, binding)
            rows.append({'pid': entry['pid'], 'score': entry.get('score'), 'question': source[1]})
        return {'gid': gid, 'name': group.name, 'type': group.type, 'rows': rows}

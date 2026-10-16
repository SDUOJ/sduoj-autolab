"""Owner-scoped management conversations; all OJ calls require explicit confirmation."""
import json
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from auth import cover_header
from utils import api_response
from auto_task.entities import AiResource, uid
from auto_task.store import transaction, dumps
from auto_task.resources import require_admin
from auto_task import management_agent as agent

router = APIRouter(prefix='/management-agent', tags=['management-agent'])

class NewSession(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)
    preset: str
    instructions: str = Field('', max_length=12000)

class Message(BaseModel):
    text: str = Field(..., min_length=1, max_length=30000)

class Decision(BaseModel):
    action_hash: str = Field(..., min_length=64, max_length=64)
    approve: bool

@router.get('/presets')
@api_response()
def presets(user=Depends(cover_header)):
    require_admin(user)
    return [{'id': key, 'title': title, 'instructions': (agent.GUIDES / (key + '.md')).read_text()} for key, title in agent.PRESETS.items()]

@router.get('/sessions')
@api_response()
def sessions(user=Depends(cover_header)):
    require_admin(user)
    with transaction() as s:
        rows = s.query(AiResource).filter_by(kind='management_session', owner_id=str(user['userId']), deleted=0).order_by(AiResource.updated_at.desc()).limit(200).all()
        return [{'id': row.id, 'title': json.loads(row.data)['title'], 'state': agent.session_view(s, row)['state'],
                 'updated_at': row.updated_at.isoformat()} for row in rows]

@router.post('/sessions')
@api_response()
def create(body: NewSession, user=Depends(cover_header)):
    require_admin(user)
    if body.preset not in agent.PRESETS: raise HTTPException(422, '未知预设')
    instructions = (agent.GUIDES / (body.preset + '.md')).read_text() + '\n管理员补充说明：\n' + body.instructions
    with transaction() as s:
        row = AiResource(id=uid(), kind='management_session', owner_id=str(user['userId']), owner_username=user['username'],
                         state='ready', data=dumps({'title': body.title, 'preset': body.preset, 'instructions': instructions, 'events': [], 'actions': [], 'plan': []}))
        s.add(row); s.flush(); return agent.view(row)

@router.get('/sessions/{session_id}')
@api_response()
def detail(session_id: str, user=Depends(cover_header)):
    with transaction() as s:
        row = agent.owned(s, user, session_id)
        result = agent.view(row)
        return agent.session_view(s, row)

@router.post('/sessions/{session_id}/messages')
@api_response()
def message(session_id: str, body: Message, user=Depends(cover_header)):
    agent.start_turn(session_id, user, body.text)
    return {'started': True}

@router.post('/sessions/{session_id}/actions/{action_id}')
@api_response()
def decision(session_id: str, action_id: str, body: Decision, request: Request, user=Depends(cover_header)):
    return agent.decide(session_id, user, action_id, body.action_hash, body.approve, request.headers.get('cookie', ''))

@router.post('/sessions/{session_id}/stop')
@api_response()
def stop(session_id: str, user=Depends(cover_header)):
    agent.stop_turn(session_id, user)
    return {'stopped': True}

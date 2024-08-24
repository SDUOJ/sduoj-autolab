import os

from fastapi import APIRouter, Depends
from starlette.responses import FileResponse

from model.answer_sheet import answerSheetModel
from ser.answer_sheet import routerTypeBaseWithUsername, ser_preview
from ser.problem_set import ser_problem_set_summary
from utils import makeResponse

router = APIRouter(
    prefix="/test"
)


@router.get("/getInfo")
async def get_info():
    return FileResponse('output.txt', filename="output.txt", media_type="text/plain")

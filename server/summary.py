from fastapi import APIRouter, Depends

from model.answer_sheet import answerSheetModel
from ser.answer_sheet import routerTypeBaseWithUsername, ser_preview
from ser.problem_set import ser_problem_set_summary
from utils import makeResponse

router = APIRouter(
    prefix="/summary"
)


@router.post("/summary")
async def summary(data: dict = Depends(ser_problem_set_summary)):
    db = answerSheetModel()
    return makeResponse(
        await db.get_all_progress_cache(data["psid"], data["code"]))


@router.post("/preview")
async def preview(data: routerTypeBaseWithUsername = Depends(ser_preview)):
    db = answerSheetModel()
    return makeResponse(await db.get_rank_preview_info(data))

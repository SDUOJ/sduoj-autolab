from fastapi import APIRouter
from starlette.responses import StreamingResponse

router = APIRouter(
    prefix="/test"
)


def iter_file(file_path):
    with open(file_path, "rb") as file_like:
        yield from file_like


@router.get("/getInfo")
async def get_info():
    file_path = "output.txt"
    return StreamingResponse(iter_file(file_path), media_type="text/plain")

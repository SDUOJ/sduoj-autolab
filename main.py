from datetime import datetime

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from server import answer_sheet, objective, \
    problem_set, problem_group, subjective, subjective_judge, summary, screen_record,sign_in_record
from utilsTime import getMsTime


app = FastAPI()
app.include_router(objective.router)
app.include_router(answer_sheet.router)
app.include_router(problem_set.router)
app.include_router(problem_group.router)
app.include_router(subjective.router)
app.include_router(subjective_judge.router)
app.include_router(summary.router)
app.include_router(screen_record.router)
app.include_router(sign_in_record.router)


origins = [
    "*"
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # 允许的源列表
    allow_credentials=True,  # 允许返回 cookies
    allow_methods=["*"],  # 允许所有 HTTP 方法
    allow_headers=["*"],  # 允许所有 HTTP 头
)


@app.exception_handler(HTTPException)  # 自定义HttpRequest 请求异常
async def http_exception_handle(request, exc):
    response = JSONResponse({
        "code": exc.status_code,
        "message": str(exc.detail),
        "data": None,
        "timestamp": getMsTime(datetime.now())
    }, status_code=exc.status_code)
    return response


@app.exception_handler(RequestValidationError)
async def request_validatoion_error(request, exc):
    try:
        message = str(exc.detail)
    except:
        try:
            message = str(exc.raw_errors[0].exc)
        except:
            message = "请求错误"
    response = JSONResponse({
        "code": 400,
        "message": message,
        "data": None,
        "timestamp": getMsTime(datetime.now())
    }, status_code=400)
    return response


@app.exception_handler(Exception)
async def request_validatoion_error(request, exc):
    if str(exc) == "未登录":
        response = JSONResponse({
            "code": 403,
            "message": "未登录",
            "data": None,
            "timestamp": getMsTime(datetime.now())
        }, status_code=500)
    else:
        response = JSONResponse({
            "code": 500,
            "message": "内部错误",
            "data": None,
            "timestamp": getMsTime(datetime.now())
        }, status_code=500)
    return response


@app.on_event("startup")
async def startup():
    from utils import init_redis
    init_redis()
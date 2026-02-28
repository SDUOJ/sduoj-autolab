from datetime import datetime

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from server import answer_sheet, objective, \
    problem_set, problem_group, subjective, subjective_judge, summary, screen_record, test, problem_set_late, auto_task, \
    course, course_schedule, seat_binding, attendance, classroom
from utilsTime import getMsTime
from auto_task import start_background_worker


app = FastAPI()
app.include_router(objective.router)
app.include_router(answer_sheet.router)
app.include_router(problem_set.router)
app.include_router(problem_set_late.router)
app.include_router(problem_group.router)
app.include_router(subjective.router)
app.include_router(subjective_judge.router)
app.include_router(summary.router)
app.include_router(screen_record.router)
app.include_router(test.router)
app.include_router(auto_task.router)

# v3.0 新增路由
app.include_router(course.router)
app.include_router(course_schedule.router)
app.include_router(seat_binding.router)
app.include_router(attendance.router)
app.include_router(classroom.router)

# 已移除全局 CORS 中间件，避免自动添加 Access-Control-Allow-Origin 头。


def _assert_http_method_policy() -> None:
    """
    统一方法约束：仅允许 GET/POST（以及 GET 自动附带的 HEAD）。
    """
    allowed_methods = {"GET", "POST", "HEAD"}
    violations = []
    for route in app.routes:
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        invalid = set(methods) - allowed_methods
        if invalid:
            path = getattr(route, "path", str(route))
            violations.append(f"{path}: {sorted(invalid)}")
    if violations:
        raise RuntimeError(
            "HTTP method policy violation: only GET/POST are allowed.\n"
            + "\n".join(violations)
        )


_assert_http_method_policy()


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
    start_background_worker()

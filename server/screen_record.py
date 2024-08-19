import mimetypes
import os
import shutil
from datetime import datetime
from io import BytesIO

from PIL import Image
from fastapi import APIRouter, Form, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
from moviepy.editor import ImageSequenceClip

from model.problem_set import problemSetModel
from model.screen_record import screenRecordModel
from ser.screen_record import newRecord, ResponseModel, VideoList, PSList, NormalResponse

router = APIRouter(
    prefix="/screen_record"
)
@router.post("/addRecord")
async def addRecord(data: newRecord):
    db = screenRecordModel()
    db2 = problemSetModel()

    result = db.get_path_by_token(data.token)
    if result is not None:
        print("视频已存在")
        return NormalResponse(code=0, message="记录已经存在")

    res = await db2.ps_get_info_by_id(data.bs_id)
    datas = {
        'bs_type': res["type"],
        'bs_id': data.bs_id,
        'v_path': "D:\\SDUOJ\\ScreenRecord\\" + data.u_name + "_" + str(datetime.now().strftime("%Y%m%d%H%M%S")) + "\\",
        'u_id': data.u_id,
        'u_name': data.u_name,
        'token': data.token,
        'start_time': datetime.now(),
        'modify_time': datetime.now(),
        'cnt_frame': 0
    }
    db = screenRecordModel()
    db.add_record(datas)
    return NormalResponse(code=0, message="记录添加成功")


@router.post("/addFrame")
async def addFrame(token: str = Form(...), pic: UploadFile = File(...)):
    db = screenRecordModel()

    result = db.get_path_by_token(token)
    if result is None:
        return NormalResponse(code=404, message="无此视频记录")

    path = result.v_path
    cnt = result.cnt_frame
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if cnt == -1:
        return NormalResponse(code=404, message="记录已锁定")

    # 读取上传的图片
    image_bytes = await pic.read()
    image = Image.open(BytesIO(image_bytes))

    # 指定目标图片尺寸
    target_size = (1280, 720)
    # 调整图片尺寸
    image = image.resize(target_size)

    # 构建图片文件名，例如 "1.jpg"
    frame_filename = f"{cnt}.jpg"
    frame_path = os.path.join(os.path.dirname(path), frame_filename)  # 确保路径是文件夹路径，不是文件路径

    # 保存调整尺寸后的图片
    image.save(frame_path)

    # 更新最后确认到数据库
    db.add_frame_by_token(token, {'modify_time': datetime.now(), 'cnt_frame': cnt+1})

    print(f"Frame added successfully: {frame_path}")
    return NormalResponse(code=0, message="追加帧成功")


@router.get("/getPSList")
async def getPSList():
    db = screenRecordModel()
    result = db.get_ps_list()
    if not result:
        return NormalResponse(code=0, message="无录屏题单记录")

    ps_list = [
        PSList(
            psid=problem_set.psid,
            name=problem_set.name,
            description=problem_set.description,
            tm_start=problem_set.tm_start.strftime("%Y-%m-%d %H:%M:%S") if problem_set.tm_start else None,
            tm_end=problem_set.tm_end.strftime("%Y-%m-%d %H:%M:%S") if problem_set.tm_end else None,
            groupId=problem_set.groupId,
            tag=problem_set.tag
        )
        for screen_record, problem_set in result
    ]

    return ResponseModel(code=0, message="获取题单列表成功", data=ps_list)

@router.get("/getVideoList")
async def getVideoList(bs_id: int):
    data = bs_id
    db = screenRecordModel()
    result = db.get_video_list(data)
    if not result:
        return NormalResponse(code=404, message="无视频记录")

    video_list = [
        VideoList(
            u_id=video.u_id,
            u_name=video.u_name,
            token=video.token,
            start_time=video.start_time.strftime("%Y-%m-%d %H:%M:%S") if video.start_time else None,
            modify_time=video.modify_time.strftime("%Y-%m-%d %H:%M:%S") if video.modify_time else None,
        )
        for video in result
    ]

    return ResponseModel(code=0, message="获取视频列表成功", data=video_list)

@router.get("/getVideo")
async def getVideo(token: str):
    db = screenRecordModel()
    result = db.get_path_by_token(token)
    path = result.v_path
    cnt = result.cnt_frame

    folder_name = os.path.basename(path)
    video_path = os.path.join(path, folder_name + '_created.mp4')

    # 检查视频文件是否已存在
    if os.path.isfile(video_path):
        return FileResponse(video_path, media_type='video/mp4', filename=os.path.basename(video_path))

    images_folder = os.path.dirname(path)
    images = [os.path.join(images_folder, f"{i}.jpg") for i in range(cnt)]

    clip = ImageSequenceClip(images, fps=1)
    clip.write_videofile(video_path, codec='libx264')

    if not os.path.isfile(video_path):
        return NormalResponse(code=404, message="视频文件创建失败")

    return FileResponse(video_path, media_type='video/mp4', filename=os.path.basename(video_path))

@router.get("/createLockVideo")
async def createLockVideo(token: str):
    db = screenRecordModel()
    result = db.get_path_by_token(token)
    path = result.v_path
    cnt = result.cnt_frame

    if cnt == -1:
        return NormalResponse(code=404, message="记录已锁定")

    folder_name = os.path.basename(path)
    video_path = os.path.join(path, folder_name + '_created.mp4')

    # 确定图片文件夹路径
    images_folder = os.path.dirname(path)
    images = [os.path.join(images_folder, f"{i}.jpg") for i in range(cnt)]

    # 使用moviepy合成视频
    clip = ImageSequenceClip(images, fps=1)
    clip.write_videofile(video_path, codec='libx264')

    # 检查视频文件是否创建成功
    if not os.path.isfile(video_path):
        return NormalResponse(code=404, message="视频创建失败")

    for image in images:
        os.remove(image)
    db.add_frame_by_token(token, {'cnt_frame': -1})

    return NormalResponse(code=0, message="记录锁定成功")


@router.get("/deleteVideo")
async def deleteVideo(token: str):
    db = screenRecordModel()
    result = db.get_path_by_token(token)
    path = result.v_path
    db.delete_by_token(token)

    # 检查路径是否存在并且是文件夹
    if os.path.isdir(path):
        # 使用shutil.rmtree来删除整个文件夹
        shutil.rmtree(path)

    return NormalResponse(code=0, message="视频记录已删除")

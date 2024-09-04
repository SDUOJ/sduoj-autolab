import os
import platform
import shutil
from datetime import datetime
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont
from fastapi import APIRouter, Form, File, UploadFile
from fastapi.responses import FileResponse
from moviepy.editor import ImageSequenceClip
from moviepy.video.compositing.concatenate import concatenate_videoclips
from moviepy.video.io.VideoFileClip import VideoFileClip

from model.screen_record import screenRecordModel
from ser.screen_record import newRecord, ResponseModel, VideoList, PSList, NormalResponse

router = APIRouter(
    prefix="/screen_record"
)


# 添加记录
@router.post("/addRecord")
async def addRecord(data: newRecord):
    db = screenRecordModel()

    result = db.get_path_by_token(data.token)
    if result is not None:
        print("视频已存在")
        return NormalResponse(code=0, message="记录已经存在", data="记录已经存在")

    res = db.get_ps_type(data.bs_id)
    if res is None:
        return NormalResponse(code=404, message="题单不存在", data="题单不存在")

    base_path = get_base_path()
    v_path = os.path.join(base_path, str(data.bs_id) + "_" + res.name, data.u_name + "_" + datetime.now().strftime("%Y%m%d%H%M%S"))

    datas = {
        'bs_type': res.type,
        'bs_id': data.bs_id,
        'v_path': v_path,
        'u_id': data.u_id,
        'u_name': data.u_name,
        'token': data.token,
        'start_time': datetime.now(),
        'modify_time': datetime.now(),
        'cnt_frame': 0
    }
    db.add_record(datas)
    return NormalResponse(code=0, message="记录添加成功", data="记录添加成功")

@router.post("/addFrame")
async def addFrame(token: str = Form(...), pic: UploadFile = File(...)):
    db = screenRecordModel()

    result = db.get_path_by_token(token)
    if result is None:
        return NormalResponse(code=0, message="无此视频记录", data="无此视频记录")

    path = result.v_path
    cnt = result.cnt_frame

    os.makedirs(os.path.dirname(path), exist_ok=True)

    image_bytes = await pic.read()
    image = Image.open(BytesIO(image_bytes))

    target_size = (1280, 720)
    image = image.resize(target_size)

    if cnt == -1:
        return NormalResponse(code=0, message="视频正在导出", data="视频正在导出")
    else:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default()
        draw.text((target_size[0] - 180, 10), timestamp, font=font, fill=(255, 255, 255))

    frame_filename = f"{cnt}.jpg" if cnt != -1 else "exporting.jpg"
    frame_path = os.path.join(os.path.dirname(path), frame_filename)

    image.save(frame_path)

    if cnt != -1:
        db.add_frame_by_token(token, {'modify_time': datetime.now(), 'cnt_frame': cnt + 1})

    print(f"Frame added successfully: {frame_path}")
    return NormalResponse(code=0, message="追加帧成功", data="追加帧成功")

# 获取有录屏记录的题单列表
@router.get("/getPSList")
async def getPSList():
    db = screenRecordModel()
    result = db.get_ps_list()

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


# 获取某个题单的视频列表
@router.get("/getVideoList")
async def getVideoList(bs_id: int):
    data = bs_id
    db = screenRecordModel()
    result = db.get_video_list(data)
    if not result:
        return NormalResponse(code=404, message="无视频记录", data="无视频记录")

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


# 获取视频下载
@router.get("/getVideo")
async def getVideo(token: str):
    db = screenRecordModel()
    result = db.get_path_by_token(token)
    path = result.v_path
    cnt = result.cnt_frame

    folder_name = os.path.basename(path)
    video_path = os.path.join(path, folder_name + '_created.mp4')

    video_exists = os.path.isfile(video_path)
    images_folder = path
    images = [os.path.join(images_folder, f"{i}.jpg") for i in range(cnt)]

    if cnt == 0 and video_exists:
        return FileResponse(video_path, media_type='video/mp4', filename=os.path.basename(video_path))
    else:
        # 暂时锁定视频，防止冲突
        db.add_frame_by_token(token, {'cnt_frame': -1})
        if video_exists:
            existing_video = VideoFileClip(video_path)
            clip = ImageSequenceClip(images, fps=1)
            final_video = concatenate_videoclips([existing_video, clip])
            final_video.write_videofile(video_path, codec='libx264', audio=False)
        else:
            clip = ImageSequenceClip(images, fps=1)
            clip.write_videofile(video_path, codec='libx264', audio=False)

    if not os.path.isfile(video_path):
        return {"code": 404, "message": "视频文件创建失败", "data": "视频文件创建失败"}

    for image in images:
        os.remove(image)
    db.add_frame_by_token(token, {'cnt_frame': 0})

    return FileResponse(video_path, media_type='video/mp4', filename=os.path.basename(video_path))


# 删除记录和视频
@router.get("/deleteVideo")
async def deleteVideo(token: str):
    db = screenRecordModel()
    result = db.get_path_by_token(token)
    path = result.v_path
    db.delete_by_token(token)

    if os.path.isdir(path):
        shutil.rmtree(path)

    return NormalResponse(code=0, message="视频记录已删除", data="视频记录已删除")

# 根据bs_id删除所有视频记录
@router.get("/deleteAll")
async def deleteAll(bs_id: int):
    data = bs_id
    db = screenRecordModel()
    result = db.get_video_list(data)
    if not result:
        return NormalResponse(code=404, message="无视频记录", data="无视频记录")

    for r in result:
        path = r.v_path
        db.delete_by_token(r.token)
        if os.path.isdir(path):
            shutil.rmtree(path)

    return NormalResponse(code=0, message="所有视频记录已删除", data="所有视频记录已删除")

def get_base_path():
    system = platform.system()
    if system == "Windows":
        return "D:\\SDUOJ\\ScreenRecord"
    elif system == "Linux":
        return "/var/SDUOJ/ScreenRecord"
    elif system == "Darwin":  # macOS
        return "/Users/Shared/SDUOJ/ScreenRecord"
    else:
        raise Exception("Unsupported operating system")
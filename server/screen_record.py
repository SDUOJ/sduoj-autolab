import os
from datetime import datetime
from io import BytesIO
import shutil

import numpy
from PIL import Image
from fastapi import APIRouter, Form, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip, ImageSequenceClip

from model.screen_record import screenRecordModel
from ser.screen_record import newRecord, videoList

router = APIRouter(
    prefix="/screen_record"
)
@router.post("/addRecord")
async def addRecord(data: newRecord):
    db = screenRecordModel()

    result = db.get_path_by_token(data.token)
    if result is not None:
        print("视频已存在")
        raise HTTPException(status_code=200, detail="record already exist")

    datas = {
        'bs_type': data.bs_type,
        'bs_id': data.bs_id,
        'v_path': "D:\\SDUOJ\\ScreenRecord\\" + data.u_name + "_" + str(datetime.now().strftime("%Y%m%d")) + "\\",
        'u_id': data.u_id,
        'token': data.token,
        'start_time': datetime.now(),
        'modify_time': datetime.now(),
        'cnt_frame': 0
    }
    db = screenRecordModel()
    db.add_record(datas)
    raise HTTPException(status_code=200, detail="record added successfully")


@router.post("/addFrame")
async def addFrame(token: str = Form(...), pic: UploadFile = File(...)):
    db = screenRecordModel()

    result = db.get_path_by_token(token)
    if result is None:
        raise HTTPException(status_code=404, detail="no such record")

    path = result.v_path
    cnt = result.cnt_frame
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if cnt == -1:
        raise HTTPException(status_code=404, detail="record already locked")

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
    return {"detail": "frame added successfully"}

@router.get("/getVideoList")
async def getVideoList(datas: videoList):
    data = datas.dict()
    db = screenRecordModel()
    result = db.get_video_list(data)
    if not result:
        raise HTTPException(status_code=404, detail="no such record")

    video_list = [
        {
            "token": video.token,
            "start_time": video.start_time.strftime("%Y-%m-%d %H:%M:%S") if video.start_time else None,
            "modify_time": video.modify_time.strftime("%Y-%m-%d %H:%M:%S") if video.modify_time else None,
        }
        for video in result
    ]
    return video_list

@router.get("/getVideo")
async def getVideo(token: str = Form(...)):
    db = screenRecordModel()
    result = db.get_path_by_token(token)
    path = result.v_path
    cnt = result.cnt_frame

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
        raise HTTPException(status_code=404, detail="Video not created")

    path += '_created.mp4'
    # 检查文件是否存在
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Video not found")
    # 返回视频文件
    return FileResponse(path, media_type='video/mp4', filename=os.path.basename(path))

@router.get("/createLockVideo")
async def createLockVideo(token: str = Form(...)):
    db = screenRecordModel()
    result = db.get_path_by_token(token)
    path = result.v_path
    cnt = result.cnt_frame

    if cnt == -1:
        raise HTTPException(status_code=404, detail="record already locked")

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
        raise HTTPException(status_code=404, detail="Video not created")

    for image in images:
        os.remove(image)
    db.add_frame_by_token(token, {'cnt_frame': -1})

    raise HTTPException(status_code=200, detail="video created successfully")


@router.get("/deleteVideo")
async def deleteVideo(token: str = Form(...)):
    db = screenRecordModel()
    result = db.get_path_by_token(token)
    path = result.v_path
    db.delete_by_token(token)

    # 检查路径是否存在并且是文件夹
    if os.path.isdir(path):
        # 使用shutil.rmtree来删除整个文件夹
        shutil.rmtree(path)

    raise HTTPException(status_code=200, detail="video and associated files deleted")

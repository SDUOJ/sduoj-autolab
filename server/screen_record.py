import os
from datetime import datetime
from io import BytesIO

import numpy
from PIL import Image
from fastapi import APIRouter, Form, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip

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
        'v_path': "D:\\SDUOJ\\ScreenRecord\\" + data.u_name + "_" + str(datetime.now().strftime("%Y%m%d")) + ".mp4",
        'u_id': data.u_id,
        'token': data.token,
        'start_time': datetime.now(),
        'modify_time': datetime.now()
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
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # 检查视频文件是否存在
    if not os.path.isfile(path):
        # 创建一个包含图片的短视频文件
        image_bytes = await pic.read()
        image = Image.open(BytesIO(image_bytes))
        image.save(path, format='GIF', save_all=True, duration=1000, loop=0)
        db.add_frame_by_token(token, {'modify_time': datetime.now()})
        raise HTTPException(status_code=201, detail="video created successfully")

    # 读取视频文件
    video_clip = VideoFileClip(path)
    # 将上传的图片转换为PIL Image对象
    image_bytes = await pic.read()
    image = Image.open(BytesIO(image_bytes))
    # 将PIL Image对象转换为numpy数组
    image_array = numpy.array(image)
    # 使用numpy数组创建ImageClip
    image_clip = ImageClip(image_array, duration=1)
    # 设置图片的位置
    image_clip = image_clip.set_position(('center', 'center'))
    # 合成视频和图片
    final_clip = CompositeVideoClip([video_clip, image_clip.set_start(video_clip.duration)])
    # 将合成视频保存到新文件
    final_clip.write_videofile(path, codec='libx264', audio_codec=False, fps=video_clip.fps)
    # 更新最后确认时间到数据库
    db.add_frame_by_token(token, {'modify_time': datetime.now()})

    print(f"成功")
    raise HTTPException(status_code=200, detail="frame added successfully")

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

@router.post("/getVideo")
async def getVideo(token: str = Form(...)):
    db = screenRecordModel()
    path = db.get_path_by_token(token).v_path

    # 检查文件是否存在
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Video not found")
    # 返回视频文件
    return FileResponse(path, media_type='video/mp4', filename=os.path.basename(path))

@router.post("/deleteVideo")
async def deleteVideo(token: str = Form(...)):
    db = screenRecordModel()
    path = db.get_path_by_token(token).v_path

    db.delete_by_token(token)
    # 检查文件是否存在
    if os.path.isfile(path):
        os.remove(path)
    raise HTTPException(status_code=200, detail="video deleted")

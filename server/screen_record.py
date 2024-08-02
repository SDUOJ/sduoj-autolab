from fastapi import APIRouter, Form, File, UploadFile
from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip, VideoClip
from model.screen_record import screenRecordModel
from utils import makeResponse
from datetime import datetime
from ser.screen_record import newRecord, newFrame
import os

router = APIRouter(
    prefix="/screen_record"
)
@router.post("/addRecord")
async def addRecord(data: newRecord):
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
    return makeResponse("create")


@router.post("/addFrame")
async def addFrame(token: str = Form(...), pic: UploadFile = File(...)):
    db = screenRecordModel()

    result = db.get_path_by_token(token)
    if result is None:
        print("无匹配token")
        return {"message": "need create"}

    path = result.v_path
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # 检查视频文件是否存在
    if not os.path.isfile(path):
        print(f"视频文件不存在，正在创建一个新的视频文件: {path}")
        # 创建一个包含图片的短视频文件
        image_path = os.path.join(os.path.dirname(path), f"{os.path.basename(path).rsplit('.', 1)[0]}_overlay.png")
        with open(image_path, "wb+") as buffer:
            buffer.write(await pic.read())
        clip = ImageClip(image_path, duration=1)  # 设置视频持续时间
        clip.write_videofile(path, fps=1, codec='libx264', audio=False)
        db.add_frame_by_token(token, {'modify_time': datetime.now()})
        if os.path.exists(image_path):
            os.remove(image_path)
        return {"message": "视频文件创建成功"}

    # 保存上传的图片文件到本地
    image_path = os.path.join(os.path.dirname(path), f"{os.path.basename(path).rsplit('.', 1)[0]}_{int(datetime.now().timestamp())}.png")
    with open(image_path, "wb+") as buffer:
        buffer.write(await pic.read())

    # 读取视频文件
    video_clip = VideoFileClip(path)
    # 将图片转换为clip
    image_clip = ImageClip(image_path, duration=1)  # 设置图片在视频中的持续时间
    # 设置图片的位置
    image_clip = image_clip.set_position(('center', 'center'))
    # 合成视频和图片
    final_clip = CompositeVideoClip([video_clip, image_clip.set_start(video_clip.duration)])
    # 将合成视频保存到新文件
    final_clip.write_videofile(path, codec='libx264', audio_codec=False, fps=video_clip.fps)

    # 更新最后确认时间到数据库
    db.add_frame_by_token(token, {'modify_time': datetime.now()})

    # 删除下载的图片文件
    if os.path.exists(image_path):
        os.remove(image_path)

    print(f"成功")
    return {"message": "视频追加成功"}
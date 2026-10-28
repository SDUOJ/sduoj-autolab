# sduoj-autolab

## AI 异步任务后端

跨端状态、实现逻辑和未完成项请查看 [共享项目进度](../sduoj-deploy/docs/AI_PLATFORM_PROGRESS.md)。已存在后端 API 不代表全部能力或生产替换已完成。

题栈能力已合入现有 `auto_task`、FastAPI 和 MySQL。接口、能力映射、显式数据库迁移和旧任务切换见 [AI 后端集成说明](docs/auto-task-ai-integration.md)。Web 默认不再为每个 Gunicorn 进程启动任务消费者，独立执行 `python -m auto_task.worker`；启用前需完成迁移和模型/沙箱配置。本次不修改 Web2 前端。

## 本机开发

先在 `../sduoj-deploy` 执行 `./dev up`，再在本目录执行：

```sh
conda activate oj_problem_set
python prepare_dev.py
./dev
```

默认使用 `SDUOJ_MODE=debug`，数据库、Redis 和 Java 服务均连接本机。
运行线上配置需显式设置 `SDUOJ_MODE=release`。本地数据库快照、端口和
配置说明见 [开发部署文档](../sduoj-deploy/DEV.md)。

SDUOJ-based pluggable components for in-depth support of course needs, supporting subjective questions, time-limited exams, series of question sets and other functions. Use FastAPI as the backend.

## 部署方案

1. 建立数据库:

```shell
sudo docker-compose -f setup.yaml up
# ctrl + c
sudo docker-compose -f setup.yaml start
```

redis 的密码设置不上，还需要再修正一下。
```shell
sudo docker exec -it id sh
redis-cli
config set requirepass xxx
```


2. 创建虚拟环境：

本地：

```shell
pip freeze > requirements.txt
```

部署：

```shell

conda create --name oj_problem_set python=3.9
conda activate oj_problem_set
python -m pip install -r requirements.txt
#运行 model 中的文件，建立表。
python db.py
mkdir logs
python -m gunicorn -c gunicorn.conf.py main:app

```

3. 运维

```shell
ps -ef |grep main:app |awk '{print $2}'| xargs kill -9
python -m uvicorn main:app --reload --host=0.0.0.0 --port 5005
python -m uvicorn main:app --reload --host=127.0.0.1 --port 5005

```

## 鸣谢
2024年7月1日至9月1日，SDUOJ团队采用Pengyu Xue, Linhao Wu等人开发的[ERICommiter](https://arxiv.org/abs/2404.14824)进行提交信息生成，特此致谢。
## Acknowledgment
We acknowledge the utilization of [ERICommiter](https://arxiv.org/abs/2404.14824), developed by Pengyu Xue, Linhao Wu et al., for commit message generation by the SDUOJ team from July 1 to September 1, 2024. We express our gratitude for this contribution.

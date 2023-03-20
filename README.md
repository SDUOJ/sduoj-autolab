# sduoj-autolab

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

```
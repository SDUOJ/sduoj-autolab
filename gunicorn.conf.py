# 监听内网端口
bind = '0.0.0.0:5005'

# 工作目录
chdir = './'

# 并行工作进程数
workers = 16

# 指定每个工作者的线程数
threads = 2

# 监听队列
backlog = 1024

# 超时时间
timeout = 120

daemon = True

# 工作模式协程
worker_class = 'uvicorn.workers.UvicornWorker'

# 设置最大并发量
worker_connections = 4000

# 设置进程文件目录
pidfile = './logs/gunicorn.pid'

# 设置访问日志和错误信息日志路径
accesslog = './logs/gunicorn_access.log'
errorlog = './logs/gunicorn_error.log'

# 设置gunicron访问日志格式，错误日志无法设置
access_log_format = '%(h) -  %(t)s - %(u)s - %(s)s %(H)s'

# 设置这个值为true 才会把打印信息记录到错误日志里
capture_output = True

# 设置日志记录水平
loglevel = 'debug'

#
#
# 终止 ps -ef |grep main:app |awk '{print $2}'| xargs kill -9
# 启动
# conda activate hw
# cd homework
# gunicorn -c gunicorn.conf.py main:app

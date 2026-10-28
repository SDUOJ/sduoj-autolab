# AI 后端开发验收流程

更新时间：2026-09-06。所有命令只针对本机 debug 环境；禁止把测试 URL、账号或密钥替换为生产配置。

## 1. 单元与接口回归

```bash
/opt/miniconda3/envs/oj_problem_set/bin/python -m pytest -q
```

当前结果：230 passed，11 skipped。跳过项只依赖显式环境变量：独立 MySQL 和真实 Docker。

## 2. 独立 MySQL

先创建临时数据库容器并将连接写入当前 shell：

```bash
docker --context desktop-linux run --rm --name autolab-native-test-mysql \
  -e MYSQL_ALLOW_EMPTY_PASSWORD=yes -e MYSQL_DATABASE=autolab_ai_test \
  -p 127.0.0.1:64475:3306 -d mysql:8.0
AUTOLAB_AI_TEST_DATABASE_URL=mysql+pymysql://root@127.0.0.1:64475/autolab_ai_test \
  /opt/miniconda3/envs/oj_problem_set/bin/python -m pytest -q
```

本轮独立 MySQL 快照：230 passed，9 skipped。真实建表、事务、锁竞争、全文索引、账本、迁移、身份再授权、协作、考务、批阅、查重、面试和原生题库均有回归；9 个跳过项均为真实 Docker opt-in，不是业务失败。

## 3. 真实执行器

```bash
AUTOLAB_AI_REAL_SANDBOX_TEST=1 \
  /opt/miniconda3/envs/oj_problem_set/bin/python -m pytest tests/test_ai_real_sandbox.py -q
```

当前结果：核心执行器 8/8 passed，命题 generator/validator/标程真实容器 1/1 passed，并新增磁盘 64 MiB/源码只读检查。覆盖 C/C++/Python/Java、编译诊断、超时、禁网、JPlag 6.3 和命题包；JPlag 使用 `eclipse-temurin:25-jdk`，Java parser 需要 JDK。

## 4. 本机 OJ 三类题库对接

```bash
SDUOJ_MODE=debug /opt/miniconda3/envs/oj_problem_set/bin/python scripts/verify_ai_native_dev.py
```

脚本只连接 `127.0.0.1:8080` Gateway 和本机 OJ MySQL，创建带 `AI-DEV` 标记的开发题组，验证客观/主观/程序题导入、发布、重放、回读和历史。程序题使用新题号、既有本地 A+B 测试点和 C++14 模板，随后通过原生提交接口等待 Accepted。

## 5. 合同与页面

```bash
/opt/miniconda3/envs/oj_problem_set/bin/python scripts/export_ai_contracts.py
cd ../sduoj-web2
node_modules/.bin/jest --runInBand src/Pages/Manage/AI/NativeQuestionWorkbench.test.tsx
node_modules/.bin/eslint src/Pages/Manage/AI/NativeQuestionWorkbench.tsx src/Pages/Manage/AI/NativeQuestionWorkbench.test.tsx src/Utils/API/ai-workbench.ts
node_modules/.bin/tsc --noEmit --pretty false
node_modules/.bin/playwright test -c tests/ai-native.config.ts --reporter=list
```

页面浏览器验收必须使用独立端口 3011，登录凭证读取 `sduoj-deploy/data/dev/ai-integration/test-user.json`，不得输出凭证；检查桌面与 390px 视口、分页、无外网请求和无 pageerror。

新增工作区检查使用端口 3012：`node_modules/.bin/playwright test -c tests/ai-workspace.config.ts`。它用真实 dev OJ 登录依次加载 9 个 AI 页面，检查 1440px/390px 布局、后端授权请求和 pageerror，并通过真实 Gateway SSE 验证任务取消事件。

## 6. 证据边界

测试夹具不证明真实模型质量、供应商账单准确性、生产身份来源、ARM64 隔离、备份恢复或跨 Java exactly-once。模型 provider/key 未配置前不得运行付费模型 E2E。生产迁移、Nginx/Nacos、旧消费者停用和灰度发布均需单独变更窗口与回滚记录。

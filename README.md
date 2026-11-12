# kiln

制品构建平台的一期可运行骨架。架构决策以 [制品构建平台架构方案](https://cursor.com/canvas/store-58c8b3dd-c94c-4c26-bc68-e2ab4b942931/acb42b49-db33-462d-bfbe-a4123efe8571) 为准。

这一版提供 API 大纲、简体中文控制台、Postgres / Jenkins / Gitea 的 compose 契约，以及能在 CI 里跑通的测试。它不是完整产品。

## 本地运行

配置只来自环境变量。可复制 `.env.example` 为 `.env`（该文件已被忽略）。`.env.example` 里的数据库口令 `kiln` 只用于本地开发，不能用于生产。不要把模型密钥写进仓库。

### 不使用 Docker

在仓库根目录：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e "./backend[dev]"
uvicorn kiln.main:create_app --factory --host 127.0.0.1 --port 8000
```

另开一个终端：

```bash
cd console
npm install
npm run dev
```

- 健康检查：<http://127.0.0.1:8000/health>
- API 说明：<http://127.0.0.1:8000/docs>
- 控制台：<http://127.0.0.1:5173>

`/health` 不连接数据库，也不访问模型端点。默认 `LLM_PROVIDER=off`。

### Docker Compose

```bash
docker compose up --build
```

- API：<http://127.0.0.1:8000/health>
- 控制台：<http://127.0.0.1:8080>
- Postgres：`localhost:5432`，库名 `kiln`

默认会启动 `kiln-postgres`、`kiln-api`、`kiln-console`。一期 API 接受 `DATABASE_URL`，但还不建立数据库连接。

这个文件供开发机使用。不要在共享实验机上启动，以免占用宿主机端口。容器名都带 `kiln-` 前缀。

Jenkins 和 Gitea 是后续实验室占位，容器名是 `kiln-jenkins` 和 `kiln-gitea`，不随默认命令启动：

```bash
docker compose --profile lab up
```

占位端口是 Jenkins `18080`、Gitea `13000`。

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `KILN_ENV` | `local` | 环境名 |
| `KILN_HTTP_HOST` | `0.0.0.0` | API 监听地址 |
| `KILN_HTTP_PORT` | `8000` | API 监听端口 |
| `KILN_CORS_ORIGINS` | 本地控制台来源 | 逗号分隔 |
| `DATABASE_URL` | 本地 Postgres | 元数据连接串。本期不连接 |
| `LLM_PROVIDER` | `off` | `openai`、`mock` 或 `off`。`none` 和 `disabled` 视为 `off` |
| `LLM_BASE_URL` | 空 | OpenAI 兼容根路径，例如 `http://127.0.0.1:11434/v1`。`openai` 时必填 |
| `LLM_API_KEY` | 空 | 仅从环境读取。无鉴权的本地端点可以留空 |
| `LLM_MODEL` | 空 | 默认模型。请求里也可以传 `model` |
| `LLM_TIMEOUT_SECONDS` | `30` | 调用超时 |

`LLM_PROVIDER=openai` 时，聊天请求会 `POST {LLM_BASE_URL}/chat/completions`。模拟模式和关闭模式不会访问网络。

## API 大纲

前缀 `/api/v1`。数据在进程内存中，重启即失。这些路由目前不执行鉴权。

- 本地账号：`GET/POST /accounts`，`GET /accounts/{id}`
- RBAC 目录：`GET /rbac/roles`，`GET /rbac/permissions`
- 制品：`GET/POST /artifacts`，`POST /artifacts/{id}/versions`
- 下载预约：`POST /artifacts/{id}/versions/{version}/download-reservations`
- 稳定取包：`GET /retrieve/{reservation_id}`（本期只返回预约契约，`content_available` 为 false）
- Skill：`POST /skills` 创建草稿，`POST /skills/{id}/submit-review`，`POST /skills/{id}/publish`
- 模型：`GET /llm/provider`，`POST /llm/chat`

Skill 步骤不会被执行。接口不会运行 shell。

## 测试

```bash
source .venv/bin/activate
python -m ruff check backend
python -m pytest backend
cd console && npm run build
```

GitHub Actions 会跑后端测试、控制台构建，以及 `docker compose config`。

## 不在本期

部署、制品仓库集成、镜像仓库、Kubernetes 构建引擎、Jenkins 执行器、身份联邦、登录与口令存储、制品字节下载、任意 shell 执行。

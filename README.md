# kiln

制品构建平台的一期可运行骨架。架构决策以 [制品构建平台架构方案](https://cursor.com/canvas/store-58c8b3dd-c94c-4c26-bc68-e2ab4b942931/acb42b49-db33-462d-bfbe-a4123efe8571) 为准。

这一版提供 API 大纲、简体中文控制台、Postgres / Jenkins / Gitea 的 compose 契约，以及能在 CI 里跑通的测试。它不是完整产品。

机器配置、依赖系统，以及在一台新机器上把当前骨架启动起来的步骤，写在 [部署说明](docs/DEPLOY.md)。平台不负责把制品发布到运行环境。

## 机器配置

`docker-compose.yml` 没有 `cpus`、`mem_limit`、`deploy.resources`，Jenkins 服务也没有 `JAVA_OPTS`。下表是按当前服务清单给出的起步建议，不是压测后的生产规格。

| 档位 | CPU | 内存 | 可用磁盘 | 用来跑什么 |
| --- | --- | --- | --- | --- |
| 小实验室 / 测试 | 2 | 4 GB | 20 GB | 默认栈：Postgres、API、控制台。镜像在这台机器上构建 |
| 建议 | 4 | 8 GB | 40 GB | 默认栈，并为 profile `lab` 的 Jenkins、Gitea 占位和构建缓存留余量 |

- 系统：能构建并运行仓库中这些 Linux 镜像的 64 位环境。CI 使用 GitHub Actions 的 `ubuntu-latest`。仓库不要求特定发行版，镜像也没有钉死 CPU 架构，不使用 GPU。
- Docker：Docker Engine，以及 Compose V2（命令是 `docker compose`）。文件使用 `profiles` 和 `depends_on.condition: service_healthy`。版本号没有钉死。
- 默认栈占用宿主机端口 `5432`、`8000`、`8080`。profile `lab` 另占 `18080`、`13000`。
- 容器名固定：`kiln-postgres`、`kiln-api`、`kiln-console`。lab 另有 `kiln-jenkins`、`kiln-gitea`。网络名固定为 `kiln-net`。

小实验室档把镜像构建算了进去：控制台镜像在构建阶段执行 `npm ci` 和 `npm run build`，这一步比三个容器运行时更吃内存。建议档的 8 GB 用来在同一台机器上再放下 profile `lab`。Jenkins 服务没有内存上限，也没有 `JAVA_OPTS`，实际堆大小由上游镜像决定，本仓库没有测量。

## 依赖系统

| 系统 | 本期 | 仓库中的实际情况 |
| --- | --- | --- |
| Docker Engine 与 Compose V2 | 现在需要（Compose 部署） | `docker-compose.yml`。CI 执行 `docker compose config`。直接跑进程的开发方式不依赖 Docker |
| PostgreSQL | 现在需要（默认 Compose 栈） | 镜像 `postgres:16-alpine`，默认库 `kiln`。配置项为 `DATABASE_URL`、`POSTGRES_USER`、`POSTGRES_PASSWORD`、`POSTGRES_DB`。API 接受连接串，本期不建立连接。只跑 API 进程时可以不启动数据库 |
| kiln API 与控制台 | 现在需要 | 本地构建 `kiln-api:local`（`python:3.11-slim`）和 `kiln-console:local`（构建阶段 `node:22-alpine`，运行阶段 `nginx:1.27-alpine`）。没有现成的远程应用镜像 |
| Jenkins | 可选占位；执行器尚未接线 | 仅 `docker compose --profile lab`。镜像 `jenkins/jenkins:lts-jdk17`。代码里没有 Jenkins 地址或任务配置 |
| Git 远端 | 计划。lab 中有未接入的 Gitea | 架构方案把 GitLab 保留为生产代码源。当前代码不读取 Git 远端。compose 里与之相邻的只有 `gitea/gitea:1.22`（sqlite3）占位 |
| 制品目录 | 现在是进程内存。Nexus 尚未接线 | 制品与校验和登记在 API 内存中。`GET /api/v1/retrieve/{reservation_id}` 的 `content_available` 为 false。仓库没有 Nexus 地址或客户端。架构方案中的现网 Nexus 集成仍待对接 |
| OpenAI 兼容模型端点 | 可选 | `LLM_PROVIDER` 默认 `off`。设为 `openai` 时必须提供 `LLM_BASE_URL` |
| 身份联邦 | 计划 | 内存中的本地账号与 RBAC 目录。没有口令、登录，也没有外部身份源的产品名或地址 |
| Kubernetes、专用镜像仓库 | 计划 | 仓库中没有对应清单或配置项。架构方案把它们放在镜像构建阶段 |

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

逐步核对、环境变量如何进入容器，以及常见失败，见 [部署说明](docs/DEPLOY.md)。

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

骨架如何在一台机器上启动，见 [部署说明](docs/DEPLOY.md)。平台仍然不把制品发布到运行环境。制品仓库集成、镜像仓库、Kubernetes 构建引擎、Jenkins 执行器、身份联邦、登录与口令存储、制品字节下载、任意 shell 执行，这些都还没有实现。

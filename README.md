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
- 默认栈把宿主机端口 `5432`、`8000`、`8080` 只绑定到 `127.0.0.1`。profile `lab` 的 `18080`、`13000` 同样只绑定 `127.0.0.1`。容器内的 API 进程仍监听 `0.0.0.0`。
- 容器名固定：`kiln-postgres`、`kiln-api`、`kiln-console`。lab 另有 `kiln-jenkins`、`kiln-gitea`。网络名固定为 `kiln-net`。

小实验室档把镜像构建算了进去：控制台镜像在构建阶段执行 `npm ci` 和 `npm run build`，这一步比三个容器运行时更吃内存。建议档的 8 GB 用来在同一台机器上再放下 profile `lab`。Jenkins 服务没有内存上限，也没有 `JAVA_OPTS`，实际堆大小由上游镜像决定，本仓库没有测量。

## 依赖系统

| 系统 | 本期 | 仓库中的实际情况 |
| --- | --- | --- |
| Docker Engine 与 Compose V2 | 现在需要（Compose 部署） | `docker-compose.yml`。CI 执行 `docker compose config`。直接跑进程的开发方式不依赖 Docker，但 API 仍然需要 PostgreSQL |
| PostgreSQL | 现在需要 | 镜像 `postgres:16-alpine`，默认库 `kiln`。配置项为 `DATABASE_URL`、`POSTGRES_USER`、`POSTGRES_PASSWORD`、`POSTGRES_DB`。API 启动时连接该库并执行迁移。账号、RBAC 目录、制品、版本、下载预约和 skill 都写在这里。没有可连接的数据库时 API 起不来 |
| kiln API 与控制台 | 现在需要 | 本地构建 `kiln-api:local`（`python:3.11-slim`）和 `kiln-console:local`（构建阶段 `node:22-alpine`，运行阶段 `nginx:1.27-alpine`）。没有现成的远程应用镜像 |
| Jenkins 兼容执行器 | 一期要做，这一版尚未接线 | 仅 `docker compose --profile lab`。镜像 `jenkins/jenkins:lts-jdk17` 是占位。代码里没有 Jenkins 地址或任务配置 |
| Git 远端 | 计划。lab 中有未接入的 Gitea | 架构方案把 GitLab 保留为生产代码源。当前代码不读取 Git 远端。compose 里与之相邻的只有 `gitea/gitea:1.22`（sqlite3）占位 |
| 制品目录 | 元数据已在 PostgreSQL。Nexus 与制品字节仍是一期要做的事 | 制品、版本、校验和与下载预约写在 Postgres。`GET /api/v1/retrieve/{reservation_id}` 的 `content_available` 为 false。仓库没有 Nexus 地址或客户端 |
| OpenAI 兼容模型端点 | 可选 | `LLM_PROVIDER` 默认 `off`。设为 `openai` 时必须提供 `LLM_BASE_URL` |
| 本地登录与身份联邦 | 一期要做，这一版还没有 | 本地账号和 RBAC 目录在 Postgres。`/api/v1` 需要本地令牌 `KILN_API_TOKEN`。没有登录页、口令，也没有外部身份源 |
| Harbor、Kubernetes、专用镜像仓库 | 不属于一期 | 仓库中没有对应清单或配置项。架构方案把镜像构建放在后面 |

## 本地运行

配置只来自环境变量。可复制 `.env.example` 为 `.env`（该文件已被忽略）。`.env.example` 里的数据库口令 `kiln` 只用于本地开发，不能用于生产。不要把模型密钥写进仓库。

### 不使用 Docker

先准备一个可连接的 PostgreSQL。默认 `DATABASE_URL` 是 `postgresql://kiln:kiln@localhost:5432/kiln`。API 进程启动时会执行迁移；数据库不可达时进程起不来。

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

`/health` 本身不查询数据库，也不访问模型端点。进程若能完成启动，说明当时的迁移已经跑过。默认 `LLM_PROVIDER=off`。

### Docker Compose

逐步核对、环境变量如何进入容器，以及常见失败，见 [部署说明](docs/DEPLOY.md)。

```bash
docker compose up --build
```

- API：<http://127.0.0.1:8000/health>
- 控制台：<http://127.0.0.1:8080>
- Postgres：`localhost:5432`，库名 `kiln`

默认会启动 `kiln-postgres`、`kiln-api`、`kiln-console`。API 在进程启动时连接 Postgres 并执行迁移，不需要单独的迁移命令。

这个文件供开发机使用。不要在共享实验机上启动，以免占用宿主机端口。容器名都带 `kiln-` 前缀。

Jenkins 和 Gitea 不随默认命令启动，容器名是 `kiln-jenkins` 和 `kiln-gitea`。Gitea 只是 lab 占位，不是生产代码源；生产代码源仍是 GitLab。仓库里还没有 Git 客户端，以后接入时应做成可替换的适配器。Jenkins 容器同样只是占位；兼容执行器仍是一期要做的事，这一版还没有接上：

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
| `DATABASE_URL` | 本地 Postgres | 元数据连接串。API 启动时连接并迁移 |
| `KILN_API_TOKEN` | `kiln-local` | `/api/v1` 的 Bearer 令牌。`kiln-local` 只用于本地和 CI，不能用于生产。留空时 API 拒绝启动 |
| `LLM_PROVIDER` | `off` | `openai`、`mock` 或 `off`。`none` 和 `disabled` 视为 `off` |
| `LLM_BASE_URL` | 空 | OpenAI 兼容根路径，例如 `http://127.0.0.1:11434/v1`。`openai` 时必填 |
| `LLM_API_KEY` | 空 | 仅从环境读取。无鉴权的本地端点可以留空 |
| `LLM_MODEL` | 空 | 默认模型。请求里也可以传 `model` |
| `LLM_TIMEOUT_SECONDS` | `30` | 调用超时 |

`LLM_PROVIDER=openai` 时，聊天请求会 `POST {LLM_BASE_URL}/chat/completions`。模拟模式和关闭模式不会访问网络。

## API 大纲

前缀 `/api/v1`。账号、RBAC 目录、制品、版本、下载预约和 skill 保存在 PostgreSQL，API 进程重启后仍然在。这些路由需要请求头 `Authorization: Bearer <KILN_API_TOKEN>`。没有令牌时返回 401，不会改库。`/health` 和 `/docs` 不需要令牌；说明页本身不能绕过这道门去写入。这不是登录，也不按角色鉴权。

- 本地账号：`GET/POST /accounts`，`GET /accounts/{id}`
- RBAC 目录：`GET /rbac/roles`，`GET /rbac/permissions`
- 制品：`GET/POST /artifacts`，`POST /artifacts/{id}/versions`
- 下载预约：`POST /artifacts/{id}/versions/{version}/download-reservations`
- 稳定取包：`GET /retrieve/{reservation_id}`（本期只返回预约契约，`content_available` 为 false）
- Skill：`POST /skills` 创建草稿，`POST /skills/{id}/submit-review`，`POST /skills/{id}/publish`
- 模型：`GET /llm/provider`，`POST /llm/chat`

Skill 步骤不会被执行。接口不会运行 shell。

## 测试

后端测试连接真实 PostgreSQL，不使用内存库、SQLite 或假的持久层。默认连接串是 `postgresql://kiln:kiln@127.0.0.1:5432/kiln`，可用环境变量 `KILN_TEST_DATABASE_URL` 覆盖。GitHub Actions 的 backend 作业会启动 `postgres:16`。`backend/tests/test_persistence.py` 里的 `test_records_survive_api_process_restart` 通过 HTTP 写入记录，结束 API 进程，确认库里仍有这些行，再启动一个新进程读回同一条记录。`backend/tests/test_auth.py` 里的 `test_anonymous_requests_do_not_change_durable_rows` 确认未带令牌的客户端得到 401，并且账号与版本行数不变。`backend/tests/test_boundaries.py` 确认 compose 发布端口都带 `127.0.0.1`。

```bash
source .venv/bin/activate
python -m ruff check backend
python -m pytest backend
cd console && npm run build
```

GitHub Actions 会跑后端测试、控制台构建，以及 `docker compose config`。

## 这一版还没有，但仍属于一期

骨架如何在一台机器上启动，见 [部署说明](docs/DEPLOY.md)。下面这些还没有写进当前骨架，按架构方案仍是一期要完成的事：

- Nexus 集成
- Jenkins 兼容执行器
- 本地登录，以及身份联邦的起步
- 制品字节和真正的取包（现在 `content_available` 仍是 false）

`/api/v1` 已经有本地访问令牌。没有令牌不能改领域数据，也不能读取这些接口。这还不是登录，也不是身份联邦。

## 不属于一期

- Harbor
- Kubernetes 镜像构建
- 专用镜像仓库
- 把制品发布到运行环境。平台仍然不把制品部署到运行环境。

GitLab 仍是生产代码源。compose 里的 Gitea 只是 profile `lab` 的占位。仓库里还没有 Git 客户端；以后如果接入，应做成可替换的适配器，而不是把某一个产品写死。任意 shell 执行不在平台能力内。

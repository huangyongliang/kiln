# 部署说明

本文说明如何在一台新机器上启动当前仓库里的一期骨架。范围只包括把已有进程跑起来并核对它们。平台不负责把制品发布到运行环境。架构取舍以 [README](../README.md) 所链的架构方案为准。仓库没有写出的地址、口令、主机名和任务名，本文留空。

当前版本号是 `0.1.0`（`backend/kiln/__init__.py` 的 `__version__`）。`GET /health` 返回的 `version` 与它一致。

## 依赖系统

| 系统 | 档位 | 当前仓库里实际有什么 |
| --- | --- | --- |
| Docker Engine 与 Compose V2 | 现在需要（走 Compose 时） | 根目录 `docker-compose.yml`。CI 用 `docker compose config` 校验。文末的进程方式不使用 Docker |
| PostgreSQL | 现在需要（默认 Compose 栈） | 服务 `postgres`，镜像 `postgres:16-alpine`，容器 `kiln-postgres`，数据卷键名 `kiln-postgres-data`。API 接受 `DATABASE_URL`，不打开连接。只跑 API 进程时可以不启动数据库 |
| kiln API | 现在需要 | 服务 `api`，本地镜像 `kiln-api:local`，容器 `kiln-api`。基础镜像 `python:3.11-slim`，监听容器内 `8000` |
| 控制台 | 现在需要 | 服务 `console`，本地镜像 `kiln-console:local`，容器 `kiln-console`。构建阶段 `node:22-alpine`，运行阶段 `nginx:1.27-alpine`，容器内 `80` 映射到宿主机 `8080` |
| Jenkins | 可选占位 | 仅 profile `lab`。镜像 `jenkins/jenkins:lts-jdk17`，容器 `kiln-jenkins`，卷键名 `kiln-jenkins-home`。没有 `JENKINS_URL` 或任务配置，API 不调用它 |
| Git 远端 | 计划 | 架构方案把 GitLab 保留为生产代码源。代码不读取 Git 地址、令牌或项目路径。profile `lab` 里的 `gitea/gitea:1.22`（`GITEA__database__DB_TYPE=sqlite3`，容器 `kiln-gitea`）是未接入的占位，不是生产代码源 |
| 制品存储 | 现在是进程内存；Nexus 尚未接线 | 制品、版本和下载预约在 API 进程内存中，重启即失。取包响应里 `content_available` 为 false。包类型字段有 `maven`、`python`、`generic`、`frontend`，没有对应的仓库客户端。架构方案中的现网 Nexus 集成尚未写入配置 |
| OpenAI 兼容模型端点 | 可选 | `LLM_PROVIDER` 默认 `off`。`openai` 会 `POST {LLM_BASE_URL}/chat/completions`。`mock` 不访问网络 |
| 身份联邦 | 计划 | 本地账号和 RBAC 目录在内存中。没有口令、登录，也没有外部身份系统的产品名或地址 |
| Kubernetes、专用镜像仓库 | 计划 | 没有清单、没有镜像推送，也没有构建执行器。架构方案把它们放在后续的镜像构建阶段 |

默认命令 `docker compose up` 只启动 Postgres、API、控制台。Jenkins 和 Gitea 要加 `--profile lab`。

## 机器配置

Compose 文件没有 `cpus`、`mem_limit`、`deploy.resources`。Jenkins 服务没有 `JAVA_OPTS`。下面是起步建议，不是压测后的生产规格。

| 档位 | CPU | 内存 | 可用磁盘 | 覆盖的服务 |
| --- | --- | --- | --- | --- |
| 小实验室 / 测试 | 2 | 4 GB | 20 GB | 默认栈，并且在同一台机器上构建镜像 |
| 建议 | 4 | 8 GB | 40 GB | 默认栈，加上 profile `lab` 和构建缓存的余量 |

依据：

- 默认三个容器不设内存上限。小实验室档的 4 GB 主要留给同机镜像构建：控制台镜像会拉取 `node:22-alpine`，并执行 `npm ci` 与 `npm run build`。运行时的 Postgres、API 和 nginx 比这一步轻。本仓库没有做过占用测量。
- 仓库没有现成的 `kiln-api` / `kiln-console` 远程镜像，部署这台机器要自己构建。未写仓库前缀的基础镜像按守护进程的默认规则拉取：`python:3.11-slim`、`node:22-alpine`、`nginx:1.27-alpine`、`postgres:16-alpine`。lab 另加 `jenkins/jenkins:lts-jdk17` 和 `gitea/gitea:1.22`。仓库没有配置镜像加速。
- Jenkins 服务没有 `JAVA_OPTS`，compose 也没有给它设内存上限。JVM 堆由上游镜像和 JDK 的默认值决定，本仓库没有测量。建议档的 8 GB 是把这个占位和默认栈放在一起时的起步值。
- 磁盘要放下镜像层、构建缓存，以及命名卷。项目名是 `kiln`，卷在 Docker 里通常显示为 `kiln_kiln-postgres-data`、`kiln_kiln-jenkins-home`、`kiln_kiln-gitea-data`。API 不往 Postgres 写业务数据，数据库卷里只有初始化后的系统目录。Jenkins 数据卷只在启动过 lab 之后出现。

系统与 Docker：

- 需要能构建并运行上述 Linux 镜像的 64 位环境。CI 使用 GitHub Actions 的 `ubuntu-latest`。仓库不指定发行版，镜像没有钉死 CPU 架构，也不使用 GPU。
- 需要 Docker Engine 和 Compose V2。`docker compose version` 应能执行。文件用了 `profiles` 和 `depends_on.condition: service_healthy`，Compose V1 的 `docker-compose` 不适用。仓库没有钉死 Docker 的版本号。
- 默认栈占用宿主机端口 `5432`、`8000`、`8080`。lab 另占 `18080`、`13000`。
- 容器名是固定的：`kiln-postgres`、`kiln-api`、`kiln-console`、`kiln-jenkins`、`kiln-gitea`。网络名固定为 `kiln-net`。同一 Docker 守护进程上不能再起一套同名容器。
- Compose 没有把宿主机目录或 Docker 套接字挂进容器，也没有 `DOCKER_HOST`。改代码后需要重新构建镜像。

不要在已经占用这些端口或容器名的共享机器上启动。

## 前提

在仓库根目录操作。机器上需要：

- Git
- 上文的 Docker Engine 与 Compose V2
- 能拉取上一节列出的基础镜像
- 默认栈的三个宿主机端口空闲。准备启动 lab 时，`18080` 和 `13000` 也要空闲

进程方式（不使用 Docker）另需 Python 3.11 或更高，以及 Node.js 22。`backend/pyproject.toml` 要求 `requires-python >= 3.11`。CI 和 `console/Dockerfile` 使用 Node.js 22；`console/package.json` 没有 `engines` 字段。

## 获取代码

当前仓库位于 GitHub。生产代码源按架构方案是 GitLab，但本期没有任何把 kiln 接到 GitLab 的配置。

```bash
git clone https://github.com/huangyongliang/kiln.git
cd kiln
```

## 环境文件

只从示例复制，不要把填过密钥的文件提交进仓库。`.gitignore` 已忽略 `.env` 和 `.env.local`。

```bash
cp .env.example .env
```

`.env.example` 里的数据库口令 `kiln` 只用于本地开发，不能用于生产。示例不包含模型密钥。无鉴权的本地模型端点可以把 `LLM_API_KEY` 留空。

示例中的键：

| 变量 | 示例值 | 谁读取 |
| --- | --- | --- |
| `KILN_ENV` | `local` | API 进程的配置对象。容器命令不使用它，compose 也没有把它传进容器 |
| `KILN_HTTP_HOST` | `0.0.0.0` | 仅 `kiln.main:run`（入口脚本 `kiln-api`）。容器的 `CMD` 写死 `--host 0.0.0.0 --port 8000` |
| `KILN_HTTP_PORT` | `8000` | 同上。改这个值不会改变 compose 发布的 `8000:8000` |
| `KILN_CORS_ORIGINS` | `http://localhost:5173,http://localhost:8080` | 进程直接运行时生效。compose 给容器写死了同一组来源，不读取 `.env` 里的这一行 |
| `DATABASE_URL` | `postgresql://kiln:kiln@localhost:5432/kiln` | 进程直接运行时进入配置。容器内的值由 compose 拼成 `postgresql://…@postgres:5432/kiln`，主机名是服务名 `postgres` |
| `POSTGRES_USER` | `kiln` | 只由 compose 插值，传给 Postgres 容器，并用来拼接容器内的 `DATABASE_URL` |
| `POSTGRES_PASSWORD` | `kiln` | 同上 |
| `POSTGRES_DB` | `kiln` | 同上 |
| `LLM_PROVIDER` | `off` | compose 会传入 API 容器。`openai`、`mock`、`off`。`none` 和 `disabled` 视为 `off`，`openai-compatible` 视为 `openai` |
| `LLM_BASE_URL` | 空 | `openai` 时必填。compose 原样传入 |
| `LLM_API_KEY` | 空 | compose 原样传入。只从环境读取 |
| `LLM_MODEL` | 空 | 默认模型。请求体里也可以传 `model` |
| `LLM_TIMEOUT_SECONDS` | `30` | 允许范围大于 0 且不超过 300 |

已经导出的同名环境变量会覆盖 `.env`。Compose 插值是这样，pydantic-settings 也是这样。

代码不读取 Jenkins、Gitea、GitLab、Nexus 或身份系统的地址。把这类变量写进 `.env` 不会改变 API 的行为。

`LLM_PROVIDER=openai` 且 `LLM_BASE_URL` 为空或只有空白时，进程在加载配置时失败。校验错误中包含 `LLM_PROVIDER=openai 时必须设置 LLM_BASE_URL`。保持示例里的 `off` 即可先把骨架启动。

## 构建并启动默认栈

在仓库根目录：

```bash
docker compose config >/dev/null
docker compose up --build -d
```

第一条只渲染配置，不起容器，与 CI 的校验相同。第二条会：

- 构建 `kiln-api:local`（上下文 `./backend`）和 `kiln-console:local`（上下文 `./console`）
- 拉取 `postgres:16-alpine`（本地没有该镜像时）
- 创建网络 `kiln-net` 和数据卷（键名 `kiln-postgres-data`）
- 先等 `kiln-postgres` 健康，再启动 `kiln-api`，然后启动 `kiln-console`

API 镜像以 uid `10001` 的用户 `kiln` 运行。镜像构建不包含 `backend/tests`（见 `backend/.dockerignore`）。控制台构建使用 `npm ci`。

`kiln-console` 的 `depends_on` 只等待 API 容器启动，不等待 API 健康。API 健康检查有 `start_period: 10s`，间隔 5 秒，最多 20 次。控制台先起来时，经 `8080` 访问 `/health` 可能短暂失败，等 `kiln-api` 变为 healthy 后再查。

前台运行可以去掉 `-d`：

```bash
docker compose up --build
```

## 核对默认栈

```bash
docker compose ps
```

`kiln-postgres` 和 `kiln-api` 应显示 healthy。`kiln-console` 没有 healthcheck，状态为运行即可。

### API

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/
curl -fsS http://127.0.0.1:8000/api/v1/llm/provider
```

`/health` 的正文是：

```json
{"status":"ok","service":"kiln","version":"0.1.0"}
```

`/` 返回 `service`、`health`（`/health`）和 `docs`（`/docs`）。默认模型状态是 `provider` 为 `off`，`base_url` 和 `default_model` 为 null，两个 `*_configured` 字段为 false。

`/health` 不连接 Postgres，也不访问模型端点。它只能说明 API 进程在应答。

接口说明在 <http://127.0.0.1:8000/docs>。控制台的 nginx 只反代 `/api/` 和 `/health`，不反代 `/docs`。

### 控制台

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/
curl -fsS http://127.0.0.1:8080/health
```

浏览器打开 <http://127.0.0.1:8080>。概览页读取 `/health`。页面有概览、制品、Skill、账号与权限、对话。`console/nginx.conf` 把 `/api/` 和 `/health` 转到 compose 网络里的 `api:8000`，并把客户端请求体限制为 `1m`。直接访问宿主机 `8000` 不经过这道限制。

### Postgres

```bash
docker exec kiln-postgres pg_isready -U kiln -d kiln
```

若改过 `.env` 的 `POSTGRES_USER` 或 `POSTGRES_DB`，这里换成相同的值。成功时输出中含有 `accepting connections`。

这只说明数据库容器在接受连接。一期 API 不使用这条连接，重启 API 后账号、制品和 skill 仍然消失。连接串留在配置里，是为了后续实验室指向 Postgres。

## 账号

没有引导账号，没有登录页，也没有口令字段。控制台「账号与权限」在 API 尚未创建账号时显示「还没有账号」，并列出角色目录：`reader`（只读）、`builder`（构建者）、`publisher`（发布者）、`admin`（管理员）。这些角色目前不参与鉴权。

需要一条内存中的账号时，可以向 API 登记。用户名须匹配 `^[a-z][a-z0-9_-]{2,31}$`。进程重启后记录消失。

```bash
curl -fsS http://127.0.0.1:8000/api/v1/accounts \
  -H 'Content-Type: application/json' \
  -d '{"username":"ada","display_name":"Ada"}'
curl -fsS http://127.0.0.1:8000/api/v1/accounts
```

列表响应里 `persistence` 为 `memory`。空列表是 `{"persistence":"memory","items":[]}`。

## 可选：模型端点

默认 `LLM_PROVIDER=off`。此时对合法的对话请求，`POST /api/v1/llm/chat` 返回 503，正文说明模型未启用。

不访问网络的本地回复：

```bash
# 在 .env 中设置 LLM_PROVIDER=mock 后重新创建 API 容器
docker compose up --build -d api
```

`mock` 不需要 `LLM_BASE_URL`。

接入一个 OpenAI 兼容端点时，在 `.env` 中设置：

```bash
LLM_PROVIDER=openai
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_MODEL=替换为端点上的模型名
LLM_API_KEY=
```

`http://127.0.0.1:11434/v1` 只说明根路径的写法，与 README 中的示例相同。仓库不假设本机已经有这个端点。无鉴权端点保持 `LLM_API_KEY` 为空；有鉴权时只把密钥放在本机 `.env` 或进程环境中。

改完后重建 API 容器：

```bash
docker compose up --build -d api
curl -fsS http://127.0.0.1:8000/api/v1/llm/provider
```

Compose 把 `LLM_BASE_URL` 原样写进 `kiln-api`。容器里的 `127.0.0.1` 是容器自己，不是宿主机。仓库没有 `extra_hosts`，也没有规定从容器访问宿主机的主机名。模型若跑在别的机器或容器网络能路由到的地址上，把那个地址填进 `LLM_BASE_URL`。进程直接跑在宿主机上时，`127.0.0.1` 才指向宿主机。

对话请求是 `POST /api/v1/llm/chat`，正文为 `{"messages":[{"role":"user","content":"…"}]}`。未设置 `LLM_MODEL` 且请求里也没有 `model` 时，返回 400。超时使用 `LLM_TIMEOUT_SECONDS`。

## 可选：lab 占位（Jenkins 与 Gitea）

这一组容器不会接入 kiln。启动它们不能让 API 触发构建或读取代码仓库。代码里没有可以指向外部 Jenkins、GitLab 或 Gitea 的配置项。

```bash
docker compose --profile lab up --build -d
```

| 容器 | 镜像 | 宿主机端口 | 核对 |
| --- | --- | --- | --- |
| `kiln-jenkins` | `jenkins/jenkins:lts-jdk17` | `18080` → 容器 `8080` | 浏览器打开 <http://127.0.0.1:18080>，看到的是上游 Jenkins 自己的安装或登录页 |
| `kiln-gitea` | `gitea/gitea:1.22` | `13000` → 容器 `3000` | 浏览器打开 <http://127.0.0.1:13000>。首次进入的是 Gitea 自己的安装页 |

Jenkins 上游镜像把初始解锁口令放在容器内 `/var/jenkins_home/secrets/initialAdminPassword`。该文件由镜像首次启动时生成，仓库不保存它，kiln 也不读取它。Gitea 服务只设置了 `GITEA__database__DB_TYPE=sqlite3`，没有预置管理员。

是否做完这两个上游产品的安装向导，都不改变 kiln 的 API。生产代码源仍然是架构方案中的 GitLab；这里的 Gitea 只是 compose 里的占位。

## 不使用 Docker

在仓库根目录另开进程时，不启动 Postgres、Jenkins 或 Gitea。`DATABASE_URL` 仍不会被连接。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e "./backend[dev]"
uvicorn kiln.main:create_app --factory --host 127.0.0.1 --port 8000
```

另一个终端：

```bash
cd console
npm install
npm run dev
```

- API：<http://127.0.0.1:8000/health>，说明页 <http://127.0.0.1:8000/docs>
- 控制台：<http://127.0.0.1:5173>

`console/vite.config.js` 把开发服务器绑在 `127.0.0.1:5173`，并把 `/api` 与 `/health` 代理到 `http://127.0.0.1:8000`。配置从进程环境变量和当前工作目录的 `.env` 读取，因此这条 uvicorn 命令需要在仓库根目录执行，才能读到根目录的 `.env`。

镜像构建和 CI 使用 `npm ci`。上面的 `npm install` 是 README 中的本机开发命令。

## 停止

停掉包含 lab 在内的容器，并删除本次创建的网络 `kiln-net`。命名卷默认保留：

```bash
docker compose --profile lab down
```

同时删除命名卷（Postgres 数据目录；若启动过 lab，还包括 Jenkins home 和 Gitea 数据）：

```bash
docker compose --profile lab down -v
```

API 进程内存中的账号、制品和 skill 在容器重建或进程退出时已经消失，不在这些卷里。

## 常见失败

这些情况都来自当前 compose 和配置代码。

- **端口已被占用。** 默认栈需要 `5432`、`8000`、`8080`，lab 另需 `18080`、`13000`。Docker 无法绑定宿主机端口时，对应容器起不来。API 设置了 `depends_on.postgres.condition: service_healthy`，Postgres 起不来时 API 会一直等待。
- **容器名冲突。** `container_name` 是全局的。守护进程里已经有 `kiln-postgres`、`kiln-api`、`kiln-console`、`kiln-jenkins` 或 `kiln-gitea` 时，新的 `up` 会被拒绝。换一个 compose 项目名也不能绕过这些固定名字。
- **网络名冲突。** 网络固定叫 `kiln-net`。若同名网络已由别的来源创建且设置不兼容，compose 创建网络会失败。
- **`openai` 缺少根路径。** `LLM_PROVIDER=openai` 且 `LLM_BASE_URL` 为空时，API 进程在启动阶段抛出配置错误，健康检查不会变为 healthy。把提供方改回 `off` 或 `mock`，或填上从该容器能访问的根路径。
- **容器内的 `127.0.0.1`。** 见上文模型一节。宿主机上的模型端点不会因为写了回环地址就被容器访问到。
- **控制台短暂不可用。** `kiln-console` 不等待 API 健康。nginx 反代的上游是 Docker DNS 名 `api`。API 尚未监听时，`http://127.0.0.1:8080/health` 会失败；直接看 `8000`，或等 `docker compose ps` 里的 API 变为 healthy。
- **把 `/health` 当成全量依赖检查。** 返回 `ok` 只表示 API 进程在应答。它不代表 Postgres 已被使用，也不代表模型端点可达。
- **重启后数据不见。** 账号、制品、预约和 skill 都在 `MemoryStore` 中。这是本期实现，不是卷挂载失败。
- **改了源码但容器行为不变。** 运行中的镜像在构建时复制代码。`backend/Dockerfile` 复制 `pyproject.toml` 和 `kiln/`，`console/Dockerfile` 复制控制台源码后执行 `npm run build`。需要 `docker compose up --build`。
- **浏览器跨域。** 直接打开 API 时，来源必须落在 `KILN_CORS_ORIGINS` 里。容器内写死为 `http://localhost:8080` 和 `http://localhost:5173`。经控制台同源访问 `8080` 上的页面时，由 nginx 转发，不依赖这组 CORS。
- **共享机器。** 端口和容器名都是固定值，一套实例会占住这台 Docker 宿主机上的对应资源。

查看 API 容器日志：

```bash
docker compose logs api
```

## 启动本期骨架之后仍然没有的接线

下面这些在架构方案里有归属，但当前代码和 compose 都还不能配置它们。本文不给出虚构的接入步骤。

- **GitLab。** 生产代码源。没有地址、令牌、项目或检出配置。
- **Jenkins 执行器。** lab 容器是上游 Jenkins。API 没有 Jenkins 地址或任务配置。
- **Nexus。** 现网制品存储的集成目标。没有地址、仓库名或同步。取包接口只返回预约契约。
- **身份联邦。** 现网身份系统的产品名和地址尚未进入仓库。本地账号没有口令。
- **Kubernetes 与专用镜像仓库。** 镜像构建阶段的计划，仓库中没有清单。
- **把制品发布到运行环境。** 不在平台本期能力内。

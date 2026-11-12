# 部署说明

本文说明如何在一台新机器上启动当前仓库里的一期骨架。范围只包括把已有进程跑起来并核对它们。平台不负责把制品发布到运行环境。架构取舍以 [README](../README.md) 所链的架构方案为准。仓库没有写出的地址、口令、主机名和任务名，本文留空。

当前版本号是 `0.1.0`（`backend/kiln/__init__.py` 的 `__version__`）。`GET /health` 返回的 `version` 与它一致。

## 依赖系统

| 系统 | 档位 | 当前仓库里实际有什么 |
| --- | --- | --- |
| Docker Engine 与 Compose V2 | 现在需要（走 Compose 时） | 根目录 `docker-compose.yml`。CI 用 `docker compose config` 校验。文末的进程方式不使用 Docker |
| PostgreSQL | 现在需要 | 服务 `postgres`，镜像 `postgres:16-alpine`，容器 `kiln-postgres`，数据卷键名 `kiln-postgres-data`。API 用 `DATABASE_URL` 连接，启动时执行迁移。账号、RBAC 目录、制品、版本、下载预约和 skill 写在这个库里。数据库不可达时 API 起不来 |
| kiln API | 现在需要 | 服务 `api`，本地镜像 `kiln-api:local`，容器 `kiln-api`。基础镜像 `python:3.11-slim`，监听容器内 `8000` |
| 控制台 | 现在需要 | 服务 `console`，本地镜像 `kiln-console:local`，容器 `kiln-console`。构建阶段 `node:22-alpine`，运行阶段 `nginx:1.27-alpine`，容器内 `80` 映射到宿主机 `8080` |
| Jenkins 兼容执行器与产品构建 | 按环境变量接线 | API 在 `JENKINS_URL`、`JENKINS_USER`、`JENKINS_API_TOKEN` 都有值时，向该 Jenkins 创建兼容任务并读取状态和日志。再配上代码源后，产品构建会检出仓库、运行 `kiln-build.sh`，并把 `kiln-dist/artifact.bin` 写入平台目录。不部署到运行环境。profile `lab` 的 `jenkins/jenkins:lts-jdk17`、容器 `kiln-jenkins`、卷 `kiln-jenkins-home`、端口 `127.0.0.1:18080` 仍是本仓库里的占位，不是共享实验机上的实例 |
| Git 远端 | 可替换适配器 | `GIT_PROVIDER=gitlab` 是生产代码源，`GIT_PROVIDER=gitea` 是实验室实现。地址只来自 `GIT_BASE_URL`，令牌只来自 `GIT_TOKEN`。仓库不写生产 GitLab 地址。profile `lab` 的 `gitea/gitea:1.22`（容器 `kiln-gitea`）仍是占位，不是生产代码源 |
| 制品存储 | 目录在 PostgreSQL，字节在平台本地目录 | 制品、版本和下载预约写在 Postgres。字节写在 `KILN_ARTIFACT_ROOT`。compose 把它挂到命名卷 `kiln-artifact-data`，容器内路径是 `/var/lib/kiln/artifacts`。版本保存字节后 `content_available` 为 true，`GET /api/v1/retrieve/{reservation_id}/content` 返回这些字节。包类型字段有 `maven`、`python`、`generic`、`frontend`。Nexus 集成还没有写入配置，仓库里没有 Nexus 地址 |
| OpenAI 兼容模型端点 | 可选 | `LLM_PROVIDER` 默认 `off`。`openai` 会 `POST {LLM_BASE_URL}/chat/completions`。`mock` 不访问网络 |
| 本地登录与用户信息同步 | 这一版已接上起步 | 平台自身具备完整的账号与权限能力。本地账号、口令哈希、会话和 RBAC 目录在 Postgres 中。控制台有登录页。未配置公司用户系统时使用本地口令登录。`KILN_API_TOKEN` 仍是必填的机器和开发访问令牌，拥有目录中的全部权限。公司用户系统的对接是可选的。可选对接之后，公司系统只同步身份和在职状态，kiln 的授权仍留在平台上 |
| Harbor、Kubernetes、专用镜像仓库 | 不属于一期 | 没有清单、没有镜像推送。架构方案把镜像构建放在一期之后 |

默认命令 `docker compose up` 只启动 Postgres、API、控制台。profile `lab` 里的 Jenkins 和 Gitea 仍是占位。不要在共享实验机上加 `--profile lab`，也不要在那台机器上启动这份 compose。执行器和代码源通过环境变量指向已经在跑的服务。

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
- 磁盘要放下镜像层、构建缓存，以及命名卷。项目名是 `kiln`，卷在 Docker 里通常显示为 `kiln_kiln-postgres-data`、`kiln_kiln-artifact-data`、`kiln_kiln-jenkins-home`、`kiln_kiln-gitea-data`。API 把账号、权限目录、制品元数据、预约和 skill 写进 Postgres。制品字节写进 `kiln-artifact-data`。数据库卷里是这些表，外加迁移记录。Jenkins 数据卷只在启动过 lab 之后出现。

系统与 Docker：

- 需要能构建并运行上述 Linux 镜像的 64 位环境。CI 使用 GitHub Actions 的 `ubuntu-latest`。仓库不指定发行版，镜像没有钉死 CPU 架构，也不使用 GPU。
- 需要 Docker Engine 和 Compose V2。`docker compose version` 应能执行。文件用了 `profiles` 和 `depends_on.condition: service_healthy`，Compose V1 的 `docker-compose` 不适用。仓库没有钉死 Docker 的版本号。
- 默认栈把宿主机端口 `5432`、`8000`、`8080` 只绑定到 `127.0.0.1`。lab 的 `18080`、`13000` 同样只绑定 `127.0.0.1`。容器内 API 仍监听 `0.0.0.0`，那是容器自己的网卡，不是宿主机的所有网卡。
- 容器名是固定的：`kiln-postgres`、`kiln-api`、`kiln-console`、`kiln-jenkins`、`kiln-gitea`。网络名固定为 `kiln-net`。同一 Docker 守护进程上不能再起一套同名容器。
- Compose 没有把宿主机目录或 Docker 套接字挂进容器，也没有 `DOCKER_HOST`。改代码后需要重新构建镜像。

不要在已经占用这些端口或容器名的共享机器上启动。

## 前提

在仓库根目录操作。机器上需要：

- Git
- 上文的 Docker Engine 与 Compose V2
- 能拉取上一节列出的基础镜像
- 默认栈的三个宿主机端口在 `127.0.0.1` 上是空闲的。准备启动 lab 时，`127.0.0.1` 上的 `18080` 和 `13000` 也要空闲

进程方式（不使用 Docker）另需 Python 3.11 或更高，以及 Node.js 22。`backend/pyproject.toml` 要求 `requires-python >= 3.11`。CI 和 `console/Dockerfile` 使用 Node.js 22；`console/package.json` 没有 `engines` 字段。

## 获取代码

当前仓库位于 GitHub。生产代码源适配器是 GitLab：把 `GIT_PROVIDER=gitlab` 和 `GIT_BASE_URL` 放在环境里即可，仓库不写这个地址。实验室用 `GIT_PROVIDER=gitea`。

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
| `KILN_HTTP_PORT` | `8000` | 同上。改这个值不会改变 compose 发布的 `127.0.0.1:8000:8000` |
| `KILN_CORS_ORIGINS` | `http://localhost:5173,http://localhost:8080` | 进程直接运行时生效。compose 给容器写死了同一组来源，不读取 `.env` 里的这一行 |
| `DATABASE_URL` | `postgresql://kiln:kiln@localhost:5432/kiln` | 进程直接运行时进入配置。容器内的值由 compose 拼成 `postgresql://…@postgres:5432/kiln`，主机名是服务名 `postgres` |
| `KILN_API_TOKEN` | `kiln-local` | `/api/v1` 的 Bearer 令牌。compose 在未设置时传入 `kiln-local`。这个值只用于本地，不能用于生产。留空时 API 在加载配置时失败 |
| `POSTGRES_USER` | `kiln` | 只由 compose 插值，传给 Postgres 容器，并用来拼接容器内的 `DATABASE_URL` |
| `POSTGRES_PASSWORD` | `kiln` | 同上 |
| `POSTGRES_DB` | `kiln` | 同上 |
| `LLM_PROVIDER` | `off` | compose 会传入 API 容器。`openai`、`mock`、`off`。`none` 和 `disabled` 视为 `off`，`openai-compatible` 视为 `openai` |
| `LLM_BASE_URL` | 空 | `openai` 时必填。compose 原样传入 |
| `LLM_API_KEY` | 空 | compose 原样传入。只从环境读取 |
| `LLM_MODEL` | 空 | 默认模型。请求体里也可以传 `model` |
| `LLM_TIMEOUT_SECONDS` | `30` | 允许范围大于 0 且不超过 300 |
| `JENKINS_URL` | 空 | compose 原样传入 API。空则不连接 Jenkins。须是 http 或 https，不能带用户名、口令、查询串 |
| `JENKINS_USER` | 空 | 与 URL、令牌一起设置。compose 原样传入 |
| `JENKINS_API_TOKEN` | 空 | Jenkins API 令牌或该用户口令。只放在环境或本机 `.env`，不能提交 |
| `JENKINS_TIMEOUT_SECONDS` | `30` | 单次调用超时，大于 0 且不超过 120 |
| `GIT_PROVIDER` | `off` | `gitea`、`gitlab` 或 `off`。compose 未设置时传入 `off` |
| `GIT_BASE_URL` | 空 | `gitea` 或 `gitlab` 时必填。compose 原样传入。没有生产默认值 |
| `GIT_CLONE_BASE_URL` | 空 | Jenkins 用来克隆的代码源根地址。留空则使用 `GIT_BASE_URL`。compose 原样传入。不能带用户名或口令，也没有生产默认值 |
| `GIT_TOKEN` | 空 | `gitea` 或 `gitlab` 时必填。只从环境读取 |
| `GIT_TIMEOUT_SECONDS` | `30` | 单次调用超时，大于 0 且不超过 120 |
| `KILN_ARTIFACT_ROOT` | 进程方式默认 `var/kiln-artifacts`；compose 固定为 `/var/lib/kiln/artifacts` | 平台自管制品字节的本地目录。compose 不读取 `.env` 里的这一行，而是写死容器内路径并挂载命名卷 `kiln-artifact-data` |
| `KILN_ARTIFACT_MAX_BYTES` | `67108864` | 单次保存的字节上限。compose 在未设置时传入这个值 |
| `KILN_OIDC_ISSUER_URL` | 空 | 用户信息同步的发行方地址。留空则只使用本地口令。compose 原样传入。不要把公司用户系统的地址写进仓库 |
| `KILN_OIDC_CLIENT_ID` | 空 | 用户信息同步的客户端标识。任一项必填变量有值时必须同时填写 |
| `KILN_OIDC_CLIENT_SECRET` | 空 | 用户信息同步的客户端密钥。只从环境读取 |
| `KILN_OIDC_REDIRECT_URI` | 空 | 授权码回调地址。配置时必填 |
| `KILN_OIDC_EMPLOYMENT_CLAIM` | 空 | 在职状态声明的名称。配置时必填 |
| `KILN_OIDC_EMPLOYMENT_ACTIVE_VALUES` | 空 | 视为在职的声明值，逗号分隔。配置时必填 |
| `KILN_OIDC_SCOPES` | 空 | 仅在已配置时生效。留空则使用 `openid profile email` |
| `KILN_SESSION_TTL_SECONDS` | `43200` | 用户会话有效期，默认 12 小时 |
| `KILN_SESSION_COOKIE_SECURE` | `false` | 为 true 时会话 cookie 带 Secure。本地 http 保持 false |

已经导出的同名环境变量会覆盖 `.env`。Compose 插值是这样，pydantic-settings 也是这样。

`JENKINS_URL` 和 `GIT_BASE_URL` 由这一版的 API 读取。`GIT_CLONE_BASE_URL` 只在 Jenkins 看到的代码源地址和 API 不同时才填，留空则克隆地址与 `GIT_BASE_URL` 相同。GitLab 仍是生产代码源，Gitea 是实验室适配器，两者都只使用你填入的地址。制品字节由平台写到 `KILN_ARTIFACT_ROOT`，不依赖 Nexus。Nexus 集成仍属于一期，这一版没有 Nexus 客户端，也没有 Nexus 地址。本地登录，以及用户信息同步的起步已经接上。平台自身具备完整的账号与权限能力。公司用户系统的对接是可选的。可选对接之后，公司系统只同步身份和在职状态，kiln 的授权仍留在平台上。Harbor、Kubernetes 和专用镜像仓库不属于一期。平台不把制品部署到运行环境。

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
- `kiln-api` 启动时连接数据库并执行迁移，然后写入 RBAC 目录。不需要单独的迁移命令

API 镜像的入口先保证 `KILN_ARTIFACT_ROOT` 可由 uid `10001` 写入，再以用户 `kiln` 运行 API 进程。镜像构建不包含 `backend/tests`（见 `backend/.dockerignore`）。控制台构建使用 `npm ci`。

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
curl -fsS http://127.0.0.1:8000/api/v1/llm/provider \
  -H 'Authorization: Bearer kiln-local'
```

`/health` 和 `/` 不需要令牌。`/api/v1/llm/provider` 需要。未带令牌时返回 401，正文是 `需要访问令牌`。`kiln-local` 只用于本地。

`/health` 的正文是：

```json
{"status":"ok","service":"kiln","version":"0.1.0"}
```

`/` 返回 `service`、`health`（`/health`）和 `docs`（`/docs`）。默认模型状态是 `provider` 为 `off`，`base_url` 和 `default_model` 为 null，两个 `*_configured` 字段为 false。

`/health` 不查询 Postgres，也不访问模型端点。返回 `ok` 只说明 API 进程在应答。进程能启动，表示启动当时的迁移已经完成；之后数据库断开时，`/health` 仍可能返回 `ok`。

接口说明在 <http://127.0.0.1:8000/docs>。控制台的 nginx 只反代 `/api/` 和 `/health`，不反代 `/docs`。

### 控制台

```bash
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/
curl -fsS http://127.0.0.1:8080/health
```

浏览器打开 <http://127.0.0.1:8080>。概览页读取 `/health`。页面有概览、构建、制品、Skill、账号与权限、对话。`console/nginx.conf` 把 `/api/` 和 `/health` 转到 compose 网络里的 `api:8000`，并把客户端请求体限制为 `1m`。直接访问宿主机 `8000` 不经过这道限制。

### Postgres

```bash
docker exec kiln-postgres pg_isready -U kiln -d kiln
```

若改过 `.env` 的 `POSTGRES_USER` 或 `POSTGRES_DB`，这里换成相同的值。成功时输出中含有 `accepting connections`。

这只说明数据库容器在接受连接。API 使用这条连接保存领域数据。制品字节在命名卷 `kiln-artifact-data` 里。重启 API 容器后，账号、制品元数据、预约、skill 和已保存的字节都还在；`docker compose down -v` 会连这些数据卷一起删除。

可以这样核对重启后记录还在。下面的用户名须匹配 `^[a-z][a-z0-9_-]{2,31}$`。

```bash
curl -fsS http://127.0.0.1:8000/api/v1/accounts \
  -H 'Authorization: Bearer kiln-local' \
  -H 'Content-Type: application/json' \
  -d '{"username":"ada","display_name":"Ada"}'
docker compose restart api
```

等 `kiln-api` 再次变为 healthy 后再查列表。列表响应里 `persistence` 为 `postgres`。空列表是 `{"persistence":"postgres","items":[]}`。重启前创建的 `ada` 仍在 `items` 里。

```bash
curl -fsS http://127.0.0.1:8000/api/v1/accounts \
  -H 'Authorization: Bearer kiln-local'
```

不带令牌再查一次，应得到 401，库里的 `ada` 不会因此消失。未带令牌也不能保存或下载制品字节。

保存并取回一组字节时，先登记制品，再 `PUT` 原始字节。校验和是这组字节的 sha256。下面的示例只说明调用形状；`kiln-local` 只用于本地。

```bash
printf 'kiln' > /tmp/kiln-sample.bin
SUM=$(sha256sum /tmp/kiln-sample.bin | awk '{print $1}')
ART=$(curl -fsS http://127.0.0.1:8000/api/v1/artifacts \
  -H 'Authorization: Bearer kiln-local' \
  -H 'Content-Type: application/json' \
  -d '{"name":"sample-pkg","package_type":"generic"}')
ART_ID=$(printf '%s' "$ART" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -fsS "http://127.0.0.1:8000/api/v1/artifacts/${ART_ID}/versions/1/content" \
  -X PUT \
  -H 'Authorization: Bearer kiln-local' \
  -H 'Content-Type: application/octet-stream' \
  -H 'X-Kiln-Checksum-Algorithm: sha256' \
  -H "X-Kiln-Checksum: ${SUM}" \
  --data-binary @/tmp/kiln-sample.bin
```

响应里 `content_available` 为 true。预约之后，`GET /api/v1/retrieve/{reservation_id}/content` 返回同一组字节。只重启 API 进程不会丢掉这个文件；删掉 `kiln-artifact-data` 才会。

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/v1/accounts
```

## 账号

控制台有登录页。创建账号时可以设置口令，库里只保存哈希。会话写在 Postgres，退出时撤销。未配置公司用户系统时使用本地口令。配置 `KILN_OIDC_*` 之后，用户信息同步按发行方和 subject 找到或创建 kiln 账号，新账号默认角色是 `reader`，不复制公司侧的组或角色。在职状态未通过或账号已停用时不建立会话。平台自身具备完整的账号与权限能力。公司用户系统的对接是可选的。可选对接之后，公司系统只同步身份和在职状态，kiln 的授权仍留在平台上。`KILN_API_TOKEN` 仍是机器和开发用的访问令牌，拥有目录中的全部权限。侧栏里的令牌输入不再是主要入口。控制台「账号与权限」在 API 尚未创建账号时显示「还没有账号」，并列出角色目录：`reader`（只读）、`builder`（构建者）、`publisher`（发布者）、`admin`（管理员）。这些角色参与写入鉴权。没有对应权限时返回 403。发起任务和产品构建需要 `executor.run`，对话需要 `agent.qa`。只读角色没有这两项，调用 `POST /api/v1/jobs`、`POST /api/v1/builds` 或 `POST /api/v1/llm/chat` 会返回 403。列出和查看账号、制品、skill 分别需要 `account.read`、`artifact.read`、`skill.read`，只读角色已有这三项。构建者可以提交 skill 审核（`skill.submit`），只读角色不能。发布仍由发布者和管理员负责（`skill.publish`）。退回草稿同样需要 `skill.publish`，须填写原因。查看执行器状态、任务、产品构建及其日志需要 `executor.read`，查看代码源适配器、仓库列表并解析引用需要 `git.read`。这两项授给构建者、发布者和管理员，不授给只读角色。

需要一条账号时，可以向 API 登记。用户名须匹配 `^[a-z][a-z0-9_-]{2,31}$`。记录写在 Postgres 里，只重启 API 进程不会消失。上文 Postgres 一节有一组创建后重启再读取的命令。

## 可选：模型端点

默认 `LLM_PROVIDER=off`。此时持有 `agent.qa` 的对话请求 `POST /api/v1/llm/chat` 返回 503，正文说明模型未启用。只读角色返回 403。未登录返回 401。

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
curl -fsS http://127.0.0.1:8000/api/v1/llm/provider \
  -H 'Authorization: Bearer kiln-local'
```

Compose 把 `LLM_BASE_URL` 原样写进 `kiln-api`。容器里的 `127.0.0.1` 是容器自己，不是宿主机。仓库没有 `extra_hosts`，也没有规定从容器访问宿主机的主机名。模型若跑在别的机器或容器网络能路由到的地址上，把那个地址填进 `LLM_BASE_URL`。进程直接跑在宿主机上时，`127.0.0.1` 才指向宿主机。

对话请求是 `POST /api/v1/llm/chat`，需要 `agent.qa`，正文为 `{"messages":[{"role":"user","content":"…"}]}`。未设置 `LLM_MODEL` 且请求里也没有 `model` 时，返回 400。超时使用 `LLM_TIMEOUT_SECONDS`。对话可在授权下通过工具起草 skill 草稿，或发起与 POST /builds 相同的产品构建。

## Jenkins 与代码源

执行器只在 `JENKINS_URL`、`JENKINS_USER`、`JENKINS_API_TOKEN` 都非空时工作。代码源只在 `GIT_PROVIDER` 为 `gitea` 或 `gitlab` 且 `GIT_BASE_URL`、`GIT_TOKEN` 都有值时工作。缺任何一项时，API 仍然启动。已具备 `executor.run` 时，`POST /api/v1/jobs` 返回 503；已具备 `git.read` 且代码源未配置时，`GET /api/v1/git/repositories` 返回 503。只读角色调用 `POST /api/v1/jobs` 或 `POST /api/v1/builds` 返回 403，即使 Jenkins 或代码源未配置，也不会写入任务或构建记录。读取执行器状态、任务、产品构建及其日志需要 `executor.read`，读取代码源需要 `git.read`。只读角色没有这两项，这些 GET 返回 403，正文是「没有权限」，不含 `JENKINS_URL` 或「代码源未配置」。构建者、发布者、管理员和机器令牌在未配置时，任务、构建、仓库列表和解析引用返回 503。`GET /api/v1/executor` 和 `GET /api/v1/git/source` 对具备对应读取权限的调用返回未配置（`configured` 为 false），并且代码源响应里的 `production_provider` 固定为 `gitlab`。

这些调用接受会话 cookie、登录返回的 Bearer 令牌，或 `Authorization: Bearer <KILN_API_TOKEN>`。`POST /api/v1/jobs` 与 `POST /api/v1/builds` 还需要 `executor.run`。读取任务、构建和执行器状态还需要 `executor.read`，读取代码源还需要 `git.read`。没有凭证时返回 401，不会到 Jenkins 或代码源上去创建任务。机器令牌拥有目录中的全部权限。

进程直接跑在宿主机上时，`JENKINS_URL=http://127.0.0.1:8080` 指向这台机器的 8080。API 若跑在 compose 的 `kiln-api` 里，容器内的 `127.0.0.1` 是容器自己，不是宿主机。把 Jenkins 或 Gitea 填成从该容器能路由到的地址。

创建兼容任务的请求体是 `{"name":"compat","marker":"kiln-ok"}`。`marker` 只能是字母、数字和 `._:-`。kiln 在 Jenkins 上建一个自由风格任务，让它打印这个标记，然后 `GET /api/v1/jobs/{id}` 看状态，`GET /api/v1/jobs/{id}/log` 读日志。这只用来核对日志，不产出制品字节，也不部署到运行环境。

产品构建的请求体是 `{"repository":"owner/name","ref":"main","artifact_name":"sample-pkg","version":"1.0.0","package_type":"generic"}`。仓库根目录要有 `kiln-build.sh`，成功时写出 `kiln-dist/artifact.bin`。kiln 先用当前适配器把 `ref` 解析成提交，再让 Jenkins 克隆该提交并运行脚本。`GIT_CLONE_BASE_URL` 留空时，克隆根地址就是 `GIT_BASE_URL`。构建成功后，`GET /api/v1/builds/{id}` 里 `content_available` 为 true，字节在 `KILN_ARTIFACT_ROOT`。预约之后，`GET /api/v1/retrieve/{reservation_id}/content` 返回同一组字节。响应里 `deploys_to_runtime` 为 false。令牌只放在环境里，不写入数据库，也不出现在返回的日志里。

解析代码引用是 `GET /api/v1/git/commits?repository=owner/name&ref=main`。`gitea` 走 Gitea 的 `/api/v1/`，`gitlab` 走 GitLab 的 `/api/v4/`。Gitea 1.24 解析单个提交用的是 `/git/commits/{ref}`，响应字段是 `sha`。

### 共享实验机上已经在跑的实验室

不要在那台机器上执行本仓库的 `docker compose up`。实验室容器名是 `kiln-postgres`、`kiln-gitea`、`kiln-jenkins`，宿主机端口包括 `5432`、`3000`、`2222`、`8080`、`50000`。本仓库 compose 使用同样的容器名，并且控制台也要发布 `127.0.0.1:8080`。

实验室的 HTTP 端点是：

| 服务 | 容器 | 本机地址 |
| --- | --- | --- |
| PostgreSQL | `kiln-postgres` | `127.0.0.1:5432`，库和用户名为 `kiln` |
| Gitea | `kiln-gitea` | `http://127.0.0.1:3000/`，API 在 `/api/v1/`，健康检查在 `/api/healthz` |
| Jenkins | `kiln-jenkins` | `http://127.0.0.1:8080/` |

口令、令牌不在仓库里。在那台机器上把 kiln 进程的 `JENKINS_*` 和 `GIT_PROVIDER=gitea`、`GIT_BASE_URL`、`GIT_TOKEN` 指到上表即可。生产 GitLab 的地址仍然不要写进仓库；要用 GitLab 时，把 `GIT_PROVIDER=gitlab` 和当时的 `GIT_BASE_URL` 放在环境里。

同一批测试也可以指到这个实验室，而不是 CI 的一次性容器：

```bash
KILN_TEST_JENKINS_URL=http://127.0.0.1:8080 \
KILN_TEST_JENKINS_USER=这里换成实验室用户 \
KILN_TEST_JENKINS_API_TOKEN=这里换成环境里的令牌 \
KILN_TEST_GIT_BASE_URL=http://127.0.0.1:3000 \
KILN_TEST_GIT_TOKEN=这里换成环境里的令牌 \
KILN_TEST_GIT_PROVIDER=gitea \
python -m pytest backend/tests/test_executor.py backend/tests/test_git_source.py backend/tests/test_product_build.py
```

如果这台机器上的 Jenkins 打不开 `KILN_TEST_GIT_BASE_URL`（例如 Jenkins 在容器里，而 Gitea 只绑在宿主机的回环地址上），再设置 `KILN_TEST_GIT_CLONE_BASE_URL` 为 Jenkins 能访问的同一套 Gitea。不要把这个值写回仓库。

上面的「这里换成」不要写回仓库。Gitea 令牌需要能创建并读取仓库，因为测试会建一个名字以 `kiln-src-` 开头的仓库，结束时再删掉。Jenkins 测试会建一个名字以 `kiln-` 开头的任务，结束时再删掉。产品构建测试还会让 Jenkins 克隆该仓库并取回 `kiln-dist/artifact.bin`。

### 仓库里的 lab 占位

`docker compose --profile lab` 仍然会尝试启动 `kiln-jenkins` 和 `kiln-gitea`。不要在共享实验机上这样做。占位镜像是 `jenkins/jenkins:lts-jdk17` 和 `gitea/gitea:1.22`，端口是 `127.0.0.1:18080` 和 `127.0.0.1:13000`。它们没有预置管理员。即使在一台空机器上把占位启动起来，只要没有把 `JENKINS_*` 和 `GIT_*` 指过去，kiln 也不会调用它们。

### CI 里的一次性 Jenkins 与 Gitea

GitHub Actions 不使用上面的容器名和端口。`scripts/ci-external-services.sh` 启动 `kiln-ci-jenkins`、`kiln-ci-gitea`，卷是 `kiln-ci-jenkins-data`、`kiln-ci-gitea-data`，网络是 `kiln-ci-net`，端口是 `127.0.0.1:28080` 和 `127.0.0.1:23000`。镜像是 `jenkins/jenkins:2.580.2-lts-alpine` 和 `gitea/gitea:1.24.7`。口令在作业里生成。Jenkins 通过该网络上的 `http://kiln-ci-gitea:3000` 克隆，不另开宿主机端口。这个脚本拒绝使用实验室的容器名、卷名、网络名 `kiln-net` 和端口。

## 不使用 Docker

在仓库根目录另开进程时，不启动 Jenkins 或 Gitea，但 API 仍然要能连上 `DATABASE_URL` 指向的 PostgreSQL。默认值是本机 `localhost:5432` 的库 `kiln`。进程启动时执行迁移。没有数据库时 API 起不来。

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

同时删除命名卷（Postgres 数据目录和制品字节；若启动过 lab，还包括 Jenkins home 和 Gitea 数据）：

```bash
docker compose --profile lab down -v
```

只停容器或只重建 API 时，Postgres 卷里的账号、制品元数据、预约和 skill 还在，制品字节卷里的文件也还在。加上 `-v` 才会删掉这些数据。

## 常见失败

这些情况都来自当前 compose 和配置代码。

- **端口已被占用。** 默认栈在 `127.0.0.1` 上需要 `5432`、`8000`、`8080`，lab 另需 `18080`、`13000`。这些端口不向其他机器发布。Docker 无法绑定宿主机端口时，对应容器起不来。API 设置了 `depends_on.postgres.condition: service_healthy`，Postgres 起不来时 API 会一直等待。
- **容器名冲突。** `container_name` 是全局的。守护进程里已经有 `kiln-postgres`、`kiln-api`、`kiln-console`、`kiln-jenkins` 或 `kiln-gitea` 时，新的 `up` 会被拒绝。换一个 compose 项目名也不能绕过这些固定名字。
- **网络名冲突。** 网络固定叫 `kiln-net`。若同名网络已由别的来源创建且设置不兼容，compose 创建网络会失败。
- **`openai` 缺少根路径。** `LLM_PROVIDER=openai` 且 `LLM_BASE_URL` 为空时，API 进程在启动阶段抛出配置错误，健康检查不会变为 healthy。把提供方改回 `off` 或 `mock`，或填上从该容器能访问的根路径。
- **容器内的 `127.0.0.1`。** 见上文模型一节。宿主机上的模型端点不会因为写了回环地址就被容器访问到。
- **控制台短暂不可用。** `kiln-console` 不等待 API 健康。nginx 反代的上游是 Docker DNS 名 `api`。API 尚未监听时，`http://127.0.0.1:8080/health` 会失败；直接看 `8000`，或等 `docker compose ps` 里的 API 变为 healthy。
- **把 `/health` 当成全量依赖检查。** 返回 `ok` 只表示 API 进程在应答。它不重新探测 Postgres，也不代表模型端点可达。进程若从未变成 healthy，先看启动日志里的迁移或连接错误。
- **数据库未就绪或迁移失败。** API 启动时执行迁移。Postgres 未接受连接、口令不对或库不存在时，进程退出，健康检查不会变为 healthy。看 `docker compose logs api`。
- **重启 API 后数据还在，删卷后才消失。** 账号、制品元数据、预约和 skill 在 Postgres 卷里。制品字节在 `kiln-artifact-data`。`docker compose restart api` 不会清掉它们。`docker compose down -v` 会。
- **改了源码但容器行为不变。** 运行中的镜像在构建时复制代码。`backend/Dockerfile` 复制 `pyproject.toml` 和 `kiln/`，`console/Dockerfile` 复制控制台源码后执行 `npm run build`。需要 `docker compose up --build`。
- **浏览器跨域。** 直接打开 API 时，来源必须落在 `KILN_CORS_ORIGINS` 里。容器内写死为 `http://localhost:8080` 和 `http://localhost:5173`。经控制台同源访问 `8080` 上的页面时，由 nginx 转发，不依赖这组 CORS。
- **共享机器。** 端口和容器名都是固定值，一套实例会占住这台 Docker 宿主机上的对应资源。

查看 API 容器日志：

```bash
docker compose logs api
```

本地登录，以及用户信息同步的起步已经接上。控制台有登录页，口令只保存哈希。未配置公司用户系统时使用本地口令。配置之后，公司系统只同步身份和在职状态，kiln 的授权仍留在平台上。`KILN_API_TOKEN` 仍是必填的机器和开发访问令牌。

## 这一版还没有，但仍属于一期

下面这些按架构方案仍是一期要完成的事。这一版没有做它们，本文也不给出虚构的接入步骤。

- **Nexus。** 现网制品库。没有地址、仓库名或同步。平台自己的本地目录已经能保存并取回字节。

产品构建、Jenkins 兼容执行器和可替换的代码源适配器在这一版。产品构建从代码仓库检出提交，在 Jenkins 上运行 `kiln-build.sh`，把字节存进平台目录，取包返回这些字节。生产代码源适配器的类型是 GitLab，实验室实现是 Gitea。两边的地址都只来自环境变量。兼容任务仍然只打印标记。

## 不属于一期

- **Harbor。** 仓库中没有清单。
- **Kubernetes 镜像构建。** 仓库中没有清单。
- **专用镜像仓库。** 没有镜像推送。
- **把制品发布到运行环境。** 平台不把制品部署到运行环境。执行器响应里的 `deploys_to_runtime` 为 false。

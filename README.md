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
| Jenkins 兼容执行器与产品构建 | 现在可以接线 | 配置 `JENKINS_URL`、`JENKINS_USER`、`JENKINS_API_TOKEN` 后，API 能创建兼容任务并跟踪日志，也能从代码仓库检出提交、在 Jenkins 上运行该提交里的 `kiln-build.sh`，把产出的字节写入 `KILN_ARTIFACT_ROOT`。不把制品部署到运行环境。仓库 compose 的 profile `lab` 仍是占位，不是共享实验机上已经在跑的 Jenkins |
| Git 远端 | 可替换适配器 | `GIT_PROVIDER=gitea` 是实验室实现，对着配置的 `GIT_BASE_URL` 调用 Gitea HTTP API。`GIT_PROVIDER=gitlab` 是生产代码源，调用 GitLab HTTP API。仓库不写生产 GitLab 地址。compose 里的 `gitea/gitea:1.22` 仍是未作为生产源的占位 |
| 制品目录 | 目录、版本和字节由平台保存 | 元数据在 PostgreSQL。字节在平台自管的本地目录 `KILN_ARTIFACT_ROOT`（compose 命名卷 `kiln-artifact-data`）。版本保存字节后 `content_available` 为 true，`GET /api/v1/retrieve/{reservation_id}/content` 返回这些字节。Nexus 集成仍属于一期，这一版没有 Nexus 地址或客户端 |
| OpenAI 兼容模型端点 | 可选 | `LLM_PROVIDER` 默认 `off`。设为 `openai` 时必须提供 `LLM_BASE_URL` |
| 本地登录与用户信息同步 | 这一版已接上起步 | 平台自身具备完整的账号与权限能力。本地账号、口令哈希、会话和 RBAC 目录在 Postgres。控制台有登录页。未配置公司用户系统时使用本地口令登录。`KILN_API_TOKEN` 仍是必填的机器和开发访问令牌，拥有目录中的全部权限。公司用户系统的对接是可选的。可选对接之后，公司系统只同步身份和在职状态，kiln 的授权仍留在平台上 |
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

不要在共享实验机上启动这个文件。那台机器上已经有 `kiln-postgres`、`kiln-gitea`、`kiln-jenkins`，Jenkins 占用了宿主机 `8080`，和本文件的控制台端口冲突。

Jenkins 和 Gitea 不随默认命令启动。profile `lab` 里的 `kiln-jenkins`、`kiln-gitea` 仍是占位，端口是 `18080` 和 `13000`，而且没有预置令牌。kiln 不靠启动这组占位来工作。执行器和代码源看 `JENKINS_*`、`GIT_*`。Gitea 只是实验室适配器，生产代码源是 GitLab，地址由 `GIT_BASE_URL` 提供，仓库里不写。

```bash
docker compose --profile lab up
```

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
| `JENKINS_URL` | 空 | Jenkins 根地址，例如共享实验机上的 `http://127.0.0.1:8080`。空则不连接。不能在地址里带用户名或口令 |
| `JENKINS_USER` | 空 | Jenkins 用户。与 URL、令牌一起设置 |
| `JENKINS_API_TOKEN` | 空 | Jenkins API 令牌或该用户的口令。只从环境读取，不能写入仓库 |
| `JENKINS_TIMEOUT_SECONDS` | `30` | 单次 Jenkins HTTP 调用超时，最大 120 |
| `GIT_PROVIDER` | `off` | `gitea`（实验室）、`gitlab`（生产代码源）或 `off`。`none`、`disabled` 和空值视为 `off` |
| `GIT_BASE_URL` | 空 | 代码源根地址。`gitea` 或 `gitlab` 时必填。仓库不提供生产默认值 |
| `GIT_CLONE_BASE_URL` | 空 | Jenkins 用来克隆的代码源根地址。留空则使用 `GIT_BASE_URL`。API 和 Jenkins 不在同一网络视角时才需要，例如容器网络里的地址。不能带用户名或口令，也不是生产地址 |
| `GIT_TOKEN` | 空 | 代码源访问令牌。`gitea` 或 `gitlab` 时必填。只从环境读取 |
| `GIT_TIMEOUT_SECONDS` | `30` | 单次代码源 HTTP 调用超时，最大 120 |
| `KILN_ARTIFACT_ROOT` | `var/kiln-artifacts` | 平台自管制品字节的本地目录。相对路径按进程工作目录解析。不是 Nexus 地址 |
| `KILN_ARTIFACT_MAX_BYTES` | `67108864` | 单次保存的字节上限 |
| `KILN_OIDC_ISSUER_URL` | 空 | 用户信息同步的发行方地址。留空则只使用本地口令。不要把公司用户系统的地址写进仓库 |
| `KILN_OIDC_CLIENT_ID` | 空 | 用户信息同步的客户端标识。配置时必填 |
| `KILN_OIDC_CLIENT_SECRET` | 空 | 用户信息同步的客户端密钥。只从环境读取 |
| `KILN_OIDC_REDIRECT_URI` | 空 | 授权码回调地址，须是 http 或 https。配置时必填 |
| `KILN_OIDC_EMPLOYMENT_CLAIM` | 空 | 在职状态声明的名称。配置时必填 |
| `KILN_OIDC_EMPLOYMENT_ACTIVE_VALUES` | 空 | 视为在职的声明值，逗号分隔。配置时必填 |
| `KILN_OIDC_SCOPES` | 空 | 仅在已配置时生效。留空则使用 `openid profile email` |
| `KILN_SESSION_TTL_SECONDS` | `43200` | 用户会话有效期，默认 12 小时 |
| `KILN_SESSION_COOKIE_SECURE` | `false` | 为 true 时会话 cookie 带 Secure。本地 http 保持 false |

`LLM_PROVIDER=openai` 时，聊天请求会 `POST {LLM_BASE_URL}/chat/completions`。模拟模式和关闭模式不会访问网络。

`GIT_PROVIDER=gitea` 时，列表和解析引用走 `{GIT_BASE_URL}/api/v1/`。`GIT_PROVIDER=gitlab` 时走 `{GIT_BASE_URL}/api/v4/`。两种适配器实现同一组 kiln 接口，按配置替换，不把 Gitea 写成生产代码源。

## API 大纲

前缀 `/api/v1`。账号、RBAC 目录、制品、版本、下载预约、skill 和用户会话保存在 PostgreSQL，API 进程重启后仍然在。登录、退出、用户信息同步的开始与回调，以及「是否已配置」探测不需要事先持有令牌。其余路由接受会话 cookie、登录返回的 Bearer 令牌，或机器令牌 `KILN_API_TOKEN`。没有这些凭证时返回 401，不会改库。`/health` 和 `/docs` 不需要令牌；说明页本身不能绕过这道门去写入。已登录用户的写入会按现有权限目录校验。机器令牌拥有目录中的全部权限。

- 登录：`POST /auth/login` 校验本地口令，写入会话哈希，设置 httpOnly cookie，并返回 Bearer 令牌。`POST /auth/logout` 撤销会话。口令只以哈希保存，响应里不出现口令
- 用户信息同步：`GET /auth/sync` 说明是否已配置。未配置时 `GET /auth/oidc/start` 与 `GET /auth/oidc/callback` 返回「用户信息同步未配置」。配置后使用授权码和 PKCE，按发行方与 subject 找到或创建 kiln 账号。新账号的默认角色是 `reader`，不设置口令，也不复制公司侧的组或角色。在职状态未通过或账号已停用时不建立会话
- 本地账号：`GET/POST /accounts`，`GET /accounts/{id}`。创建账号可以带口令，调用方需要 `account.manage`
- RBAC 目录：`GET /rbac/roles`，`GET /rbac/permissions`
- 制品：`GET/POST /artifacts`，`POST /artifacts/{id}/versions` 登记尚无字节的版本
- 保存字节：`PUT /artifacts/{id}/versions/{version}/content`，请求体是原始字节，并用 `X-Kiln-Checksum-Algorithm`、`X-Kiln-Checksum` 声明校验和。成功后该版本的 `content_available` 为 true
- 下载预约：`POST /artifacts/{id}/versions/{version}/download-reservations`
- 取包：`GET /retrieve/{reservation_id}` 返回预约信息。字节已保存时 `content_available` 为 true，`GET /retrieve/{reservation_id}/content` 返回这些字节
- Skill：`POST /skills` 创建草稿，`POST /skills/{id}/submit-review`，`POST /skills/{id}/publish`
- 模型：`GET /llm/provider`，`POST /llm/chat`
- 执行器：`GET /executor`，`POST /jobs`，`GET /jobs`，`GET /jobs/{id}`，`GET /jobs/{id}/log`。任务记录在 PostgreSQL。状态和日志来自 Jenkins。响应里 `deploys_to_runtime` 为 false。兼容任务只打印调用方给出的标记
- 产品构建：`POST /builds`，`GET /builds`，`GET /builds/{id}`，`GET /builds/{id}/log`。请求体给出 `repository`、`ref`、`artifact_name`、`version`、`package_type`。kiln 用当前代码源适配器解析提交，Jenkins 克隆该提交并运行仓库根目录的 `kiln-build.sh`。脚本必须写出 `kiln-dist/artifact.bin`。构建成功后，这些字节写入 `KILN_ARTIFACT_ROOT`，对应版本的 `content_available` 为 true，`source` 为 `build`。取包接口返回同一组字节。响应里 `deploys_to_runtime` 为 false
- 代码源：`GET /git/source`，`GET /git/repositories`，`GET /git/commits?repository=owner/name&ref=main`

Skill 步骤不会被执行。kiln 进程本身不运行 shell。产品构建的 shell 跑在 Jenkins 上。

## 测试

后端测试连接真实 PostgreSQL，不使用内存库、SQLite 或假的持久层。制品字节测试把文件写进临时目录，不用模拟存储。默认连接串是 `postgresql://kiln:kiln@127.0.0.1:5432/kiln`，可用环境变量 `KILN_TEST_DATABASE_URL` 覆盖。GitHub Actions 的 backend 作业会启动 `postgres:16`。`backend/tests/test_persistence.py` 里的 `test_records_survive_api_process_restart` 通过 HTTP 写入记录，结束 API 进程，确认库里仍有这些行，再启动一个新进程读回同一条记录。`test_artifact_bytes_survive_api_process_restart` 把字节写进临时目录，结束 API 进程，再由新进程从同一目录取回相同字节。`backend/tests/test_auth.py` 确认未带令牌的客户端得到 401，不能写入账号或版本，也不能保存或下载制品字节。`backend/tests/test_login.py` 覆盖本地口令、会话在进程重启后仍然有效，以及用户信息同步。同步测试使用进程内的授权码测试双端（authorize、token、JWKS、userinfo 和在职声明）。这些测试每次都会跑，不读取 `KILN_SKIP_EXTERNAL_TESTS`。`backend/tests/test_boundaries.py` 确认 compose 发布端口都带 `127.0.0.1`。

执行器和代码源测试同样不使用假的 Jenkins 或 Gitea。它们读取：

| 变量 | 含义 |
| --- | --- |
| `KILN_TEST_JENKINS_URL` | 真实 Jenkins 根地址 |
| `KILN_TEST_JENKINS_USER` | Jenkins 用户 |
| `KILN_TEST_JENKINS_API_TOKEN` | Jenkins API 令牌或口令 |
| `KILN_TEST_GIT_BASE_URL` | 真实 Gitea 根地址 |
| `KILN_TEST_GIT_TOKEN` | Gitea 访问令牌，需要能创建并读取仓库 |
| `KILN_TEST_GIT_PROVIDER` | 省略时按 `gitea`。实验室测试要求这个值是 `gitea` |
| `KILN_SKIP_EXTERNAL_TESTS` | 设为 `1` 才跳过。未设置且上面的地址缺失时，测试失败 |

CI 在作业里启动一次性容器，名字是 `kiln-ci-jenkins`、`kiln-ci-gitea`，卷是 `kiln-ci-jenkins-data`、`kiln-ci-gitea-data`，网络是 `kiln-ci-net`，宿主机端口是 `127.0.0.1:28080` 和 `127.0.0.1:23000`。这些和共享实验机上的 `kiln-jenkins`、`kiln-gitea`、`kiln-net` 以及 `5432`、`3000`、`2222`、`8080`、`50000` 都不相同。口令在作业里生成，不写入仓库。镜像与共享实验机一致：`jenkins/jenkins:2.580.2-lts-alpine`、`gitea/gitea:1.24.7`。产品构建测试让 Jenkins 通过 `http://kiln-ci-gitea:3000` 克隆，这个地址只在 `kiln-ci-net` 内有效，不另开宿主机端口。`backend/tests/test_product_build.py` 用真实仓库、真实 Jenkins 和平台本地目录核对字节；设 `KILN_SKIP_EXTERNAL_TESTS=1` 才会跳过。

要让同一批测试指向已经在跑的实验室，把上表的变量设成那台机器上的地址和令牌，不要设置 `KILN_SKIP_EXTERNAL_TESTS`。令牌只放在环境里。

```bash
source .venv/bin/activate
python -m ruff check backend
python -m pytest backend
cd console && npm run build
```

GitHub Actions 会跑后端测试、控制台构建，以及 `docker compose config`。

本地登录，以及用户信息同步的起步已经接上：控制台有登录页，口令只保存哈希，会话写在 Postgres。未配置公司用户系统时使用本地口令。配置之后，公司系统只同步身份和在职状态，kiln 的授权仍留在平台上。`KILN_API_TOKEN` 仍是必填的机器和开发访问令牌。

## 这一版还没有，但仍属于一期

骨架如何在一台机器上启动，见 [部署说明](docs/DEPLOY.md)。制品目录、版本、权限目录和取包接口由平台持有。版本字节保存在平台本地目录里，取包会返回这些字节。下面这些按架构方案仍是一期要完成的事，但不是这一版：

- Nexus 集成

没有令牌或会话时不能改领域数据，也不能读取这些接口，也不能保存或下载制品字节。平台自身具备完整的账号与权限能力。

这一版有产品构建：从已配置的代码源检出提交，在 Jenkins 上运行仓库里的 `kiln-build.sh`，把 `kiln-dist/artifact.bin` 存进平台目录，取包返回这些字节，`content_available` 为 true。响应里 `deploys_to_runtime` 为 false。兼容任务仍然只打印标记。代码源适配器可以替换。实验室适配器是 Gitea，生产代码源是 GitLab。生产 GitLab 的地址不在仓库里。

## 不属于一期

- Harbor
- Kubernetes 镜像构建
- 专用镜像仓库
- 把制品发布到运行环境。平台仍然不把制品部署到运行环境。响应里 `deploys_to_runtime` 为 false
- 控制台视觉重做

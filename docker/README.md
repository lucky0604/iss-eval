# Docker 部署

本目录包含 EvalScope 服务（Web Dashboard + 精度评测 + 性能压测）的单容器部署配置。
镜像内置两个 Python 环境：框架环境（`eval` / `perf` / `service`）和隔离的 AIPerf 0.13.0 运行时。

## 目录内容

| 文件 | 说明 |
| --- | --- |
| `Dockerfile` | 多阶段构建：Node 编译 Dashboard → Python 运行时 + 独立 AIPerf 环境 |
| `docker-compose.yml` | 端口、数据卷、ulimit、日志轮转、可选的 GPU 预留 |
| `entrypoint.sh` | 启动前创建输出目录、探测 AIPerf 版本，然后 `exec` 服务进程 |
| `.env.example` | 配置模板，复制为 `.env` 后按需修改 |
| `verify.sh` | 部署后自检：容器状态、版本、外部 runner 前提、AIPerf 引擎可用性 |

构建上下文是**仓库根目录**（`../.dockerignore` 负责裁剪上下文），因此修改代码后重新构建即可。

## 镜像设计要点

1. **前端在镜像内构建**：`node:22-bookworm-slim` 执行 `npm ci && npm run build`，产物复制到
   `evalscope/web/dist`，再随包安装进 site-packages，由 Flask 直接提供 SPA；宿主机不需要 Node。
2. **AIPerf 使用独立环境**：锁文件 `requirements/aiperf-linux-x86_64-py312.lock` 安装在
   `/opt/aiperf-venv`（`--only-binary=:all:`）。AIPerf 的依赖 pin 与框架环境冲突，因此不共用环境；
   服务通过绝对路径 `EVALSCOPE_AIPERF_EXECUTABLE` 探测外部可执行文件，要求版本为 `0.13.0`。
3. **运行期系统依赖**：`procps` 提供 `/bin/ps`（外部 runner 必需）、`tini` 作为 PID 1 负责回收子进程并转发信号。
4. **非 root 运行**：容器用户 `evalscope`（默认 uid/gid `1000`），只对 `/data` 有写权限。
5. **持久化**：`/data/outputs` 存放报告、AIPerf 原始产物与请求 SQLite 索引；`/data/cache` 存放
   ModelScope / HuggingFace 数据集与 tokenizer 缓存。

## 前置条件

- Linux **x86_64**：仓库只提供 `linux/x86_64 + CPython 3.12` 的 AIPerf 锁文件（见下文“自定义平台”）。
- Docker Engine 24+、Docker Compose v2。
- 构建期可访问 PyPI 与 npm（或通过 `.env` 配置镜像源）。
- 运行期可访问被测模型服务，以及 ModelScope / HuggingFace（用于下载数据集与 tokenizer）。
- 资源建议：≥ 4 vCPU / 8 GB 内存；磁盘按压测规模预留，AIPerf 原始 JSONL 与 SQLite 索引会持续增长。

## 快速开始

```bash
cd docker
cp .env.example .env        # 按需修改端口、时区、镜像源、数据目录
docker compose build
docker compose up -d
docker compose ps
curl -fsS http://127.0.0.1:9000/health
sh verify.sh
```

打开 `http://<server>:9000/dashboard`。首次构建需下载前端依赖、Python 依赖和 AIPerf 锁文件，耗时从数分钟到十几分钟不等。

### 在没有 Docker 的机器上准备

本目录的配置可以在没有 Docker 的机器上编辑和审查；镜像必须在有 Docker 的环境（目标服务器、CI 或另一台机器）构建：

```bash
# 方式一：在目标服务器上直接构建
cd docker && docker compose build && docker compose up -d

# 方式二：在别处构建后转运，目标服务器无需访问外网
docker build -f docker/Dockerfile -t evalscope:latest .
docker save evalscope:latest | gzip > evalscope-latest.tar.gz
# 复制到服务器后：
gunzip -c evalscope-latest.tar.gz | docker load
```

转运镜像时，目标服务器仍需保留仓库中的 `docker/` 目录用于 `docker compose` 编排（镜像已包含代码，不需要重新构建）。

不使用 Compose 时：

```bash
docker build -f docker/Dockerfile -t evalscope:latest .
docker run -d --name evalscope -p 9000:9000 \
  -e TZ=Asia/Shanghai \
  -v evalscope_outputs:/data/outputs \
  -v evalscope_cache:/data/cache \
  evalscope:latest
```

## 验收 AIPerf 后端

```bash
sh verify.sh        # 容器状态 + 版本 + /bin/ps + 引擎可用性，AIPerf 不可用时退出码非 0
```

手动逐项检查：

```bash
docker compose exec evalscope evalscope --version
docker compose exec evalscope /opt/aiperf-venv/bin/aiperf --version   # 必须包含 0.13.0
docker compose exec evalscope python -c "from evalscope.perf.executors.external import supports_external_runner; print(supports_external_runner())"
curl -fsS http://127.0.0.1:9000/api/v1/perf/engines
```

`/api/v1/perf/engines` 返回的 `aiperf.available` 应为 `true`。服务在查询引擎可用性时会执行一次版本探测
（结果按分钟缓存）：路径不是绝对路径、文件不可执行、版本不是 `0.13.0`、或缺少 `/bin/ps` 时，引擎会被标记为
不可用，原生 EvalScope 引擎不受影响。

## 配置项

`.env` 由 Compose 自动加载：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `EVALSCOPE_IMAGE` | `evalscope:latest` | 镜像标签 |
| `EVALSCOPE_CONTAINER_NAME` | `evalscope` | 容器名 |
| `EVALSCOPE_BIND_ADDRESS` | `0.0.0.0` | 宿主机监听地址；仅本机访问改为 `127.0.0.1` |
| `EVALSCOPE_PORT` | `9000` | 宿主机端口（容器内固定 9000） |
| `TZ` | `Asia/Shanghai` | 容器时区，影响日志与 AIPerf 实时日志时间戳 |
| `PIP_INDEX_URL` / `PIP_TRUSTED_HOST` | 官方 PyPI | 构建期 pip 源（内网 HTTP 源需同时设置 trusted host） |
| `NPM_REGISTRY` | 官方 npm | 构建期 npm 源 |
| `PYTHON_VERSION` / `NODE_VERSION` | `3.12` / `22` | 构建基础镜像版本 |
| `AIPERF_LOCK_FILE` | `requirements/aiperf-linux-x86_64-py312.lock` | AIPerf 锁文件（相对仓库根目录） |
| `EVALSCOPE_OUTPUTS_MOUNT` | 空（命名卷） | 自定义产物目录（绑定挂载路径） |
| `EVALSCOPE_CACHE_MOUNT` | 空（命名卷） | 自定义缓存目录（绑定挂载路径） |
| `EVALSCOPE_OUTPUTS_VOLUME` / `EVALSCOPE_CACHE_VOLUME` | `evalscope_outputs` / `evalscope_cache` | 命名卷名称 |
| `EVALSCOPE_UID` / `EVALSCOPE_GID` | `1000` | 容器运行用户，需与绑定目录所有者一致 |
| `EVALSCOPE_AIPERF_EXECUTABLE` | `/opt/aiperf-venv/bin/aiperf` | 容器内 AIPerf 绝对路径 |

## 数据持久化

默认使用命名卷，开箱即用。若要把数据放在宿主机（便于备份、直接查看报告）：

```bash
mkdir -p /srv/evalscope/outputs /srv/evalscope/cache
sudo chown -R 1000:1000 /srv/evalscope
```

在 `.env` 中设置：

```dotenv
EVALSCOPE_OUTPUTS_MOUNT=/srv/evalscope/outputs
EVALSCOPE_CACHE_MOUNT=/srv/evalscope/cache
```

注意：绑定目录必须先创建并授权，否则 Docker 会以 root 创建目录，容器内用户无法写入。

备份命名卷：

```bash
docker run --rm -v evalscope_outputs:/data -v "$PWD":/backup alpine \
  tar czf /backup/evalscope-outputs-$(date +%F).tgz -C /data .
```

## 日常运维

```bash
docker compose logs -f evalscope          # 跟踪日志
docker compose restart evalscope          # 重启服务
docker compose exec evalscope bash        # 进入容器排查
docker compose down                       # 停止（保留数据卷）
```

升级版本：

```bash
git pull
docker compose build --pull
docker compose up -d
```

运行中任务的注意事项：

- 服务是**单进程**应用，任务注册表和运行状态保存在内存中，重启后不会自动恢复运行中的任务，
  也不会自动续跑实验；请先在 Dashboard 中停止任务再重启。
- `stop_grace_period: 60s` 为清理留出时间；AIPerf 子进程组由外部 runner 的 watchdog 在父进程退出后回收。
- 不要使用 `--scale` 起多副本：任务状态与输出目录写入都是单实例语义。

## 安全建议

- 服务**没有内置鉴权**，任何能访问端口的人都可以提交任务、读取报告和删除产物。
- 建议保持 `EVALSCOPE_BIND_ADDRESS=127.0.0.1`，通过 Nginx / Caddy 反向代理提供 TLS、Basic Auth 或 IP 白名单。
  若直接暴露到内网，至少配置防火墙规则。
- `/data/outputs` 包含 prompt、模型回复、请求元数据与评测报告，以及 `configs/task_config.yaml` 等配置快照
  （`api_key` 字段为 `SecretStr`，序列化时会被掩码，但仍建议整体按敏感数据处理）；卷、备份和日志都应按敏感数据管理。
- 容器默认以非 root 运行，不要添加 `privileged: true`。如需在容器内运行沙箱类 Agent 评测，
  请单独评估挂载 Docker socket 的风险，不要复用本部署。

## 已知限制

- 镜像**不包含** PyTorch / CUDA，面向 OpenAI 兼容 API 的精度评测与压测；本地 checkpoint（`llm_ckpt`）
  与本地多模态模型需要另建 GPU 镜像（可在本镜像基础上安装 torch）。
- AIPerf 后端仅支持流式 OpenAI Chat 兼容接口（单次运行 / 前缀缓存实验），语义与限制见
  `docs/*/user_guides/stress_test/aiperf_cache.md`。
- 外部 runner 只向 AIPerf 子进程透传 `PATH`、`HOME`、`LANG`、`LC_ALL`、`TMPDIR`、`SSL_CERT_FILE`、
  `SSL_CERT_DIR`，**不包含** `HTTP_PROXY` / `HTTPS_PROXY`。如果被测模型服务只能经代理访问，需要调整网络或 runner。
- 数据集与 tokenizer 默认从 ModelScope 下载并缓存到 `/data/cache`；离线环境请预先挂载缓存目录。

## 自定义平台或 AIPerf 版本

- **arm64 等平台**：按 `docs/en/user_guides/stress_test/aiperf_cache.md` 的方法生成对应平台的锁文件，然后：
  `AIPERF_LOCK_FILE=requirements/<your-lock>.lock docker compose build`
- **升级 AIPerf**：服务端 `ExternalRuntime.expected_version` 固定为 `0.13.0`，升级前需同步修改并回归
  输出 schema、进程清理与取消逻辑，否则引擎会显示为不可用。

## 常见问题

1. **AIPerf 显示不可用**：用上面的命令确认版本为 `0.13.0`、路径为绝对路径且有可执行权限；确认容器内存在 `/bin/ps`（本镜像已安装 `procps`）。
2. **写入产物时报权限错误**：绑定目录所有者不是 `EVALSCOPE_UID`；执行 `chown -R 1000:1000 <dir>`，或在 `.env` 中调整 uid/gid 后重新构建。
3. **数据集下载失败**：检查容器网络/DNS；离线部署可把数据集放到 `/data/cache/modelscope` 后挂载。
4. **构建时依赖下载慢**：在 `.env` 中配置 `PIP_INDEX_URL` / `NPM_REGISTRY` 后重新 `docker compose build`。
5. **健康检查失败**：`docker compose logs evalscope` 查看启动日志；确认 9000 端口未被占用。

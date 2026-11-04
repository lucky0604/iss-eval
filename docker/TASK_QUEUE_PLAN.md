# EvalScope 任务队列改造方案

> 目标：把「提交评测/压测任务后必须停留在当前页面」变成「提交即返回、后台可靠执行、随时回来看结果」，
> 同时消除部署后频繁出现的 timeout、任务丢失、重启即中断等问题。
>
> 结论先行：**推荐 Redis 7 + RQ 3.x + 独立 worker 容器**，Web 层只负责提交/查询，任务在 worker 中执行；
> RQ 的 job timeout 默认不限制、worker 有心跳、失联任务可重排队，正好覆盖本项目的长任务场景。

---

## 1. 现状评审

### 1.1 当前运行链路

```
Browser ──POST /api/v1/eval/invoke──► Flask (app.run, 单进程多线程)
                                        │
                                        ├─ _build_task_config()
                                        ├─ build_task_info() → outputs/<id>/task_info.json
                                        ├─ multiprocessing.Process(spawn) ──► run_task() / run_perf_benchmark()
                                        │                                      │
                                        │                                      ├─ progress.json
                                        │                                      ├─ logs/eval_log.log
                                        │                                      └─ reports/
                                        └─ 阻塞等待子进程结束，HTTP 响应才返回
```

关键代码路径：

| 关注点 | 位置 | 现状 |
| --- | --- | --- |
| 服务启动 | `evalscope/service/app.py:137` | `app.run()`，Flask 自带开发服务器 |
| 评测提交 | `evalscope/service/blueprints/eval.py:245` | `/invoke` 同步阻塞，任务跑完才返回 |
| 压测提交 | `evalscope/service/blueprints/perf.py:1041` | 同样同步阻塞 |
| 任务执行 | `evalscope/service/utils/process.py:200` | `multiprocessing.Process` + 轮询结果队列 |
| 运行中任务 | `evalscope/service/utils/process.py:44` | `_active_processes` **内存字典** |
| 任务元数据 | `evalscope/service/utils/log.py:121` | `outputs/<task_id>/task_info.json` |
| 前端提交 | `evalscope/web/src/api/task.ts:10` | `fetch` 一直挂到任务结束 |
| 前端监控 | `evalscope/web/src/components/tasks/TaskRunMonitor.tsx` | 已有 5s 轮询 status/log 的能力 |
| 任务列表 | `evalscope/web/src/pages/TasksPage.tsx` | 已有 `/tasks` 页面和 ActiveTasksPanel |

### 1.2 部署方案评审（`docker/`）

做得好的部分：

- 多阶段构建，前端产物打进镜像，宿主机不需要 Node。
- 框架与 AIPerf 使用两个 Python 环境，避免依赖冲突。
- 非 root 运行、`tini` 做 PID 1、`stop_grace_period: 60s`、日志轮转、健康检查。
- 输出与缓存使用命名卷，备份/迁移说明清楚。

必须正视的问题：

1. **HTTP 请求寿命 = 任务寿命**。评测可能跑几十分钟到几小时，浏览器、Nginx、负载均衡、公司网关的
   idle timeout 都会先到；页面刷新/关闭后前端丢失结果，但服务端线程仍被占住。
2. **没有队列，也没有容量控制**。每个 `/invoke` 直接起一个子进程；多人同时提交会无上限并发，
   评测任务之间争抢网络/CPU，AIPerf 压测结果也会被干扰。
3. **运行状态只在内存**。`_active_processes` 在进程内；Web 容器一重启，所有运行中任务从 Dashboard 消失，
   `task_info.json` 里的状态还停留在 `running`，无法自动恢复。
4. **`/stop` 只能在同一个 Web 进程内生效**。一旦执行搬到独立 worker，取消必须走新的通道。
5. **Flask 开发服务器上生产**。`app.run()` 没有生产级超时/并发/稳定性保证；如果把请求改成短请求问题不大，
   但它不适合作为唯一 Web 服务器。
6. **健康检查只检查 HTTP 进程**。没有检查 Redis/队列/worker；worker 挂了，Dashboard 仍然“健康”。
7. **没有任务级重试/恢复策略**。子进程 OOM、worker 被杀、宿主机重启后，任务只能靠人工重新提交。
8. **默认无鉴权 + `0.0.0.0` 暴露**。加入队列后，任何能访问端口的人都可以灌任务；需要队列上限、反向代理鉴权。
9. **没有资源上限**。评测/压测可能吃满内存导致 OOM；需要在 compose 中给出可选的 `mem_limit`/CPU 建议。

### 1.3 为什么现在“动不动 timeout”

| 超时来源 | 当前是否命中 | 说明 |
| --- | --- | --- |
| 浏览器/代理等待长请求 | 是 | `/invoke` 一直不返回，Nginx 默认 `proxy_read_timeout 60s` 直接 504 |
| Flask 开发服务器 | 潜在 | 不是生产 WSGI，线程被长任务占满后其他请求排队 |
| 模型 API 单请求超时 | 是 | `generation_config.timeout` 等；应作为**单请求**超时，而不是任务总超时 |
| 容器重启/发布 | 是 | 运行中任务直接丢，前端只能看到“连接失败” |
| 未来的队列组件 | 取决于选型 | Celery+Redis 默认 1h visibility timeout、Dramatiq 默认 10min time limit 都是典型坑 |
| AIPerf 单请求超时 | 是 | 属于压测参数，应保持可配置，不应被队列层再套一层短超时 |

**核心判断：超时不是靠“把超时调大”解决的，而是要把长任务从 HTTP 请求生命周期里彻底拿出去。**

---

## 2. 组件选型

### 2.1 候选对比

| 方案 | 长任务支持 | worker 崩溃恢复 | 取消运行中任务 | 超时默认值/坑 | 额外组件 | 适用性 |
| --- | --- | --- | --- | --- | --- | --- |
| **RQ 3 + Redis** | 好，`Job.timeout` 默认 `None` | worker 心跳；`StartedJobRegistry.cleanup` 对配置了重试的任务重排队，否则进 `FailedJobRegistry`（`AbandonedJobError`） | `send_stop_job_command` 停止运行中任务；`job.cancel()` 取消排队任务 | 默认无 job 超时；需注意 `worker_ttl`（默认 420s）和心跳 | Redis | **推荐** |
| Celery + Redis | 好，但需正确配置 | `acks_late` + `task_reject_on_worker_lost`；Redis 默认 visibility timeout 3600s，长任务可能被重复投递 | 支持 revoke/terminate，但 terminate 较粗暴 | **默认 1h visibility timeout**；调大又延迟崩溃恢复 | Redis | 过重，坑多 |
| Celery + RabbitMQ | 好 | RabbitMQ ack/redelivery 语义更稳 | 支持 | 需理解 ack/late/retry | RabbitMQ | 已有 RabbitMQ 时可考虑 |
| Dramatiq + Redis | 好 | Redis broker 有 `heartbeat_timeout`（默认 60s） | 支持 | **TimeLimit 默认 600000ms（10 分钟）**，必须显式调大/关闭；默认 `max_retries=20` | Redis | 可用，但默认值不贴合长任务 |
| Huey（Redis/SQLite） | 一般 | 文档明确 **at-most-once**，consumer 被杀时执行中的任务不会自动重试 | 支持 | 需要自己处理中断任务补偿 | 可选 Redis 或 SQLite | 不满足“稳定”要求 |
| Temporal | 最强 | 事件溯源、Activity heartbeat、retry policy | 原生支持 | 没有“超时坑”，但需要正确设置 start-to-close/heartbeat | Temporal Server + 数据库 | 能力强但运维重，单机部署过度设计 |
| 自建 SQLite 队列 | 好（单机） | 需要自己实现租约、心跳、重排队 | 需要自己实现 | 自己控制 | 无 | 可作为“不能加 Redis”的兜底，不推荐首选 |

### 2.2 为什么是 RQ + Redis

1. **模型简单**：一个 Redis 实例、一个 worker 进程即可；项目已经是单机 Compose 部署，加一个 Redis 容器成本最低。
2. **长任务默认安全**：RQ 3 的 `Job.timeout` 默认 `None`，不会像 Dramatiq 那样默认 10 分钟杀任务，也不会像
   Celery+Redis 那样有 1 小时 visibility timeout 的重复投递陷阱。
3. **失联恢复可用**：worker 有独立心跳线程，默认 `worker_ttl=420s`；维护任务默认每 600s 检查一次
   `StartedJobRegistry`，对还有重试次数的任务自动重排队，没有重试次数的进入 `FailedJobRegistry` 并标记
   `AbandonedJobError`。
4. **取消能力完整**：排队任务 `job.cancel()`；运行中任务 `send_stop_job_command()` 或我们自己的取消键。
5. **与现有执行模型契合**：worker 里仍然复用 `run_in_subprocess()`（`multiprocessing.spawn` + 现有
   `stop_process()`），AIPerf 外部 runner、进度文件、日志文件、`task_info.json` 都不需要重写。
6. **生态与可运维性**：`rq info`、`rq worker`、RQ 的 registry 都能直接排障；需要时也能接 `rq-dashboard`
   （可选，不作为必需依赖）。

> 版本建议：`rq==3.0.0`、`redis>=5.0.1`（RQ 3 要求 Python ≥ 3.10、Redis ≥ 5，官方也支持 Valkey 7.2+）。
> 生产镜像固定精确版本，升级前跑长任务回归。

### 2.3 什么情况下不要选 RQ

- 如果未来要做**多机、跨可用区、强一致**的任务编排，Temporal 更合适；届时可以把“执行器”换掉而不动 API。
- 如果运维团队明确不允许新增 Redis，可用 SQLite 队列 + 单 worker 兜底，但需要自己实现租约/心跳/重排队，
  工作量并不会更小，稳定性责任也转移到我们身上。
- 如果已经有 RabbitMQ 且团队熟悉 Celery，Celery + RabbitMQ 也可行；但不建议 Celery + Redis 跑多小时任务。

---

## 3. 目标架构

### 3.1 拓扑

```
                       ┌───────────────────────────────┐
 Browser ──HTTP──────► │  evalscope (Flask + waitress)  │
   │                   │  - 参数校验 / 入队 / 查询      │
   │                   │  - 静态 Dashboard              │
   │                   └───────────────┬───────────────┘
   │                                   │ enqueue / status / cancel
   │                   ┌───────────────▼───────────────┐
   └──poll 5s─────────►│           Redis 7             │
                       │  - RQ queue / job / registry  │
                       │  - task hash / cancel key     │
                       │  - worker heartbeat           │
                       └───────────────┬───────────────┘
                                       │ dequeue
                       ┌───────────────▼───────────────┐
                       │  evalscope-worker (RQ)        │
                       │  - concurrency 默认 1         │
                       │  - run_in_subprocess()        │
                       │  - 进度/日志/报告写共享卷     │
                       └───────────────┬───────────────┘
                                       │
                       ┌───────────────▼───────────────┐
                       │ /data/outputs  /data/cache    │  ← Web 与 worker 共享
                       └───────────────────────────────┘
```

### 3.2 职责边界

| 组件 | 职责 | 不做什么 |
| --- | --- | --- |
| Web (`evalscope`) | 校验、生成/校验 task_id、写任务记录、入队、查询状态/日志/报告、取消/重试 | 不执行评测、不持有任务子进程 |
| Redis | 队列、job 状态、任务索引、取消信号、worker 心跳 | 不存大结果（只存元数据/摘要） |
| Worker (`evalscope worker`) | 出队、执行、进度/日志、状态回写、取消响应、优雅停机 | 不提供 HTTP |
| 共享卷 `/data` | 输出、缓存、报告、日志、`task_info.json`、`result.json` | 不做跨机共享（单机部署前提） |

---

## 4. 关键设计

### 4.1 任务状态机

```
                 ┌─────────┐
   submit ──────►│ queued  │
                 └────┬────┘
        cancel        │ worker 出队
   ┌──────────────────┼──────────────┐
   ▼                  ▼              │
┌───────────┐    ┌─────────┐         │
│ cancelled │    │ running │◄────────┘
└───────────┘    └────┬────┘
                      │
        ┌─────────────┼──────────────┬─────────────┐
        ▼             ▼              ▼             ▼
   ┌─────────┐   ┌─────────┐   ┌──────────┐  ┌────────┐
   │completed│   │ stopped │   │  error   │  │timeout │
   └─────────┘   └─────────┘   └────┬─────┘  └────────┘
                                    │ 手动/崩溃重试
                                    ▼
                                 ┌─────────┐
                                 │ queued  │
                                 └─────────┘
```

对前端兼容的映射建议：

- 新增显式 `queued`、`cancelled`、`timeout` 状态；现有 `preparing/running/cancelling/finalizing/completed/stopped/error/unknown`
  继续保留。
- `timeout` 只在**用户显式配置了任务截止时间**时出现；队列本身不设置短超时。
- `worker_lost` 作为 `error` 的 `error_code`，不是独立状态，避免状态爆炸。

### 4.2 API 设计

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| POST | `/api/v1/eval/invoke` | 校验通过后入队，**立即返回 202** `{task_id,status:"queued",queue_position}` |
| POST | `/api/v1/perf/invoke` | 同上，压测任务 |
| POST | `/api/v1/eval/resume/invoke` | 入队一个“恢复评测”任务（`use_cache` 指向原 work_dir） |
| GET | `/api/v1/tasks` | 任务列表：`status` / `kind` / `limit` / `cursor`，合并 Redis 记录与磁盘历史 |
| GET | `/api/v1/tasks/{id}` | 单任务详情（沿用现有 `ActiveTaskInfo`，增加队列/重试字段） |
| GET | `/api/v1/tasks/{id}/result` | 终态后返回结果（等价于原来 `/invoke` 的最终响应） |
| GET | `/api/v1/eval/log` / `/api/v1/perf/log` | 沿用现有分页日志接口 |
| GET | `/api/v1/eval/progress` / `/api/v1/perf/progress` | 沿用现有进度接口 |
| POST | `/api/v1/tasks/{id}/cancel` | 排队：`job.cancel()`；运行中：写取消键，worker 响应后 `stopped` |
| POST | `/api/v1/tasks/{id}/retry` | 评测：同 task_id + `use_cache` 重入队；压测：生成新 task_id 重跑 |
| GET | `/api/v1/queue/stats` | 队列深度、运行中、失败数、worker 心跳、Redis 状态 |
| GET | `/health` | Web 进程存活（liveness） |
| GET | `/ready` | Web + Redis + 队列可达（readiness） |

**兼容性**：现有 `/invoke` 是“阻塞到任务结束”的语义，改成 202 是 breaking change。
**Phase 1 的实际选择是反向兼容**：默认仍是阻塞语义，前端统一携带 `?async=1` 走 202 + 轮询，
现有脚本与测试无需改动（详见第 7 节）。Phase 2 若要把异步设为默认，再按下面的方式迁移：

- 前端（随镜像发布）同步改造，默认走异步；
- 对外脚本改用 `POST /invoke` → 轮询 `GET /tasks/{id}/result`；
- 如果确实需要旧行为，提供 `?wait=true` 的兼容参数，但**只建议在本地/调试使用**，并在文档中标注
  “长任务勿用，可能被代理超时”。
- `EvalScope-Task-Id` 头保留：客户端可传，服务端校验；未传则服务端生成。重复提交同一个 id 时返回已有任务，
  实现幂等提交。

### 4.3 数据模型

新增 Pydantic 模型（放在 `evalscope/service/api_models/` 或 `evalscope/service/queue/models.py`，二者共用）：

```python
class TaskSubmitResponse(ApiResponseModel):
    task_id: str
    kind: Literal['eval', 'perf']
    status: Literal['queued'] = 'queued'
    queue_position: Optional[int] = None
    enqueued_at: str


class TaskRecord(ApiResponseModel):
    task_id: str
    kind: Literal['eval', 'perf']
    engine: Optional[Literal['evalscope', 'aiperf']] = None
    status: Literal[
        'queued', 'running', 'cancelling', 'finalizing',
        'completed', 'stopped', 'cancelled', 'error', 'timeout',
    ]
    model: str = ''
    endpoint: Optional[str] = None
    alias: Optional[str] = None
    queue: str = 'eval'                 # 'eval' | 'perf'
    queue_position: Optional[int] = None
    attempt: int = 0
    max_attempts: int = 1
    worker: Optional[str] = None
    enqueued_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    cancel_requested_at: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    config: Optional[TaskConfigSummary] = None
    report_url: Optional[str] = None
    html_report_url: Optional[str] = None
    result_url: Optional[str] = None
```

Redis key 规划（前缀可配置 `EVALSCOPE_REDIS_PREFIX=evalscope`）：

| Key | 类型 | 用途 |
| --- | --- | --- |
| `evalscope:tasks:{task_id}` | hash | `TaskRecord` 字段 |
| `evalscope:tasks:index` | zset | 按 `enqueued_at` 排序的历史索引 |
| `evalscope:task:{task_id}:cancel` | string + TTL | 取消信号；worker 每秒检查 |
| `evalscope:task:{task_id}:lock` | string + TTL | 防止同一任务被两个 worker 同时执行 |
| `evalscope:worker:{hostname}:heartbeat` | string + TTL | worker 健康检查 |
| RQ 自身 key | - | queue / job / started / failed registry |

**不要**把完整评测结果放进 Redis：worker 只把摘要和产物路径写回，完整结果落盘到
`outputs/<task_id>/result.json`、`reports/`、`perf/`。这样 Redis 内存可控、备份简单。

**配置序列化陷阱（实现时必读）**：

`TaskConfig.api_key` 是 `SecretStr`，`TaskConfig.to_dict()` / `model_dump(mode='json')` 会输出
`**********` 掩码。如果把 `to_dict()` 的结果放进 job payload，worker 反序列化后会拿掩码当密钥，
表现为全部样本 401，而且很难排查。正确做法：

- Web 端用 `TaskConfig` 做一次校验，但**入队的是规范化后的请求 dict**（JSON 原生、保留真实 `api_key`），
  再附加 `work_dir`、`no_timestamp`、`enable_progress_tracker` 等服务字段；
- worker 端用 `parse_task_config(payload['config'])` 重新构造 `TaskConfig`；
- 落盘的 `configs/task_config.yaml` 继续使用 `to_dict()`（掩码），两者用途不同，不要混用；
- 如果担心 Redis 泄露密钥，见 4.10 的可选加密方案。

`outputs/<task_id>/task_info.json` 继续保留，作为磁盘侧的持久审计记录；Redis 丢了也能从磁盘扫描出历史。
两者字段对齐，写入时用现有 `write_task_manifest()` 的原子替换逻辑。

### 4.4 Worker 执行模型

新增 CLI：

```bash
evalscope worker \
  --redis-url redis://redis:6379/0 \
  --queues perf eval \
  --concurrency 1 \
  --worker-ttl 420 \
  --maintenance-interval 600 \
  --job-timeout 0          # 0 = 不设置 RQ job 超时
```

核心 job 函数（伪代码，复用现有执行逻辑）：

```python
def execute_eval_job(payload: dict) -> dict:
    task_id = payload['task_id']
    # payload['config'] 是“规范化后的请求 dict”，包含真实的 api_key；
    # 不要使用 TaskConfig.to_dict() 的结果入队——它会把 SecretStr 序列化成掩码。
    config = parse_task_config(payload['config'])
    config.work_dir = os.path.join(OUTPUT_DIR, task_id)
    task_meta = restore_task_meta(payload['task_meta'])

    mark_running(task_id, worker=current_worker_name())
    create_log_file(task_id, os.path.join('logs', 'eval_log.log'))
    try:
        result = run_in_subprocess(
            run_eval_wrapper,
            config,
            task_id=task_id,
            task_meta=task_meta,
            should_cancel=lambda: cancel_requested(task_id),
        )
        write_result_json(task_id, result)
        mark_completed(task_id, result_summary(result))
        return {'status': 'completed', 'result_path': ...}
    except TaskStoppedError:
        mark_stopped(task_id)
        return {'status': 'stopped'}
    except Exception as exc:
        mark_error(task_id, classify_error(exc), user_safe_message(exc))
        raise
```

关键点：

1. **继续复用 `run_in_subprocess()`**：AIPerf 外部 runner、取消、stderr 捕获、进度文件都保持现状；
   只在轮询循环里增加一个 `should_cancel()` 回调。
2. **默认 `concurrency=1`**：压测必须独占（不能和其他压测/评测抢资源，否则指标失真）；评测任务默认也串行，
   避免多人同时提交把容器打爆。需要并行评测时通过 `EVALSCOPE_WORKER_CONCURRENCY=2` 显式开启。
3. **队列顺序**：`--queues perf eval` 表示优先消费 `perf`。如果希望评测和压测互不阻塞，拆成两个 worker
   （见 5.2），但必须保证同一时刻只有一个 perf job。
4. **任务锁**：worker 出队后先 `SET evalscope:task:{id}:lock {worker} NX EX 900`，运行中每 60s 续期；
   拿不到锁说明有残留执行，直接失败并提示 `duplicate_execution`。这是防止“worker 失联重排队 + 原进程还活着”
   双跑的兜底。
5. **`job_timeout=None`**：不要让 RQ 在固定时间杀任务；如果业务上需要上限，用任务级 `deadline`（见 4.6）。
6. **结果摘要**：job 返回值只放 `status`、报告路径、指标摘要；不要把 `predictions` 或完整 `Report` 序列化进 Redis。

### 4.5 取消与优雅停机

**排队任务**：直接 `job.cancel()`，从队列移除，状态 `cancelled`。

**运行中任务**：

```
POST /tasks/{id}/cancel
   └─► SET evalscope:task:{id}:cancel 1 EX 86400
           └─► worker 的 run_in_subprocess 轮询 should_cancel()
                   └─► stop_process(task_id)   # 复用现有逻辑，终止子进程
                           └─► TaskStoppedError → 状态 stopped
```

为什么不用 RQ 的 `send_stop_job_command()` 作为主通道：

- 它依赖 worker 的 command 监听机制，而我们的 job 函数阻塞在 `run_in_subprocess()` 里，行为不够可控；
- 自己用一个 Redis 取消键，可以同时覆盖“排队中”“运行中”“worker 崩溃后重排队”三种情况，
  重排后的任务一启动就会看到取消键并立即停止，语义更完整。

**SIGTERM / `docker compose down`**：

- worker 默认是 warm shutdown：不再拿新任务，但会等当前任务结束；长任务可能超过 Docker 的
  `stop_grace_period`。
- 建议自定义信号处理：收到 SIGTERM 后写取消键，等待子进程清理（最多 60s），把任务标记为 `stopped`，
  然后退出；如果来不及，RQ 会把任务当作 abandoned，重试策略接管。
- `stop_grace_period` 建议 `120s`；容器停止时 Docker 会杀掉容器 PID namespace 内的所有进程，
  因此不会留下跨容器的孤儿进程。

**孤儿进程**：

- 原生评测子进程在 Linux 上用 `prctl(PR_SET_PDEATHSIG, SIGKILL)`，父 worker 被 SIGKILL 时子进程一起退出；
- AIPerf 外部 runner 已有 parent-liveness watchdog，保持现状；
- worker 容器重启会清空整个 PID namespace，这是最后一道保险。

### 4.6 超时策略（重点）

**原则：队列层不设置“任务总超时”；只在单个外部请求上设置超时，并由调用方显式开启任务截止时间。**

| 层 | 配置 | 建议值 | 说明 |
| --- | --- | --- | --- |
| 浏览器/HTTP | 提交接口 | 立即返回 | 不再让 fetch 挂几小时 |
| 反向代理 | `proxy_read_timeout` | 默认即可 | 所有请求都是短请求；只有将来加 SSE 才需要调大 |
| Web 服务器 | waitress `threads=8` | 无 per-request worker timeout | 替代 Flask 开发服务器，避免 gunicorn 默认 30s 杀 worker |
| RQ job | `job_timeout` | `None`（CLI `--job-timeout 0`） | 长任务不被队列框架杀 |
| RQ worker 心跳 | `worker_ttl` | `420s`（默认） | 心跳线程独立于 job 执行，长任务不会被误判失联 |
| 任务截止时间 | `EVALSCOPE_TASK_DEADLINE_SECONDS` | `0`（默认关闭） | 需要时由用户/管理员显式配置；超时走 `timeout` 状态 |
| 模型单请求 | `generation_config.timeout` | 按模型服务配置 | 失败按样本/请求重试，不升级为整个任务超时 |
| AIPerf 单请求 | 压测参数 `timeout` | 按被测服务配置 | 保持现有语义 |
| Redis 客户端 | `socket_connect_timeout` / `socket_timeout` | 5s / 30s + `retry_on_timeout` | 避免 Redis 抖动直接打崩请求 |
| Docker 健康检查 | interval 30s / timeout 5s / retries 3 | 现有默认 | Web 检查 liveness，worker 检查心跳 |
| Docker 停止 | `stop_grace_period` | `120s` | 给取消/清理留时间 |

**为什么不建议用 RQ/Celery 的 `job_timeout` 做业务超时**：

- RQ 到点会走 death penalty 杀执行进程，子进程/外部进程的清理不一定完整；
- 业务上真正需要的是“可配置的任务截止时间 + 优雅取消”，这应该由 worker 的监控循环实现，
  这样可以先 `stop_process()`、写清楚 `timeout` 原因，再结束 job。

**长任务验证**：上线前必须跑一个超过 `worker_ttl`（7 分钟）和超过 1 小时的任务，确认：

- 任务没有被 `StartedJobRegistry.cleanup()` 误判为 abandoned；
- 没有重复执行；
- 进度/日志持续更新；
- 取消能在合理时间内生效。

### 4.7 崩溃恢复矩阵

| 故障 | 结果 | 恢复机制 |
| --- | --- | --- |
| 浏览器关闭/刷新 | 任务继续 | 任务在 worker，Dashboard 从 `/tasks` 恢复 |
| Web 容器重启 | 任务继续 | Web 无状态；队列/记录在 Redis + 磁盘 |
| Worker 进程崩溃（OOM/SIGKILL） | 当前 job 标记 abandoned | RQ 维护任务重排队（有重试次数）或进 `FailedJobRegistry`；启动 reconciler 兜底 |
| Worker 容器重启 | 同上；容器内进程全部清理 | `restart: unless-stopped` + 任务锁 + 重排队 |
| Redis 重启 | 队列/记录从 AOF 恢复（最多丢 1s 写入） | AOF `everysec` + RDB；worker 自动重连 |
| 宿主机重启 | Compose 自动拉起 | Redis AOF；worker 重新出队；运行中任务重排队 |
| 任务本身异常 | 状态 `error`，日志保留 | 手动重试或修复配置后重提 |
| 用户取消 | `stopped` / `cancelled` | 取消键 + `stop_process()` |

**自动重试边界**：

- **评测**：worker 失联（`AbandonedJobError`）自动重排队 1 次，重排队时设置 `use_cache=work_dir`，
  复用已完成的样本，避免从头再跑；评测本身的业务异常（配置错误、模型 4xx）不自动重试。
- **压测**：不自动重试。压测对环境和时序敏感，失败应保留现场，由用户决定是否重跑；手动重试生成新 task_id，
  避免覆盖原产物。
- **手动重试**：评测沿用现有 resume 语义（同一 task_id + `use_cache`）；压测新开 task_id。

### 4.8 幂等与重复执行防护

1. **task_id 幂等**：提交时如果 `evalscope:tasks:{task_id}` 已存在且状态非终态，直接返回已有任务；
   终态则允许显式 `retry`，不隐式覆盖。
2. **任务锁**：`SET NX EX` 租约 + 定期续期；锁丢失时 worker 主动取消自己。
3. **缓存恢复**：评测重排队用 `use_cache`，已完成的样本不重复请求模型。
4. **输出目录隔离**：每个 task_id 独立目录；压测重跑使用新 task_id。
5. **提交幂等**：前端重试提交时携带同一个 `EvalScope-Task-Id`，服务端返回已有任务而不是再入队。

### 4.9 前端改造

现有前端已经具备大部分轮询能力，改动集中在“提交语义”和“任务列表”：

1. `createTaskApi().submit()` 返回值从 `EvalInvokeResponse` 改为 `TaskSubmitResponse`；收到 202 后立即
   进入监控状态（已有 `TaskRunMonitor` 会轮询 `/tasks/{id}` 和日志）。
2. `TaskRunnerShell` 不再等待最终结果才结束提交；`result` 只用于展示提交错误，最终结果从
   `GET /tasks/{id}/result` 获取（或任务详情里的 `result_url`）。
3. `ActiveTasksPanel` 改用 `GET /api/v1/tasks?status=queued,running`，显示排队位置、worker、尝试次数。
4. `/tasks` 页面增加“队列”视图：排队中 / 运行中 / 最近完成 / 失败；支持取消、重试、查看报告。
5. 状态标签补充 `queued`、`cancelled`、`timeout`；i18n 增加对应文案。
6. `npm run contracts:generate` 重新生成 `evalscope/web/src/api/generated/contracts.ts`，不要手改生成物。
7. 刷新页面后：任务列表从服务端加载，不再依赖 `sessionStorage` 里的单个 task id（可保留作为“上次查看”）。

### 4.10 安全与容量

- **队列上限**：`EVALSCOPE_QUEUE_MAX_DEPTH`（默认 50），超过返回 `429`，防止单人灌满队列。
- **鉴权**：服务本身无鉴权；生产必须放在 Nginx/Caddy 后，启用 Basic Auth/OIDC/IP 白名单。
- **Redis 不对外暴露**：只放在 Compose 内部网络，不 publish 端口；需要调试时用 `docker compose exec redis redis-cli`。
- **密钥处理**：提交请求里的 `api_key` 会进入 RQ job payload，Redis 因此包含敏感信息。措施：
  - Redis 不暴露、可选 `requirepass`、卷权限 0700；
  - 不要把完整 job payload 打进日志；
  - 可选增强：worker 侧用 `EVALSCOPE_QUEUE_ENCRYPTION_KEY` 对 payload 做对称加密；或改为从外部 secret store 注入。
- **任务配置落盘**：`outputs/<id>/configs/task_config.yaml` 已有 `SecretStr` 掩码逻辑，保持；
  不要为了“重试”把明文密钥写到磁盘。
- **资源限制**：Compose 提供可选 `mem_limit`/`cpus`；不要把限制设得比单次评测峰值低，否则会 OOM，
  反而制造“任务失败”。建议先压测出峰值再设置。
- **结果/日志体积**：任务日志和 AIPerf 原始 JSONL 会持续增长；需要保留策略（按天/按大小清理），
  但不要和队列稳定性混在一个改动里。

---

## 5. Docker 部署改造

### 5.1 目标 Compose 拓扑

```
evalscope-web   (Flask + waitress, 端口 9000)
evalscope-worker (RQ worker, 无端口)
evalscope-redis  (Redis 7, 无端口)
          共享卷：/data/outputs、/data/cache
```

### 5.2 Compose 参考改动

```yaml
services:
  redis:
    image: ${EVALSCOPE_REDIS_IMAGE:-redis:7.4-alpine}
    container_name: ${EVALSCOPE_REDIS_CONTAINER_NAME:-evalscope-redis}
    restart: unless-stopped
    command:
      - redis-server
      - --appendonly
      - "yes"
      - --appendfsync
      - everysec
      - --save
      - "900 1"
      - --maxmemory-policy
      - noeviction
      # 可选：--requirepass ${EVALSCOPE_REDIS_PASSWORD}
    volumes:
      - ${EVALSCOPE_REDIS_MOUNT:-evalscope_redis}:/data
    environment:
      # redis-cli 读取该变量；留空时等价于无密码
      REDISCLI_AUTH: ${EVALSCOPE_REDIS_PASSWORD:-}
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 10
      start_period: 5s
    logging:
      driver: json-file
      options: { max-size: "20m", max-file: "3" }

  evalscope:
    # ...现有配置保持不变...
    environment:
      # ...现有环境变量...
      EVALSCOPE_TASK_BACKEND: rq
      EVALSCOPE_REDIS_URL: redis://redis:6379/0
      EVALSCOPE_QUEUE_MAX_DEPTH: "50"
    depends_on:
      redis:
        condition: service_healthy

  worker:
    image: ${EVALSCOPE_IMAGE:-evalscope:latest}
    container_name: ${EVALSCOPE_WORKER_CONTAINER_NAME:-evalscope-worker}
    restart: unless-stopped
    command:
      - worker
      - --redis-url
      - redis://redis:6379/0
      - --queues
      - perf
      - eval
      - --concurrency
      - "${EVALSCOPE_WORKER_CONCURRENCY:-1}"
    environment:
      TZ: ${TZ:-Asia/Shanghai}
      EVALSCOPE_TASK_BACKEND: rq
      EVALSCOPE_REDIS_URL: redis://redis:6379/0
      EVALSCOPE_OUTPUT_DIR: /data/outputs
      EVALSCOPE_AIPERF_EXECUTABLE: ${EVALSCOPE_AIPERF_EXECUTABLE:-/opt/aiperf-venv/bin/aiperf}
      MODELSCOPE_CACHE: /data/cache/modelscope
      EVALSCOPE_CACHE: /data/cache/evalscope
      HF_HOME: /data/cache/huggingface
      HF_DATASETS_CACHE: /data/cache/huggingface/datasets
    volumes:
      - ${EVALSCOPE_OUTPUTS_MOUNT:-evalscope_outputs}:/data/outputs
      - ${EVALSCOPE_CACHE_MOUNT:-evalscope_cache}:/data/cache
    depends_on:
      redis:
        condition: service_healthy
    stop_grace_period: 120s
    healthcheck:
      test:
        - CMD
        - evalscope
        - worker
        - --health-check
        - --redis-url
        - redis://redis:6379/0
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    ulimits:
      nofile:
        soft: 65535
        hard: 65535
    logging:
      driver: json-file
      options: { max-size: "50m", max-file: "5" }

volumes:
  evalscope_outputs:
    name: ${EVALSCOPE_OUTPUTS_VOLUME:-evalscope_outputs}
  evalscope_cache:
    name: ${EVALSCOPE_CACHE_VOLUME:-evalscope_cache}
  evalscope_redis:
    name: ${EVALSCOPE_REDIS_VOLUME:-evalscope_redis}
```

要点：

- worker 与 web **必须共享** `/data/outputs` 和 `/data/cache`（命名卷或同一绑定目录）。
- Redis 使用 AOF + RDB；`maxmemory-policy noeviction` 保证队列 key 不会被淘汰。
- Redis 不 publish 端口；需要从宿主机排查时用 `docker compose exec redis redis-cli`.
- 如果宿主有 GPU 遥测需求，worker 也要加和 web 相同的 `deploy.resources.reservations.devices`。
- 若希望评测与压测互不阻塞：再起一个 `worker-eval`（`--queues eval`）和一个 `worker-perf`
  （`--queues perf`）；压测 worker 必须 `concurrency=1`，并用任务锁保证全局只有一个 perf job。
  默认单 worker 更简单，也天然保证压测独占。

### 5.3 Dockerfile / entrypoint

- `requirements/service.txt` 增加：`rq`、`redis`、`waitress`（或新增 `requirements/queue.txt`
  并在 `service` extra 中引用；Dockerfile 当前直接安装 `service.txt`，加在这里最省事）。
- `Dockerfile` 的 `HEALTHCHECK` 保持 `/health`（liveness）；`/ready` 给反向代理/人工检查。
- `entrypoint.sh` 增加子命令分发：

```sh
case "${1:-}" in
  worker)
    shift
    exec evalscope worker "$@"
    ;;
  "")
    exec evalscope service --host "$HOST" --port "$PORT" --outputs "$OUTPUT_DIR" "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
```

- worker 容器复用同一镜像，不额外构建；AIPerf venv、`/bin/ps`、缓存环境都已具备。
- 如果 worker 启动时 Redis 还没就绪，CLI 内部做指数退避重连（默认一直重试），避免 crash loop；
  Compose 的 `depends_on: service_healthy` 只是第一道保障。

### 5.4 `.env.example` 新增项

```dotenv
# --- Queue ---------------------------------------------------------------
EVALSCOPE_TASK_BACKEND=rq
EVALSCOPE_REDIS_URL=redis://redis:6379/0
EVALSCOPE_REDIS_IMAGE=redis:7.4-alpine
EVALSCOPE_REDIS_CONTAINER_NAME=evalscope-redis
EVALSCOPE_REDIS_VOLUME=evalscope_redis
EVALSCOPE_REDIS_MOUNT=
# EVALSCOPE_REDIS_PASSWORD=

# --- Worker --------------------------------------------------------------
EVALSCOPE_WORKER_CONTAINER_NAME=evalscope-worker
EVALSCOPE_WORKER_CONCURRENCY=1
EVALSCOPE_WORKER_TTL=420
EVALSCOPE_JOB_TIMEOUT=0
EVALSCOPE_TASK_DEADLINE_SECONDS=0
EVALSCOPE_EVAL_AUTO_RETRY=1

# --- Web server ----------------------------------------------------------
EVALSCOPE_WEB_THREADS=8
```

### 5.5 健康检查与验收脚本

`verify.sh` 增加：

```sh
printf '\n== queue ==\n'
docker compose exec -T redis redis-cli ping
docker compose exec -T evalscope python -c "
import json, urllib.request
print(json.load(urllib.request.urlopen('http://127.0.0.1:9000/ready', timeout=5)))
"
docker compose exec -T evalscope python -c "
import json, urllib.request
stats = json.load(urllib.request.urlopen('http://127.0.0.1:9000/api/v1/queue/stats', timeout=10))
print(json.dumps(stats, ensure_ascii=False, indent=2))
"
```

`/ready` 返回 `503` 时，明确区分：

- `redis: false` → Redis 不可达；
- `worker: false` → 没有活跃 worker 心跳（队列仍可入队，但不会执行）；
- `queue: false` → RQ key 不可读。

### 5.6 日志与备份

- Web/worker 容器日志：沿用 `json-file` 轮转；任务日志在 `/data/outputs/<task_id>/logs/`。
- Redis：AOF/RDB 在 `evalscope_redis` 卷；备份方式与现有 outputs 卷一致。
- 备份建议：Redis 卷 + outputs 卷一起备份；只备份 outputs 会丢失队列/索引，但历史报告仍在。
- 恢复：先起 Redis（AOF 自动恢复），再起 worker/web；worker 会自动出队。
- 注意：Redis 的 AOF 卷不要和宿主机上的其他 Redis 实例混用；`appendonlydir` 会随版本变化。

---

## 6. 代码改动清单

### 6.1 后端

> Phase 1 已按下面的“实际落地”实现；本节其余表格描述的是 Phase 2 的 RQ 目标布局。

Phase 1 实际落地文件：

| 文件 | 改动 |
| --- | --- |
| `evalscope/service/utils/task_queue.py` | 新增 `TaskQueue`（FIFO、守护 worker、取消、`queue_position`、终态兜底落盘）与进程级单例 |
| `evalscope/service/blueprints/eval.py` | `?async=1` 时入队并返回 202 `TaskSubmitResponse`；`/stop` 先取消排队任务 |
| `evalscope/service/blueprints/perf.py` | 同上；AIPerf 缓存实验/单次运行与原生压测统一入队 |
| `evalscope/service/blueprints/tasks.py` | 合并队列快照；新增 `GET /tasks/<id>/result` |
| `evalscope/service/api_models/common.py` | `ActiveTaskInfo` 增加 `queued` 状态、`enqueued_at`、`finished_at`、`error`、`queue_position`；新增 `TaskSubmitResponse` |
| `evalscope/web/src/api/{task,perf}.ts` | 提交固定携带 `async=1`，返回 `TaskSubmitResponse` |
| `evalscope/web/src/components/tasks/*` | 排队位置、排队进度条、提交后不再等待最终结果 |
| `tests/service/test_task_queue.py` | 队列 FIFO/取消/持久化/失败、异步提交与 `/result`、阻塞兼容 |

Phase 2 目标布局：

| 文件 | 改动 |
| --- | --- |
| `requirements/service.txt` | 增加 `rq`、`redis`、`waitress`（精确版本） |
| `evalscope/service/queue/__init__.py` | 新包 |
| `evalscope/service/queue/models.py` | `TaskRecord`、`TaskSubmitResponse`、状态枚举、错误码 |
| `evalscope/service/queue/store.py` | Redis 任务记录、索引、取消键、锁、心跳；磁盘 `task_info.json` 镜像 |
| `evalscope/service/queue/broker.py` | RQ 队列入队/取消/位置/统计；JSON serializer；幂等提交 |
| `evalscope/service/queue/worker.py` | job 函数（eval/perf/resume）、取消轮询、状态回写、`handle_job_failure` |
| `evalscope/service/queue/reconciler.py` | 死 worker 遗留任务扫描/重排队兜底（可选，随 worker 启动） |
| `evalscope/service/blueprints/eval.py` | `/invoke`、`/resume/invoke` 改为校验 + 入队 + 202；`/stop` 改为取消键 |
| `evalscope/service/blueprints/perf.py` | 同上；AIPerf/原生压测统一入队 |
| `evalscope/service/blueprints/tasks.py` | 任务列表、队列统计、retry、result；合并 Redis + 磁盘 |
| `evalscope/service/api_models/common.py` / `eval.py` / `perf.py` | 新状态、字段、响应模型 |
| `evalscope/service/app.py` | 初始化队列；`/health`、`/ready`；waitress 启动；`run_service` 保持 CLI 兼容 |
| `evalscope/service/utils/process.py` | `run_in_subprocess(..., should_cancel=...)`；Linux `PR_SET_PDEATHSIG`；取消/清理路径 |
| `evalscope/cli/start_worker.py` | `evalscope worker` 子命令、健康检查、Redis 重连 |
| `evalscope/cli/cli.py` | 注册 worker 子命令 |

### 6.2 前端

| 文件 | 改动 |
| --- | --- |
| `evalscope/web/src/api/task.ts` | `submit()` 返回 `TaskSubmitResponse`；新增 `listTasks`、`retry`、`cancel`、`result` |
| `evalscope/web/src/api/tasks.ts` | `/tasks` 列表与队列统计 |
| `evalscope/web/src/components/tasks/TaskRunnerShell.tsx` | 提交后不再等待最终结果 |
| `evalscope/web/src/components/tasks/TaskRunMonitor.tsx` | 支持 `queued`/`cancelled`/`timeout`，展示排队位置/attempt/worker |
| `evalscope/web/src/components/tasks/ActiveTasksPanel.tsx` | 改读 `/tasks?status=queued,running` |
| `evalscope/web/src/pages/TasksPage.tsx` | 增加队列视图、取消/重试按钮 |
| `evalscope/web/src/i18n/*` | 新状态文案 |
| `evalscope/web/src/api/generated/contracts.ts` | `npm run contracts:generate` 重新生成 |

### 6.3 Docker / 文档 / 测试

| 文件 | 改动 |
| --- | --- |
| `docker/docker-compose.yml` | 新增 redis、worker；volume、depends_on、healthcheck |
| `docker/entrypoint.sh` | `worker` 子命令分发 |
| `docker/Dockerfile` | 队列依赖、健康检查说明、worker 可直接复用镜像 |
| `docker/.env.example` | 新增队列/worker 配置 |
| `docker/README.md` | 新拓扑、运维、超时说明、备份/恢复 |
| `docker/verify.sh` | Redis/worker/queue 检查 |
| `tests/service/test_task_queue.py` | 状态机、入队、幂等、取消、队列上限 |
| `tests/service/test_worker_recovery.py` | worker 崩溃/重排队/锁/恢复（需要真实 Redis 或 fake broker） |
| `tests/service/test_service_api_async.py` | 202 提交、轮询、result、retry |
| `tests/web/*` | 前端提交/队列视图测试 |

### 6.4 工作量参考

- 后端队列/worker/状态/取消/恢复：约 800–1200 行（含 Pydantic 模型与测试）；
- API/前端：约 400–600 行；
- Docker/文档/验收脚本：约 200 行；
- 长任务/崩溃/负载测试：2–5 天（取决于环境）。
- 一个熟悉该代码库的工程师，完成可上线版本约 **1.5–2.5 周**；只做“本地异步化”可压缩到 3–5 天，但不满足重启恢复。

---

## 7. 分阶段实施

### Phase 1：异步化（解决“必须停留页面”）——已完成

已落地的范围：

- 新增 `evalscope/service/utils/task_queue.py`：`TaskQueue` 进程内 FIFO 队列，守护线程消费，无 Redis 依赖；
  worker 数量由 `EVALSCOPE_TASK_QUEUE_WORKERS` 控制（默认 1，保持压测独占）。
- `/api/v1/eval/invoke`、`/api/v1/eval/resume/invoke`、`/api/v1/perf/invoke`、`/api/v1/perf/single/invoke`
  支持 `?async=1`：入队后立即返回 `202` + `TaskSubmitResponse`
  （`status`、`task_id`、`enqueued_at`、`queue_position`）；不带该参数时保持原来的阻塞语义，老脚本/测试不受影响。
- 前端（`task.ts` / `perf.ts`）始终带 `async=1` 提交，随后轮询 `/tasks/<id>` 与 `/tasks/active`。
- 任务状态落盘：入队时写 `task_info.json`（`queued` + `enqueued_at`），执行时更新为 `running`，结束时写终态和
  `result.json`；新增 `GET /api/v1/tasks/<task_id>/result`（未结束时返回 `202`，结束后原样返回结果 payload），
  页面刷新后可以直接恢复进度与结果。
- `/tasks/active` 和 `/tasks/<id>` 合并队列快照，排队中的任务在子进程创建前即可见，并带 `queue_position`；
  前端排队态显示“排队第 N 位”和不确定进度条。
- `/eval|perf/stop` 先尝试从队列取消：排队中的任务直接出队并标记 `stopped`（不会被执行），运行中的任务照旧终止子进程。
- 结果对阻塞与异步路径都落盘，`/tasks/<id>/result` 对历史任务同样可用。
- 验收：关闭浏览器/刷新页面后任务继续，重新打开能看到进度和结果。

已知边界（Phase 2 解决）：

- Web 进程重启会丢失内存队列中尚未开始的任务；已开始的任务随子进程一起终止。重启后 `task_info.json`
  中的 `queued/running` 状态会因没有活跃进程而被视为 `unknown`，需要人工重提。
- 排队任务被取消与 worker 取任务之间存在很短的时间窗：`cancel` 在锁内摘除记录，worker 在锁内二次确认，
  不会真正执行已取消的任务；但 worker 刚把任务标记为 `running` 后才到达的 stop 会走子进程终止路径。
- 单进程单 worker 无跨进程容量/重试/崩溃恢复——这些属于 Phase 2 的 RQ + Worker。

> 这一阶段不解决“Web 重启丢任务”，但风险最低，可先上线验证前端交互。

### Phase 2：RQ + Worker（解决重启/崩溃/容量）

- 增加 Redis + RQ 后端、worker CLI、Compose 服务；
- 取消/重试/队列统计/健康检查；
- 默认 `concurrency=1`，评测/压测队列；
- 验收：重启 Web 任务不中断；杀 worker 后任务重排队并 `use_cache` 恢复；压测独占。

### Phase 3：稳定性加固

- 任务锁、PDEATHSIG、reconciler、Redis AOF 验证；
- 长任务（> 7 分钟、> 1 小时）、取消、队列上限、Redis 重启演练；
- 反向代理/鉴权/资源限制文档；
- 可选：SSE 日志、任务保留策略、Prometheus 指标。

---

## 8. 测试与验收

### 8.1 必测场景

| # | 场景 | 预期 |
| --- | --- | --- |
| 1 | 提交评测后立即关闭浏览器 | 任务继续，稍后可在 `/tasks` 看到完成 |
| 2 | 提交后刷新/换设备打开 Dashboard | 能看到运行中任务和进度 |
| 3 | Web 容器重启 | 任务继续；重启后列表仍可见 |
| 4 | Worker 进程 `kill -9` | 任务重排队并 `use_cache` 恢复，或标记 `worker_lost` 并给出原因；无重复执行 |
| 5 | Worker 容器重启 | 同上；容器内子进程全部清理 |
| 6 | Redis 容器重启 | 队列/记录从 AOF 恢复；worker 自动重连 |
| 7 | 排队中取消 | 状态 `cancelled`，不再执行 |
| 8 | 运行中取消 | 子进程在合理时间内终止，状态 `stopped`，无残留进程 |
| 9 | 长任务 > worker_ttl（7 分钟） | 不被误判 abandoned，进度持续更新 |
| 10 | 长任务 > 1 小时 | 无 timeout，正常完成 |
| 11 | 连续提交 50 个 mock 任务 | 队列深度正确，内存稳定；超过上限返回 429 |
| 12 | 压测与评测同时提交 | 压测独占执行；评测排队或走独立 worker（按配置） |
| 13 | Redis 不可达 | Web 提交返回 503 且信息明确；历史报告仍可浏览 |
| 14 | 代理 60s 超时 | 提交/轮询不受影响（无长请求） |
| 15 | 配置错误/模型 4xx | 任务 `error` 且日志可定位；不自动无限重试 |

### 8.2 测试命令

```bash
# 单元测试（fake broker / 状态机）
pytest tests/service/test_task_queue.py -v

# 集成测试（需要一次性 Redis，建议使用独立 DB 15）
EVALSCOPE_TEST_REDIS_URL=redis://127.0.0.1:6379/15 \
  pytest tests/service/test_worker_recovery.py -v

# 现有 CI 烟测
pytest tests/cli/test_all.py::TestRun::test_ci_lite -v -s -p no:warnings

# Docker 冒烟
cd docker
docker compose up -d --build
sh verify.sh
```

### 8.3 上线验收标准

- 提交接口 P99 < 1s（不含网络）；
- 任何任务执行都不再持有长 HTTP 连接；
- Web/worker/Redis 任一重启，任务不丢失（运行中任务可恢复或明确失败）；
- 1 小时长任务无 timeout、无重复执行；
- 取消能在 30s 内生效（取决于子进程清理）；
- 队列深度、worker 心跳、失败任务在 Dashboard/`/queue/stats` 可见；
- 文档明确“哪些超时可配、默认值是什么、为什么不会随便 timeout”。

---

## 9. 风险与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| Redis 成为单点 | 队列不可用 | 单机部署可接受；AOF+RDB、`restart: unless-stopped`；未来需要 HA 时换 Sentinel/托管 Redis |
| Redis 中保存 api_key | 敏感信息泄露 | 不暴露端口、可选密码、卷权限、日志脱敏；可选 payload 加密 |
| worker 失联误判 | 任务重复执行 | 保持默认心跳；任务锁 + `use_cache` 幂等；上线前长任务验证 |
| 任务锁过期但进程还活着 | 双跑 | 锁 TTL 足够长并续期；PDEATHSIG + 容器隔离；发现锁冲突直接失败而非并行 |
| RQ 版本升级 | API 行为变化 | 固定版本；升级前跑长任务/取消/恢复回归 |
| 嵌套 multiprocessing | worker 内 spawn 失败/死锁 | worker 入口同样 `set_start_method('spawn', force=True)`；避免 fork 后继承锁 |
| AIPerf 与评测并发 | 压测指标失真 | 默认单 worker、concurrency=1；或独立 perf worker + 全局锁 |
| 磁盘增长 | 卷满导致写入失败 | 输出/日志保留策略；监控磁盘；Redis `maxmemory-policy noeviction` 但不要设过低 |
| 前端 breaking change | 旧脚本失效 | 文档说明 202 语义；提供 `?wait=true` 兼容；或保留旧端点一个版本 |
| 无鉴权 | 队列被灌满 | 反向代理鉴权、队列上限、速率限制、只读 Dashboard 与提交分离（未来） |

---

## 10. 需要确认的决策

1. **是否接受新增 Redis 容器**？
   - 推荐：接受（本方案）。
   - 备选：SQLite 队列 + 单 worker，零新增组件，但崩溃恢复/多 worker 要自己实现。
2. **评测/压测是否共用一个 worker**？
   - 推荐：默认一个 worker、`concurrency=1`，压测天然独占；后续按需要拆 `worker-eval` / `worker-perf`。
3. **`/invoke` 是否允许 breaking change（202 + 轮询）**？
   - 已定（Phase 1）：不做 breaking change，改为 `?async=1` 显式开启；前端固定携带，脚本保持原阻塞语义。
     Phase 2 若需要统一契约，再把默认值切到异步并保留 `?wait=true`。
4. **评测崩溃后是否自动重试 1 次**？
   - 推荐：是，仅针对 worker 失联，使用 `use_cache` 恢复；业务异常不自动重试。
5. **任务截止时间默认值**？
   - 推荐：默认关闭（0）；需要时按任务/全局显式配置，避免“动不动 timeout”。

---

## 11. 参考资料

- RQ 文档（job / worker / timeout / retry）：<https://python-rq.org/docs/>
- RQ 任务与取消语义：<https://python-rq.org/docs/jobs/>
- RQ 异常与 `Retry`：<https://python-rq.org/docs/exceptions/>
- RQ 默认常量（`worker_ttl=420`、维护间隔等）：<https://github.com/rq/rq/blob/master/rq/defaults.py>
- RQ `StartedJobRegistry.cleanup`（abandoned job 重排队/失败）：<https://github.com/rq/rq/blob/master/rq/registry.py>
- Redis 持久化（AOF/RDB、`appendfsync everysec`）：<https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/>
- Celery Redis visibility timeout（默认 3600s 与重复投递风险）：<https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html>
- Celery 任务 ack/时间限制：<https://docs.celeryq.dev/en/stable/userguide/tasks.html>
- Dramatiq TimeLimit/Retries 默认值：<https://dramatiq.io/reference.html>
- Huey 交付语义（at-most-once）：<https://huey.readthedocs.io/en/latest/guide.html>
- Temporal Activity 失败检测/heartbeat：<https://docs.temporal.io/encyclopedia/detecting-activity-failures>

# AIPerf 前缀缓存实验

性能任务页面保留原有 EvalScope 执行器，并提供可选的 AIPerf 执行器；默认仍为 EvalScope。AIPerf 0.13.0 支持 OpenAI 兼容的流式 chat completions、单次提示词压测，以及前缀复用比例、负载和重复次数构成的实验矩阵。服务端必须能读取 tokenizer（本地目录，或 `Qwen/Qwen2.5-0.5B-Instruct` 这类 ModelScope 模型 ID；远程来源只下载 tokenizer 文件并复用缓存），且其 chat template 应与模型服务一致。

## 安装隔离运行环境

以下依赖锁已在 macOS arm64、Python 3.12.11 本地运行。它与 EvalScope 环境分离，不改变框架支持的 Python 3.10–3.12 范围：

```bash
python3.12 -m venv /path/to/aiperf-venv
/path/to/aiperf-venv/bin/python -m pip install -r requirements/aiperf-macos-arm64-py312.lock
/path/to/aiperf-venv/bin/aiperf --version
export EVALSCOPE_AIPERF_EXECUTABLE=/path/to/aiperf-venv/bin/aiperf
evalscope service
```

`requirements/aiperf-linux-x86_64-py312.lock` 是 Linux x86_64 glibc、Python 3.12 的二进制依赖锁候选，已跨平台解析，并在 Rosetta 模拟的 Linux x86_64 环境完成安装和依赖检查；原生 Linux 运行及部署验收仍需在目标机器完成。执行器要求支持的 POSIX 进程组实现（Linux 或 macOS）以及 `/bin/ps`。请按请求预算规划进程容量、网络和输出磁盘；平台没有全局并发配额或自动保留策略。

### Linux 服务器准备与本地回归

以下命令从项目根目录执行。使用两个不同的 Python 虚拟环境：框架环境安装服务依赖，AIPerf 环境只安装锁文件。先在目标服务器确认 `python3.12`、`/bin/ps`、网络及磁盘容量；从源码构建 Web 还需要 Node.js 20.19 或更高的 20.x 版本，或 22.12 及以上版本。下面的命令不需要 Docker。请根据部署位置修改虚拟环境路径。

```bash
FRAMEWORK_VENV="$HOME/.venvs/iss-eval-framework"
AIPERF_VENV="$HOME/.venvs/iss-eval-aiperf"
python3.12 -m venv "$FRAMEWORK_VENV"
"$FRAMEWORK_VENV/bin/python" -m pip install -e '.[service]' pytest
python3.12 -m venv "$AIPERF_VENV"
"$AIPERF_VENV/bin/python" -m pip install --only-binary=:all: -r requirements/aiperf-linux-x86_64-py312.lock
"$AIPERF_VENV/bin/python" -m pip check
"$AIPERF_VENV/bin/aiperf" --version
export EVALSCOPE_AIPERF_EXECUTABLE="$AIPERF_VENV/bin/aiperf"
export EVALSCOPE_TEST_AIPERF="$EVALSCOPE_AIPERF_EXECUTABLE"
"$FRAMEWORK_VENV/bin/python" -m pytest tests/perf/test_external_runner_lifecycle.py tests/perf/test_experiment_orchestration.py -q
"$FRAMEWORK_VENV/bin/python" -m pytest tests/service/test_aiperf_single_run.py::test_real_single_run_emits_client_progress_log -q
npm --prefix evalscope/web ci
npm --prefix evalscope/web run build
"$FRAMEWORK_VENV/bin/evalscope" service --host 127.0.0.1 --port 9000
```

这些自动化测试使用本机 mock 服务，不会验证真实 P/D 节点；五阶段停止测试可能耗时。若服务运行在容器内，须在实际部署环境另行验证 PID 1 回收、信号传递、文件权限、网络和持久化输出，不能把本机虚拟环境的结果当作容器验收。

### 真实节点验收

在受控测试窗口分别对 vLLM 和 SGLang 进行以下检查，先确定不影响共享服务的请求预算：

1. 记录每个 P/D 节点的引擎版本、模型和 tokenizer/template、Connector、路由、缓存配置、时钟同步状态及 Prometheus 标签；分别验证推理认证和指标认证。不要将凭据写入验收记录或提交到 Git。
2. 在 Web 分别配置 P、D 指标端点并执行只读探测；先用小流量试运行，确认每个节点的来源、标签和可用性。选定 `0, 80, 90`、两个负载点、三次重复后核对预检的 18 个子运行及请求预算，再发起实验。额外运行一组开放环负载。
3. 保存任务 ID、计划哈希、各子运行的请求哈希、质量原因、时间窗口及 raw/normalized 产物。对照生成比例、服务端请求级缓存用量和节点查询命中率的分子、分母及覆盖率；它们是不同观测，不要求等于目标比例或单调变化。
4. 将 P/D 排队、阶段和可用 KV 传输数据逐项对照对应节点的原始指标、单位、标签、样本数和窗口；记录共享流量与路由覆盖。指标缺失或语义无法确认时保留 unavailable/partial，不以客户端速率代替节点实测。
5. 在安全的测试环境验证预热、发压和导入期间停止，确认不再发送请求、进程树已清理且 partial 报告可读；重启服务后确认历史可读但任务不会自动续跑。分别检查原生压测、能力评测和旧报告。

记录通过、失败和未执行项以及部署版本。只有在实际节点、目标操作系统和部署方式完成检查后，才可将对应环境标为已验收。

服务检测配置的可执行文件并要求版本为 0.13.0，不会在请求时安装依赖或静默切换执行器。部署时保留运行环境锁、Python 版本和原始产物；升级 AIPerf 前应重新验证命令行、产物 schema、时间边界和进程清理，不能只升级软件包。

## 配置与运行

填写模型连接，选择 **AIPerf**，再选择 **前缀缓存实验** 或 **单次运行**。单次运行的每个非空行对应一个请求；浏览器草稿和模板可能保存提示词，请勿在共享浏览器输入秘密。Web 限制提示词总量为 512 KiB；API 最多接受 10,000 条、4 MiB。单次运行只有一个负载点，不含缓存实验。

tokenizer 可填服务端可读的本地目录，或 ModelScope 模型 ID；使用模型 ID 时服务端只下载 `tokenizer.json`、`tokenizer_config.json`、`chat_template.jinja` 等 tokenizer 文件，不会拉取模型权重。

实验可设置 `0, 80, 90` 等百分比、总输入 token、前缀池、随机种子、负载点、请求数及重复次数。预检展示子运行、正式请求和预热请求预算。chat template 的固定部分可能使短输入无法达到某些比例；预检会拒绝已知不可行的组合。实际生成时仍会验证分词，无法生成所需输入时会明确失败。每个共享前缀完成 primer 后才开始对应正式运行。新的命名空间不能证明服务端缓存已清空，也不能证明所有后端都收到预热。

分别添加 P、D Prometheus 节点，设置各自角色、引擎、可选标签过滤和 Bearer Token。模型认证与指标认证相互独立。指标凭据仅用于当前任务，不保存到浏览器草稿、模板或导出文件；更改目标来源会清除对应凭据。指标 URL 不支持内嵌凭据、查询参数或片段。P/D 模式要求节点指标时必须同时配置 P 和 D。可选端点探测只读；启动时服务端会再次检查必需节点。修改字段后需要重新预检。

## 解读结果

报告分别展示 **目标复用**、**实际生成的 token 前缀复用** 和 **服务端实测缓存使用**。仅当服务端对同一个成功请求同时返回缓存和输入 token 用量时，才有请求级缓存比例。vLLM 节点的查询 token 命中率属于另一种节点窗口指标。缺失或不支持的值显示为不可用，不补零。请求延迟分组仅使用服务端实报数据，百分位按原始请求的最近秩计算；已观测请求超过 20 万时分组不可用。各轮结果独立呈现，不把每轮 P99 平均后称为整体 P99。

节点面板按节点归属展示可用的 vLLM/SGLang 排队、prefill/decode 和 KV 传输数据。Gauge、Counter 和 Histogram 的窗口语义不同：峰值是采样峰值，Histogram 分位数是近似值，覆盖率、来源、单位、样本数、标签、边界及缺失原因均保留。时间序列图先按节点、指标、完整标签与窗口筛选，再按序号均匀选取最多 500 个原始点；峰值可能被抽掉，断点可能显得更宽。单次查询最多扫描 100 万个候选点，超过时需缩小窗口；原始样本仍可通过分页 API 读取。节点窗口包含共享流量，不是逐请求 P/D Trace。指标映射参考 vLLM 0.10.2 和 SGLang 0.5.2/0.5.23 源码；必须核验实际部署版本、标签、时钟、Connector 和路由。远端模板一致性与路由覆盖未验证前，进程即使成功退出，结果也可能仍为 partial。

实时面板显示有界的节点实际抓取样本，断采或过期时保留上次值并标记 stale；当 AIPerf 0.13.0 输出实时客户端日志时，面板会独立显示已完成记录数及按 0.1 取整的每秒速率；发送完成事件出现后才显示已发送数。短任务或缺少日志采样时仍显示未知。客户端实时日志以两秒为周期启用，会增加测量开销；日志时间按服务主机本地时区解释。显式对比固定负载或目标比例及重复轮次，并要求指标来源、范围、单位和 AIPerf 版本一致；跨实验还要求不含凭证的配置身份一致。不同实验使用独立命名空间，并非逐请求配对。在当前矩阵页选择一组并点击“读取全部重复轮次”后，系统分页读取整个实验的重复运行，展示每轮样本数与已读取的可比范围，不合并各轮尾延迟；读取失败或未完成时会标明，运行中的各轮可能采样于不同时刻。基准为零时相对变化未知。独立 HTML 流式输出完整实验和请求明细，文件可能较大。

## 故障、保留与回滚

取消请求得到确认后仍可能处于 `cancelling`；只有清理确认后任务才显示停止。有可读产物时可查看 partial 报告。服务重启可读取归档，不会恢复实验调度。请按保留策略一起保存任务目录、原始 AIPerf 产物、tokenizer/template 标识和解析器版本；不要删除活动任务输出。

可在原始文件副本上使用新的输出目录检查重新导入，避免修改已发布报告：

```python
from evalscope.perf.results.aiperf_import import import_aiperf_run
from evalscope.perf.results.models import AIPerfImportArguments

result = import_aiperf_run(AIPerfImportArguments(
    raw_dir='/path/to/copied/raw/aiperf',
    normalized_dir='/path/to/new/normalized',
    allow_partial_tail=True,
))
print(result.quality, result.quality_reasons)
```

节点归属需要原始节点引用；不提供时节点身份不可用。该操作不会自动替换已发布场景或重新计算其节点分析。重新执行会创建新任务，并非续跑。

### 运行时升级

服务只接受 `--version` 为 0.13.0 的可执行文件。替换为其他版本会使 AIPerf 不可用，新 AIPerf 任务返回 503，不会自动回退。升级应按代码变更处理：更新固定版本及其 CLI/导出适配、补充代表性 fixture 和测试、重新运行本地检查，再重复服务器验收。保留旧虚拟环境和归档原始产物，以便在不改写历史的情况下恢复旧运行时。

回滚时先停止活动 AIPerf 任务并等待清理确认，再从服务环境移除 `EVALSCOPE_AIPERF_EXECUTABLE` 并重启。已有报告与原始产物仍可读取，新任务可手动选择 EvalScope。迁移会保留旧版 v1 浏览器数据，但旧客户端读取的是 v1 快照，不会读取之后的 v2 编辑。

以下本地重启演练使用锁定版本的 AIPerf 虚拟环境和 loopback 模拟服务：启动真实的 Flask 服务进程，停止该进程后，在相同输出根目录上启动第二个未配置 `EVALSCOPE_AIPERF_EXECUTABLE` 的进程。它检查归档运行仍可通过列表、详情、运行、请求、图表和 HTML 接口读取，内存中的活动任务登记为空，新 AIPerf 任务不会发压且明确返回 503，并且旧扁平原生 payload 在重启后仍能完成。另一个场景在任务运行中强杀服务进程，检查请求停止、任务被报告为 unknown 而不是 completed，且不生成伪造的运行摘要。该演练不覆盖 Linux/容器 cgroup 清理、服务进程树之外的控制器被强杀，或服务器部署演练。

```bash
EVALSCOPE_TEST_AIPERF="$AIPERF_VENV/bin/aiperf" "$FRAMEWORK_VENV/bin/python" -m pytest tests/service/test_aiperf_service_restart.py -q
```

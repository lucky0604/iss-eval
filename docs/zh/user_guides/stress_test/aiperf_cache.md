# AIPerf 前缀缓存实验

性能任务页面保留原有 EvalScope 执行器，并提供可选的 AIPerf 执行器；默认仍为 EvalScope。AIPerf 0.13.0 支持 OpenAI 兼容的流式 chat completions、单次提示词压测，以及前缀复用比例、负载和重复次数构成的实验矩阵。服务端必须能读取 tokenizer，且其 chat template 应与模型服务一致。

## 安装隔离运行环境

以下依赖锁已在 macOS arm64、Python 3.12.11 本地运行。它与 EvalScope 环境分离，不改变框架支持的 Python 3.10–3.12 范围：

```bash
python3.12 -m venv /path/to/aiperf-venv
/path/to/aiperf-venv/bin/python -m pip install -r requirements/aiperf-macos-arm64-py312.lock
/path/to/aiperf-venv/bin/aiperf --version
export EVALSCOPE_AIPERF_EXECUTABLE=/path/to/aiperf-venv/bin/aiperf
evalscope service
```

`requirements/aiperf-linux-x86_64-py312.lock` 是 Linux x86_64 glibc、Python 3.12 的二进制依赖锁候选，已跨平台解析，但尚未在 Linux 上安装和运行验收；使用前需在目标机器验证。执行器要求支持的 POSIX 进程组实现（Linux 或 macOS）以及 `/bin/ps`。请按请求预算规划进程容量、网络和输出磁盘；平台没有全局并发配额或自动保留策略。

服务检测配置的可执行文件并要求版本为 0.13.0，不会在请求时安装依赖或静默切换执行器。部署时保留运行环境锁、Python 版本和原始产物；升级 AIPerf 前应重新验证命令行、产物 schema、时间边界和进程清理，不能只升级软件包。

## 配置与运行

填写模型连接，选择 **AIPerf**，再选择 **前缀缓存实验** 或 **单次运行**。单次运行的每个非空行对应一个请求；浏览器草稿和模板可能保存提示词，请勿在共享浏览器输入秘密。Web 限制提示词总量为 512 KiB；API 最多接受 10,000 条、4 MiB。单次运行只有一个负载点，不含缓存实验。

实验可设置 `0, 80, 90` 等百分比、总输入 token、前缀池、随机种子、负载点、请求数及重复次数。预检展示子运行、正式请求和预热请求预算。chat template 的固定部分可能使短输入无法达到某些比例；预检会拒绝已知不可行的组合。实际生成时仍会验证分词，无法生成所需输入时会明确失败。每个共享前缀完成 primer 后才开始对应正式运行。新的命名空间不能证明服务端缓存已清空，也不能证明所有后端都收到预热。

分别添加 P、D Prometheus 节点，设置各自角色、引擎、可选标签过滤和 Bearer Token。模型认证与指标认证相互独立。指标凭据仅用于当前任务，不保存到浏览器草稿、模板或导出文件；更改目标来源会清除对应凭据。指标 URL 不支持内嵌凭据、查询参数或片段。P/D 模式要求节点指标时必须同时配置 P 和 D。可选端点探测只读；启动时服务端会再次检查必需节点。修改字段后需要重新预检。

## 解读结果

报告分别展示 **目标复用**、**实际生成的 token 前缀复用** 和 **服务端实测缓存使用**。仅当服务端对同一个成功请求同时返回缓存和输入 token 用量时，才有请求级缓存比例。vLLM 节点的查询 token 命中率属于另一种节点窗口指标。缺失或不支持的值显示为不可用，不补零。请求延迟分组仅使用服务端实报数据，百分位按原始请求的最近秩计算；已观测请求超过 20 万时分组不可用。各轮结果独立呈现，不把每轮 P99 平均后称为整体 P99。

节点面板按节点归属展示可用的 vLLM/SGLang 排队、prefill/decode 和 KV 传输数据。Gauge、Counter 和 Histogram 的窗口语义不同：峰值是采样峰值，Histogram 分位数是近似值，覆盖率、来源、单位、样本数、标签、边界及缺失原因均保留。节点窗口包含共享流量，不是逐请求 P/D Trace。指标映射参考 vLLM 0.10.2 和 SGLang 0.5.2/0.5.23 源码；必须核验实际部署版本、标签、时钟、Connector 和路由。远端模板一致性与路由覆盖未验证前，进程即使成功退出，结果也可能仍为 partial。

实时面板显示有界的节点实际抓取样本，断采或过期时保留上次值并标记 stale；当 AIPerf 0.13.0 输出实时客户端日志时，面板会独立显示已完成记录数及按 0.1 取整的每秒速率；发送完成事件出现后才显示已发送数。短任务或缺少日志采样时仍显示未知。客户端实时日志以两秒为周期启用，会增加测量开销；日志时间按服务主机本地时区解释。显式对比固定负载或目标比例及重复轮次，并要求指标来源、范围、单位和 AIPerf 版本一致；跨实验还要求不含凭证的配置身份一致。不同实验使用独立命名空间，并非逐请求配对。重复运行的范围和每轮样本数仅覆盖当前矩阵页，不合并各轮尾延迟。基准为零时相对变化未知。独立 HTML 流式输出完整实验和请求明细，文件可能较大。

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

回滚时先停止活动 AIPerf 任务并等待清理确认，再从服务环境移除 `EVALSCOPE_AIPERF_EXECUTABLE` 并重启。已有报告与原始产物仍可读取，新任务可手动选择 EvalScope。迁移会保留旧版 v1 浏览器数据，但旧客户端读取的是 v1 快照，不会读取之后的 v2 编辑。

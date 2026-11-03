# AIPerf 单次压测任务

服务可使用独立安装的 AIPerf 0.13.0 运行一次 OpenAI 兼容流式 Chat 压测。运行环境需要配置服务端环境变量 `EVALSCOPE_AIPERF_EXECUTABLE` 为隔离环境中可执行文件的绝对路径；平台不会自动安装或切换执行器。

在性能任务页面选择 AIPerf 和 **单次运行**，每行输入一条提示词。浏览器可能在本地草稿中保存提示词；Web 表单限制提示词总量为 512 KiB。

API 调用使用 `POST /api/v1/perf/invoke`，并通过 `EvalScope-Task-Id` 请求头标识任务。`input_prompts` 中每个非空字符串对应一次请求，数量须与 `request_count` 相等；单任务最多 10000 条、UTF-8 总计不超过 4 MiB。输入文件由服务在任务目录创建，权限为 0600，不能由客户端指定文件路径。

```json
{
  "engine": "aiperf",
  "aiperf": {
    "model": "served-model",
    "url": "http://localhost:8000/v1",
    "tokenizer_path": "/models/tokenizer",
    "input_prompts": ["First prompt", "Second prompt"],
    "load": {
      "mode": "closed_loop",
      "points": [{"concurrency": 2}],
      "request_count": 2,
      "output_token_limit": 128
    }
  }
}
```

单次运行支持一个并发或请求速率负载点。可通过 `api_key` 设置模型服务凭证；P/D 指标节点可分别配置 Prometheus 请求头和标签过滤，Web 表单支持每个节点独立的 Bearer Token。自定义推理请求头不受支持。任务停止只有在进程组清理确认后才报告为 stopped；原始产物与归一化结果会保留，可读任务会出现在性能报告归档中。安装、指标语义、保留和回滚说明见 [前缀缓存实验](aiperf_cache.md)。

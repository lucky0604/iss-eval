# AIPerf 单次压测 API

服务可使用独立安装的 AIPerf 0.13.0 运行一次 OpenAI 兼容流式 Chat 压测。运行环境需要配置服务端环境变量 `EVALSCOPE_AIPERF_EXECUTABLE` 为隔离环境中可执行文件的绝对路径；平台不会自动安装或切换执行器。

`POST /api/v1/perf/invoke` 使用 `EvalScope-Task-Id` 请求头标识任务。`input_prompts` 中每个非空字符串对应一次请求，数量须与 `request_count` 相等；单任务最多 10000 条、UTF-8 总计不超过 4 MiB。输入文件由服务在任务目录创建，权限为 0600，不能由客户端指定文件路径。

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

单次运行支持一个并发或请求速率负载点；不支持自定义推理请求头、Prometheus 认证或标签过滤。可通过 `api_key` 设置模型服务凭证。任务停止只有在进程组清理确认后才报告为 stopped；原始产物保留在服务端任务目录，单次运行的归一化报告尚未提供。

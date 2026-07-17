# DataAgent Data-Juicer Provider 实现说明 v1.1

本版本承接 v1.0，记录首批正式 Provider Proxy、Dataset 批量执行、运行事件、CPU
Golden Set 与模型发布闸门。v1.0 保留为此前单图片执行阶段的历史文档。

## 1. 本阶段结论

- Data-Juicer 继续作为外部 Operator Provider，不复制或改写其算子实现。
- 正式 Pipeline 只引用冻结的 `ProviderProxyOperator`，不直接引用动态搜索结果。
- Proxy 冻结 DataAgent 算子版本、Data-Juicer 版本、Provider ref、参数 schema、执行范围、
  依赖锁摘要和来源摘要。
- 首批准入 `image_shape_filter`、`image_aspect_ratio_filter` 和
  `image_deduplicator`。
- 单图片 Filter 使用 `asset` 执行范围；Deduplicator 使用 `dataset` 执行范围，一批数据
  只启动一次 `dj-process`。
- 外部执行仍禁止修改源图片、禁止 shell 拼接、禁止运行时自动安装依赖和模型。

## 2. Dataset 批量语义

DataAgent 为批次中的每个输入写入内部稳定 ID。Data-Juicer 输出必须保留该 ID，执行器再
将保留行映射为 `continue`，缺失行映射为 `reject`。如果 Provider 丢失全部 ID，批次失败，
不得按输出顺序猜测资产身份。

Dataset 级算子在逐资产循环前执行并缓存结果。为避免语义错位，当前 Dataset 算子必须位于
资产过滤和转换之前；允许前置只读 Ingestion 检查。

## 3. RunStore 事件

新增 `run_events` 表及 `GET /api/runs/{run_id}/events`。当前记录：

- Run 开始、计划冻结、评测、成功、失败、暂停和取消；
- Dataset 节点开始与完成；
- 每个资产的完成决定和进度；
- Provider 进程开始、返回码、耗时、输入数量及截断后的 stdout/stderr。

取消和暂停检查会传入隔离进程执行器。Windows 使用进程树终止，Provider 返回后由 Worker
把 Run 收敛到 `CANCELLED` 或 `PAUSED`，而不是误记为普通算子失败。

## 4. CPU Golden Set 与性能基线

执行命令：

```powershell
.\.venv\Scripts\python.exe scripts\run_datajuicer_cpu_golden.py
```

Golden Set 覆盖尺寸边界、宽高比边界和感知哈希重复图片。报告写入
`benchmarks/datajuicer-cpu-baseline-v1.json`，并以 `operator_benchmark` 类型追加到
`DomainVersionStore`。

本机 Data-Juicer `1.5.3` 首次正式基线通过 3/3 cases、6/6 assets，整体约
`0.0767 assets/s`。这是包含隔离进程冷启动的开发机基线，不是跨机器 SLA。

真实测试确认 `image_deduplicator` 需要 CPU 可选依赖 `imagededup`。该依赖已显式安装为
`imagededup==0.3.3.post2`；没有下载模型权重。运行时缺依赖仍会快速失败，不允许
Data-Juicer LazyLoader 自动改变环境。

## 5. GPU 模型发布闸门

当前开发机无 GPU，因此没有把任何模型算子提升为正式发布。代码已实现发布证据校验，必须
同时满足：

1. 在具有明确身份的独立 CUDA Worker 上运行；
2. `model_id`、不可变 revision 和 64 位 SHA256 与冻结需求完全一致；
3. 代码许可证和 checkpoint 许可证已审核且一致；
4. GPU Golden Set SHA256 存在，正确性和性能评测均通过。

证据缺失或字段不一致时，发布申请必须失败。模型下载仍保持关闭，待 GPU Worker 就绪后再
执行真实评测。

## 6. 后续工作

- 为更多 Data-Juicer 算子建立准入清单和独立 Golden Set，不因“可发现”自动发布；
- 将资产级 Filter 也合并为批量进程，降低当前约 28 秒的冷启动成本；
- 为 Provider 环境生成完整、可重建的依赖锁文件；
- 在独立 GPU Worker 上生成首份真实模型评测证据。

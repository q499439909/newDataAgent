# DataAgent Data-Juicer Provider 实现说明 v1.2

本版本承接 v1.1，记录完整目录、Candidate Proxy、图片 Agent 隔离和 CPU 图片批处理的实现结果。
v1.0 与 v1.1 保留为单资产执行和首批正式 Proxy 阶段的历史文档。

## 1. 当前结论

- Data-Juicer `1.5.3` 的 217 个算子均可被隔离 Worker 发现；
- 每个发现项都会生成稳定 ID 为 `datajuicer.<operator_ref>:1` 的版本化 Proxy；
- 动态发现目录与正式准入目录是两套数据，发现成功不会自动发布；
- 当前 214 个 Proxy 为 `DRAFT`，3 个为 `PERSONAL_RELEASE`；
- 非图片算子保留在 Provider Catalog，但不会进入当前图片 Agent 的生产候选；
- GPU 和模型算子保持 Draft，等待独立 GPU Worker 的许可证、revision、SHA256 和 Golden Set 证据。
- 启用 `DATAAGENT_ALLOW_DATAJUICER_CANDIDATES` 后，任务拆解会检索 Catalog，并允许匹配的 CPU 图片 Draft 进入受控执行；Draft 身份不会因此变成正式发布。

## 2. 三层目录

```text
Provider Discovery Catalog
        217 个外部元数据项
                 |
                 v
Versioned Candidate Proxy Catalog
        217 个 OperatorSpecVersion
        214 DRAFT + 3 PERSONAL_RELEASE
                 |
                 v
Image Agent Production Catalog
        默认只查询已发布且满足图片能力的算子
```

Provider Catalog 可通过以下接口查看：

```text
GET /api/operator-providers/datajuicer/operators?limit=500
GET /api/operator-providers/datajuicer/operators?tag=image&limit=500
```

版本化 Proxy Catalog 可通过以下接口查看：

```text
GET /api/operators?provider_id=datajuicer
GET /api/operators?provider_id=datajuicer&include_drafts=true
```

第一个接口默认隐藏 Draft，第二个接口明确用于开发、评测和准入工作。

## 3. 真实目录基线

2026-07-18 在本机隔离环境实测：

| 指标 | 数量 |
|---|---:|
| 全部算子 | 217 |
| Mapper | 132 |
| Filter | 57 |
| Deduplicator | 13 |
| Selector | 5 |
| Aggregator | 4 |
| Grouper | 3 |
| Pipeline | 3 |
| 图片算子 | 21 |
| CPU 图片算子 | 11 |
| GPU 图片算子 | 10 |
| PERSONAL_RELEASE | 3 |
| DRAFT | 214 |

目录按 Provider 版本写入磁盘缓存。正常启动优先读取缓存，`refresh=true` 才重新调用隔离 Worker。
刷新只更新发现目录；正式 Proxy 仍应通过重建 Registry 或重启服务形成新的启动快照，避免运行中静默换版本。

## 4. 自动适配规则

Data-Juicer 类型先转换为建议值，不直接视为最终人工分类：

| Data-Juicer 类型 | 默认执行范围 | DataAgent 建议类别 |
|---|---|---|
| Filter | asset，可批处理 | FILTERING |
| Mapper | asset | TRANSFORMATION 或基于名称推断 UNDERSTANDING |
| Deduplicator | dataset | DEDUPLICATION |
| Selector | dataset | SAMPLING |
| Aggregator / Grouper | dataset | EVALUATION |
| Pipeline | dataset | TRANSFORMATION |

`cpu` 和 `gpu` 标签分别生成 CPU 与 CUDA Runtime Profile。没有图片标签的算子使用通用
`ProviderDatasetRecord` 输入 Schema；它们可被治理和评测，但当前图片执行器会拒绝直接执行。

## 5. CPU 图片批处理

CPU 图片 Filter 与 Dataset 算子都实现 `supports_dataset_batch`。Dataset Worker 在逐资产循环前
准备整个批次，一批数据对每个批量节点只启动一次 `dj-process`。DataAgent 写入稳定内部资产 ID，
Provider 输出必须保留该 ID；丢失 ID 时整批失败，不按输出顺序猜测资产身份。

当前批处理优先覆盖位于 Pipeline 前缀中的过滤和去重节点。转换、增强或依赖上游衍生文件的节点
仍按资产执行，后续需要单独设计分阶段批次，而不是错误地用原始输入预计算。

## 6. 准入门槛

Candidate 只有同时满足以下条件才能提升到 `PERSONAL_RELEASE`：

1. 固定 Data-Juicer 版本、算子名、参数 Schema、源码摘要和依赖锁；
2. 图片输入输出语义已经映射为 DataAgent 协议；
3. CPU 或 GPU Golden Set 正确性通过；
4. 性能、取消、超时、stdout/stderr 摘要和失败恢复通过；
5. 模型算子补齐许可证、不可变 revision、权重 SHA256 和独立 Worker 证据；
6. 发布生成新的不可变 Operator 版本，不原地改变旧 Proxy 行为。

### 6.1 Candidate 自动调用

自动调用和正式发布是两件事。启用 Candidate 执行策略后：

1. Retrieval Agent 从需求和 `required_capabilities` 中提取图片处理意图；
2. Catalog Matcher 返回算子 ID、命中词、状态、Runtime、建议参数和阻塞原因；
3. CPU + image 匹配项会被 Processing Agent 编译为 Pipeline 节点；
4. GPU 匹配项会标记为不可执行并停止生成虚假可运行方案；
5. Run 提交时再次检查 Provider 版本、CPU/image 标签、Draft 策略和参数 Schema；
6. Worker 通过 Dataset Provider API 启动真实 `dj-process`。

真实烟测中，需求 `filter images by file size` 自动命中 Draft
`datajuicer.image_size_filter:1`，3 张图片只启动一次 `dj-process`，返回码为 0，最终 Run
`SUCCEEDED` 并发布 Dataset，保留 3、拒绝 0。

## 7. 下一步

1. 为剩余 8 个 CPU 图片 Candidate 建立逐算子 Golden Set 和参数边界；
2. 将通过评测的 CPU Filter 分批提升为 `PERSONAL_RELEASE`；
3. 为 Mapper 的输出图片、标注和衍生资产补齐分阶段 Dataset 执行协议；
4. 在独立 GPU Worker 中评测 10 个 GPU 图片算子；
5. 增加 Provider Catalog 差异报告，升级 Data-Juicer 时明确新增、删除和 Schema 变化。

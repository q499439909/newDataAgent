# DataAgent 开发接手文档 v9

> 交接日期：2026-07-24  
> 仓库：`D:\newDataAgent`  
> 当前分支：`codex/hybrid-operator-retrieval`  
> 当前 HEAD：`76865ce test: verify governed VLM parallel execution`  
> 上一版：`DataAgent-development-handoff-2026-07-23-v8.md`  
> v8 截止 HEAD：`c5d67d4 feat: stream agent action traces to the TUI`  
> 本文范围：v8 之后的 TaskSpec 契约修复、通用视觉筛选、Worker 单实例租约、Prompt/QC 正确性、受治理 Native Remote VLM，以及 Asset 级有界并行。  
> 当前自动回归：`234 passed, 1 warning in 24.19s`  
> Git 状态：当前分支比远端同名分支领先 48 个提交；v8 截止点到当前 HEAD 新增 13 个提交。

## 1. 我们在做什么

DataAgent 是一个面向图片数据生产的 Agent 控制平面。用户用自然语言描述：

- 图片数据在哪里；
- 想保留或排除什么内容；
- 是否需要分类、分目录、去重或质量筛选；
- 对不确定样本和源文件采用什么策略。

系统把自然语言需求转换为版本化 `TaskSpec`，再完成能力级拆解、Operator 检索、三条真实 Pipeline 编译、用户批准、独立 Worker 执行、逐资产审计、QC、修复和 DatasetVersion 发布。

v8 结束时，DataAgent 已经具备一键启动、模型生成澄清问题、受治理 Tool Loop、TUI 行动摘要、紧凑 Manifest 和共享 VLM 视觉证据。v8 之后的重点不是继续增加表面功能，而是解决真实任务暴露出来的正确性和性能问题：

1. 模型生成的 `TaskSpecPatch` 形状可能与 Domain Model 不一致；
2. “筛选穿黑色衣服图片”这类任务被错误编译成只有 decode 和 manifest 的空语义 Pipeline；
3. 旧 Worker 仍在后台运行时会抢走新任务，造成源目录、进度和代码版本错位；
4. Prompt 虽然存在，但旧版本没有真正绑定任务目标、分类标签和排除条件；
5. Run 可以文件层面成功，却没有证明语义筛选真的发生；
6. 编译器缺少分类契约时曾偷偷回退为猫狗标签；
7. Data-Juicer Remote VLM 每张图启动外部适配链路，延迟较高；
8. Native VLM 虽然更快，但最初仍按图片串行执行；
9. 并发若没有算子安全声明、Checkpoint、取消和数据库写保护，会破坏可恢复性。

当前面向简单视觉筛选任务的真实主链路是：

```text
用户自然语言
  -> Requirement Analyzer
  -> ConversationAction / TaskSpecPatch Schema 校验和标准化
  -> TaskSpecVersion
  -> 能力级拆解
  -> visual_semantic_selection
  -> Catalog 检索与 Runtime 重排
  -> Native Remote VLM + 确定性语义策略
  -> 三条 PipelineVersion
  -> 用户批准
  -> Worker 判断 Pipeline 是否支持 Asset 级并行
  -> 多张图片有界并行执行
  -> 单张图片内部节点按顺序执行
  -> Run events / node results / Native VLM evidence
  -> DatasetVersion / Manifest / QC
```

一个典型的“筛选穿黑色衣服图片”Pipeline 当前为：

```text
builtin.decode_check:1
  -> native.remote_vlm:1
  -> builtin.visual_semantic_selection:1
  -> builtin.manifest:1
```

这不是猫狗任务专用 Pipeline。任务目标、视觉条件、排除条件和分类上下文由版本化 Prompt 参数注入。

## 2. v8 之后讨论并确认的设计结论

### 2.1 模型负责语义，但不能绕过 Domain 契约

模型可以生成 Intent、Requirement 和 `TaskSpecPatch`，但模型输出不是控制面事实。进入执行前必须经过：

```text
模型 JSON
  -> JSON 修复
  -> ConversationAction Schema
  -> TaskSpecPatch Schema
  -> Domain 标准化
  -> ActionPolicy
  -> AgentRuntime
```

本阶段遇到的具体错误是模型把：

```json
{
  "mixed_label": {
    "id": "mixed",
    "display_name": "猫狗同框"
  }
}
```

返回给只接受字符串 ID 的 `ClassificationSpec.mixed_label`。正确处理不是给猫狗任务加字符串替换，而是在 Domain 边界统一把合法对象引用标准化为其 `id`，并在执行前校验整个 Patch。

确认的原则：

- 模型输出必须先验证再执行；
- 标准化应位于 Domain/Action 边界，而不是散落在对话分支中；
- 非法 Patch 不得部分落库；
- 控制面拒绝后可以进入 bounded ReAct，但不能重复提交同一个副作用动作。

### 2.2 任意视觉条件应编译为通用语义筛选，而不是任务专用算子

“穿黑衣”“戴眼镜”“室内场景”“包含车辆”等任务，不一定是闭集分类，但都属于：

```text
visual_semantic_selection
```

因此增加了通用能力和确定性策略算子：

```text
builtin.visual_semantic_selection:1
```

Remote VLM 只负责返回受约束的证据：

```text
semantic_match
semantic_mismatch
semantic_uncertain
```

下游 CPU 策略节点再根据 Pipeline 策略把 uncertain 解释为 keep、review 或 reject。

不要为每个条件新增：

```text
black_clothing_filter
red_car_filter
indoor_scene_filter
...
```

Operator 应保持原子、通用；任务差异进入 TaskSpec、PromptBinding 和参数。

### 2.3 旧 Worker 抢任务不是对话问题，而是执行身份问题

此前出现过：

- 用户任务源目录只有 23 张，进度却显示 27/32；
- 新代码已经启动，但任务仍走旧 Pipeline；
- TUI 和 API 看起来正常，实际 Run 被旧 Worker claim。

根因是同一个持久队列允许多个本地 Worker 同时 claim，而旧 Worker 仍持有旧源码和旧配置。

当前增加：

```text
.dataagent/platform/worker.lock.json
WorkerProcessLease
claim_protocol_version
```

同一个本地 platform 目录只允许一个 Worker。租约记录 PID、启动时间和随机 token；进程已死时可清理陈旧租约，活跃 Worker 存在时拒绝第二个 Worker。

必须记住：

> 源目录、进度或算子版本莫名错位时，先核对 Run、Worker PID、source revision 和租约，不要先改 Prompt。

### 2.4 Prompt 是生产版本事实，旧错误版本只能撤销执行资格

早期视觉 Prompt 没有完整绑定：

- Task objective；
- semantic requirements；
- exclusion requirements；
- classification contract。

这会导致模型收到泛化任务，Pipeline 表面有 VLM，实际上没有执行用户条件。

当前视觉 Prompt 至少要求这些任务变量：

```text
task_objective
semantic_requirements
exclusion_requirements
classification_contract
```

旧 Prompt 版本仍为审计保留，但不能继续用于生产执行：

```text
image-semantic-selection >= 2
image-task-visual-tagging >= 2
```

当前 Native VLM 默认使用：

```text
image-task-visual-tagging:3
```

不要原地修改已落入 PipelineVersion 的 Prompt。正确做法是新增版本、提高最低可执行版本，并让旧版本只读保留。

### 2.5 “Run 成功”不等于“语义任务成功”

曾经出现 23 张图片全部原样保留，Run 显示 `SUCCEEDED`，但 Pipeline 只有：

```text
builtin.decode_check:1
builtin.manifest:1
```

文件确实被处理和发布了，但用户要求的视觉筛选没有发生。

当前 QC 会检查：

- Required Capability 是否有对应节点；
- 保留资产是否包含要求的语义标签；
- Provider/VLM 原始证据是否存在；
- `visual_semantic_selection` 是否为 match、mismatch 或 uncertain；
- 是否存在执行失败；
- 语义证据缺失率和各选择结果占比。

只有真实语义证据存在且没有失败时，`semantic_quality_verified` 才能为真。

仍要注意：自动 QC 证明的是“契约完整并被执行”，不等于模型判断与人工 Golden Set 完全一致。

### 2.6 编译器绝不能在缺少分类契约时猜猫狗

v8 之后确认并删除了这个遗留硬编码：

```text
TaskSpec.classification 缺失
  -> 默认 cat / dog / mixed / unknown
```

现在：

- 纯语义筛选可以没有分类契约；
- `image_classification`、`class_resolution`、`dataset_partition` 需要分类契约时必须显式存在；
- 缺少契约就阻止编译，不得猜测标签；
- Prompt 中的分类上下文只能来自当前 TaskSpec。

这是“不要出现一个问题修一个词”的典型例子。问题不是猫狗默认值本身，而是编译器在缺少领域事实时擅自补业务语义。

### 2.7 Native Remote VLM 和 Data-Juicer Provider 是互补关系

Data-Juicer 继续作为外部 Provider，负责：

- 完整发现目录；
- 已准入的专用算子；
- Dataset 级批处理；
- 去重和其他已有 Operator 生态；
- 后续 CPU/GPU 专用模型接入。

Native Remote VLM 负责当前默认快速路径：

- 直接调用受治理视觉模型 API；
- 不启动 `dj-process` 子进程；
- 使用 DataAgent 自己的版本化 Prompt；
- 校验允许标签和必选标签组；
- 记录模型、request ID、usage、Prompt hash 和响应证据；
- 交给下游确定性策略节点决策。

正确结论不是“全面重写 Data-Juicer”，也不是“所有视觉任务都必须经过 Data-Juicer”，而是：

```text
通用、高频、低延迟 Remote VLM
  -> Native Operator

成熟 Provider 生态、批量语义、专用模型和长尾算子
  -> Data-Juicer Provider
```

### 2.8 `artifacts/` 是审计证据，不是重复输出目录

Native VLM 每张图片会生成一个紧凑 JSON 证据：

```text
datasets/<dataset_id>/artifacts/native-vlm/<node_id>/<asset_sha256>.json
```

内容包括：

- Run 和 WorkOrder；
- OperatorVersion；
- source path；
- Prompt SHA256；
- 受治理标签；
- confidence 和 reason；
- model；
- request ID；
- token usage。

图片交付文件位于：

```text
datasets/<dataset_id>/files/
```

`artifacts/` 不应删除，因为它支撑逐图回溯、成本统计、Prompt 复现和问题修复。它也不应被误写成用户筛选结果目录。

### 2.9 当前并行是 Asset 级并行，不是任意节点乱序执行

当前规则：

```text
多张图片之间
  可以并行

同一张图片内部
  Pipeline 节点仍按依赖顺序串行
```

只有整个 Pipeline 满足以下条件才并行：

- 每个节点都是 `ExecutionScope.ASSET`；
- 每个 Operator 都显式声明 `parallel_safe=True`；
- 不包含 Dataset batch Operator；
- 不包含需要共享可变状态的算子；
- Runtime Profile 允许相应并发。

以下情况自动回退串行：

- Dataset 级去重；
- Data-Juicer Dataset batch；
- 未声明线程安全的 Provider Proxy；
- 感知哈希等需要共享集合的算子；
- 任一 Dataset-scoped Operator。

实际并发数为：

```text
min(
  DATAAGENT_WORKER_CONCURRENCY,
  Operator RuntimeProfile.concurrency,
  待处理资产数
)
```

当前默认配置：

```text
DATAAGENT_WORKER_CONCURRENCY=4
native.remote_vlm:1 RuntimeProfile.concurrency=4
```

把环境变量设置为 16 不会绕过算子上限 4。

### 2.10 并发必须同时解决 Checkpoint、取消、失败隔离和数据库写入

本阶段没有只套一个 `ThreadPoolExecutor`，还补齐了：

- 每张图片独立 `OperatorContext`；
- 每张图片独立 node result 和 evidence；
- Checkpoint 按 asset sequence 关联，不按完成顺序猜位置；
- SQLite 使用 WAL、busy timeout 和进程内写锁；
- 单图失败只标记该资产，其他图片继续；
- Run 最终可进入 `PARTIAL`，失败资产可修复；
- 暂停/取消后停止派发新图片；
- 已在途图片允许安全收尾；
- 待执行任务只维持有界数量，不一次性把整个数据集塞进线程池队列；
- 记录 `asset_parallelism_selected` 或 `asset_parallelism_disabled` 事件。

不要用一个共享 `OperatorContext.shared` 跑多张图片。里面可能包含 active node、artifact root、去重集合、cancel callback 和 event sink。

### 2.11 单张延迟和批量吞吐必须分开描述

最新真实 Run：

```text
Run: run_5c03914dcf3e415c
Dataset: dataset_5c03914dcf3e415c
Source: D:\data\yifu
Assets: 10
Kept: 3
Rejected: 7
Failed: 0
Status: SUCCEEDED
Requested concurrency: 4
Effective concurrency: 4
Wall time: 约 64.1 秒
```

该 Run 的 VLM 节点：

```text
平均单次延迟: 22.225 秒
最快: 9.340 秒
最慢: 37.461 秒
10 次调用累计节点时间: 222.248 秒
批量折算吞吐: 约 6.4 秒/张
相对本次串行累计时间: 约 3.5 倍吞吐提升
```

所以不能写成“单张图片现在只要 6.4 秒”。准确说法是：

- 单张请求平均仍约 22.2 秒；
- 4 路并发后，10 张整批平均每 6.4 秒完成一张。

### 2.12 当前主要性能瓶颈是思考 Token，不是本地 Pipeline

最新 Run 的本地节点耗时：

```text
builtin.decode_check:1
  平均 13.2 ms

builtin.visual_semantic_selection:1
  平均 1.3 ms

builtin.manifest:1
  平均 0.3 ms
```

绝大部分时间来自 `native.remote_vlm:1`。

当前 Pipeline 参数：

```text
model: qwen3.7-plus
max_tokens: 1024
```

最新 10 张图片的 usage：

```text
平均 image tokens: 72
平均最终文本 tokens: 10.7
平均 reasoning tokens: 365.6
最大 reasoning tokens: 991
```

模型最终只需返回：

```json
{"tags":["semantic_match"]}
```

但混合思考模式为简单三分类消耗了大量推理 Token。阿里云官方文档说明 `qwen3.7-plus` 属于混合思考模型，可通过 `enable_thinking` 控制思考开关：

```text
https://help.aliyun.com/zh/model-studio/deep-thinking
```

下一步最有价值的提速不是直接把并发拉到 16，而是先对简单受约束视觉判断关闭思考，再测准确率、延迟和费用。

## 3. v8 之后已经完成了什么

### 3.1 TaskSpecPatch Schema 和标准化

提交：

```text
1e10ae3 fix: validate and normalize task spec patches before execution
```

完成：

- 新增结构化 `TaskSpecPatch`；
- `StartWorkOrderAction` 和 `EditTaskSpecAction` 不再接受任意字典；
- 规范 hard constraints、preferences、semantic/exclusion requirements；
- 合并字符串、列表和结构化 requirement；
- `mixed_label` / `unknown_label` 对象引用标准化为 ID；
- Tool Loop 和 Conversation 在执行前统一验证 Patch；
- 增加对话、LangGraph、Control Tool 和 Action 测试。

### 3.2 通用视觉语义筛选 Pipeline

提交：

```text
02b21af feat: compile generic visual selection pipelines
```

完成：

- 增加 `visual_semantic_selection` 能力；
- 增加 `builtin.visual_semantic_selection:1`；
- 编译任意视觉条件筛选；
- Remote VLM 返回 match/mismatch/uncertain；
- 三条策略通过 uncertain policy 体现差异；
- 纯语义筛选不再退化为 decode + manifest。

### 3.3 Worker 单实例租约和 claim 协议

提交：

```text
c0c2554 fix: prevent stale workers from claiming current runs
```

完成：

- 新增跨 Windows/Linux 的 `WorkerProcessLease`；
- 活跃 Worker 存在时拒绝第二个 Worker；
- 清理死亡 PID 的陈旧锁；
- Run claim 增加协议版本；
- 防止旧 Worker 使用旧代码抢新任务。

### 3.4 Prompt v2 绑定任务事实

提交：

```text
2883d8c fix: bind task criteria into visual selection prompts
```

完成：

- 新增 `image-semantic-selection.v2.yaml`；
- 新增 `image-task-visual-tagging.v2.yaml`；
- 注入 objective、语义条件、排除条件和分类契约；
- 明确只根据可见证据判断；
- 明确 semantic match/mismatch/uncertain 输出契约；
- 增加 PromptBinding 和编译测试。

### 3.5 QC 验证语义证据

提交：

```text
edb08ce fix: verify visual selection evidence during QC
```

完成：

- 检查要求的语义能力是否有输出；
- 检查 VLM 原始 evidence；
- 统计 match/mismatch/uncertain；
- 计算 semantic missing rate；
- 正确设置 `semantic_quality_verified`；
- 阻止“只复制文件也算语义成功”。

### 3.6 撤销错误 Prompt 的生产执行资格

提交：

```text
5d06d46 fix: revoke defective visual prompt versions
```

完成：

- 旧 Prompt 保留审计；
- Pipeline 生产执行前检查最低 Prompt 版本；
- 旧版本不能重新执行；
- 避免历史 Pipeline 静默使用已知错误 Prompt。

### 3.7 模型 JSON 修复和接地回退

提交：

```text
c06583d fix: recover malformed conversation model JSON
```

完成：

- 引入 `json-repair` 修复可恢复的模型 JSON；
- 修复后仍必须为非空对象；
- 模型失效时优先返回当前持久化 TaskSpec、Pipeline、Run 或 Dataset 事实；
- 无事实可答时才返回非变更型重试提示；
- 不再因为一次缺逗号就丢掉当前工单上下文。

### 3.8 删除猫狗分类默认值

提交：

```text
98b7828 fix: require explicit pipeline classification labels
```

完成：

- 缺分类契约时不再返回猫狗；
- 分类、类别解析、分目录需要显式 TaskSpec classification；
- 纯视觉筛选仍可合法运行；
- 编译器不再推测业务标签。

### 3.9 受治理 Native Remote VLM Operator

提交：

```text
3309685 feat: add governed native remote VLM operator
```

完成：

- 新增 `native.remote_vlm:1`；
- 通过百炼 OpenAI-compatible Vision API 直连 VLM；
- 默认成为通用 Remote Visual Understanding 快速路径；
- 不启动 Data-Juicer 子进程；
- 校验 `allowed_tags` 和 `required_tag_groups`；
- 输出受治理 `ImageTagSet`；
- 记录 model、request ID、usage 和 Prompt hash；
- 写入逐资产 JSON evidence；
- Prompt 升级到 `image-task-visual-tagging:3`；
- Catalog 排序优先可执行 Native Remote VLM；
- Data-Juicer Provider 仍保留为外部 Provider 和长尾能力来源。

真实小集合记录：

```text
run_02ada62b29b34817
native.remote_vlm:1
10 张
平均 VLM 节点耗时约 14.929 秒/张

run_bba62f85049347e3
datajuicer.image_tagging_vlm_mapper.remote_api:2
23 张
平均 VLM 节点耗时约 29.971 秒/张
```

这两个 Run 说明 Native 路径有明显延迟优势，但样本数和运行条件不同，只能作为方向性小集合证据，不能替代严格 A/B 基准。

### 3.10 Worker 并发契约

提交：

```text
4a14202 feat: define bounded asset concurrency contract
```

完成：

- 新增 `DATAAGENT_WORKER_CONCURRENCY`；
- 默认值 4，最小值 1；
- Worker 将并发配置传入 DatasetRunExecutor；
- Builtin Operator 显式声明是否支持并行；
- Data-Juicer Provider Proxy 默认不声明并行安全；
- Native Remote VLM Runtime Profile 上限为 4。

### 3.11 Asset 级并行执行

提交：

```text
1644c56 feat: execute parallel-safe assets concurrently
```

完成：

- 多资产使用受限线程池并行；
- 单资产节点顺序保持不变；
- 每个资产使用独立上下文；
- Checkpoint 支持乱序完成；
- RunStore 增加并发写保护；
- SQLite 使用 WAL 和 busy timeout；
- Dataset 发布仍按 sequence 稳定排序；
- 记录实际选择的并发数。

### 3.12 并发安全边界

提交：

```text
a4cede0 feat: guard bounded parallel asset execution
```

完成：

- 真正有界派发，不把全部资产一次性排队；
- 暂停和取消后停止发新资产；
- 不安全 Operator 自动回退串行；
- Dataset 和 batch Operator 自动回退串行；
- 记录回退原因和阻塞 Operator；
- 单资产失败不终止其他资产；
- 部分失败生成可修复 `PARTIAL`；
- 增加取消、失败隔离和串行回退测试。

### 3.13 Native VLM 并发回归

提交：

```text
76865ce test: verify governed VLM parallel execution
```

覆盖：

- Native VLM Prompt 参数；
- 图片读取；
- JSON 标签契约；
- 4 路真实 Operator 并行调用；
- 4 份独立 evidence；
- Dataset 发布；
- Runtime Profile 并发上限；
- Worker 请求 8、算子上限 2 时实际只执行 2 路。

## 4. 当前进行到哪里

### 4.1 当前实现状态

| 能力 | 状态 |
|---|---|
| TaskSpec / Pipeline / Worker / Dataset / QC / Repair / Export 主闭环 | 已完成 |
| TaskSpecPatch 结构化校验和标准化 | 已完成 |
| 通用视觉条件筛选 | 已完成 |
| 分类标签必须来自 TaskSpec | 已完成 |
| Prompt 绑定任务目标、条件和分类上下文 | 已完成 |
| 错误 Prompt 版本阻止生产执行 | 已完成 |
| QC 检查语义 evidence | 已完成 |
| Worker 单实例租约 | 已完成 |
| Native Remote VLM Operator | 已完成并真实运行 |
| Data-Juicer Provider 保留 | 已完成 |
| Asset 级有界并行 | 已完成并真实运行 |
| 不安全/Dataset/Batch Operator 串行回退 | 已完成 |
| 单资产失败隔离 | 已完成 |
| 并发暂停/取消停止派发 | 已完成 |
| Native VLM 4 路并发真实任务 | 已通过 |
| Native VLM 关闭思考模式 | 未实现 |
| Worker 级 HTTP 连接池 | 未实现 |
| VLM 结果缓存 | 未实现 |
| 自适应并发和 429 指标 | 未实现 |
| 角色级/任务级 Golden Set | 尚未完整执行 |
| 60-case Acceptance Campaign | 未执行 |
| 干净 Windows/Linux 安装启动矩阵 | 未执行 |

最准确的当前结论：

> DataAgent 已经能够把任意简单视觉筛选条件编译成真实 Native VLM Pipeline，并以受治理的 4 路 Asset 并发执行、逐图记录证据并发布 Dataset；当前主要瓶颈已从“任务不能正确编译和执行”转向“模型思考 Token、连接复用、缓存、限流治理和正式质量验收”。

### 4.2 当前真实 Run 基线

最新真实任务：

```text
目标：筛选穿黑色衣服的图片
源目录：D:\data\yifu
Run：run_5c03914dcf3e415c
Dataset：dataset_5c03914dcf3e415c
Pipeline：pipeline_version_0d7fd64a17424b5e
Prompt：image-task-visual-tagging:3
模型：qwen3.7-plus
图片：10
保留：3
拒绝：7
失败：0
QC：PASSED
并发：4
总耗时：约 64.1 秒
```

输出：

```text
D:\newDataAgent\.dataagent\platform\datasets\dataset_5c03914dcf3e415c
```

其中：

```text
files/
  3 张保留图片

artifacts/native-vlm/visual_tagging/
  10 份逐图 VLM evidence

manifest.json
  DatasetVersion 交付与审计清单
```

### 4.3 当前自动测试

执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -o addopts="" -q
```

结果：

```text
234 passed, 1 warning in 24.19s
```

唯一警告仍是 Starlette `TestClient` 对当前 `httpx` 适配方式的弃用提示，不是业务失败。

### 4.4 当前 Git 和工作区

```text
branch: codex/hybrid-operator-retrieval
HEAD: 76865ce
ahead of origin/codex/hybrid-operator-retrieval: 48 commits
```

当前存在未跟踪内容：

```text
CONTEXT.md
DataAgent-development-handoff-2026-07-20-v4.md
DataAgent-development-handoff-2026-07-20-v5.md
DataAgent-development-handoff-2026-07-22-v6.md
DataAgent-development-handoff-2026-07-23-v7.md
DataAgent-development-handoff-2026-07-23-v8.md
DataAgent-development-handoff-2026-07-24-v9.md
docs/
scripts/e2e_real_gateway.py
```

这些文档和脚本没有混入功能提交。不要无差别执行 `git add .`。

## 5. 下一步计划

### P0：关闭简单视觉判断的思考模式

先做受治理配置，不要直接在 Gateway 全局硬编码：

1. 在 Native VLM Operator 参数或 Model Routing Policy 中增加明确的 reasoning mode；
2. 简单闭集标签、真实性枚举、通用语义筛选默认候选为 `enable_thinking=false`；
3. 复杂开放视觉推理仍允许开启思考；
4. 将开关写入 Prompt/Run evidence；
5. 使用同一 Golden Set 做 thinking on/off A/B；
6. 比较准确率、uncertain 率、P50/P95、reasoning tokens 和费用；
7. 只有质量不下降时才提升为正式默认。

不要只根据“最终 JSON 很短”就跳过 Golden Set。某些细粒度真实性判断可能从思考中获益。

### P0：关闭思考后收紧输出 Token

当前 `max_tokens=1024`。关闭思考并验证契约稳定后：

```text
候选 max_tokens: 64 或 128
```

不要在思考模式开启时直接降到 64。reasoning token 也占输出预算，可能在 JSON 出现前截断。

### P0：复用 HTTP 连接

当前 `call_vision_model_json()` 每次请求创建一个新的 `httpx.Client`。下一步应：

- Worker 生命周期内复用连接池；
- 配置连接数和 keep-alive；
- 明确线程安全和关闭时机；
- 分别记录 connect、TTFT、response 总耗时；
- 不把 API key 写入 Trace；
- 对 429、5xx 和网络错误保留有限重试。

### P0：并发从 4 向 6/8 做阶梯测试

不要直接把并发定得很大。使用至少 50 张固定图片，依次测试：

```text
1
2
4
6
8
```

记录：

- 整批 wall time；
- 单图 P50/P95/P99；
- 吞吐；
- 429 次数；
- 5xx 和网络错误；
- 重试次数；
- failed/partial 比率；
- 总 token 和费用；
- 语义准确率。

只有吞吐继续增长且错误率、P95 和准确率稳定，才提高 `RuntimeProfile.concurrency`。

### P0：建立 VLM 结果缓存

建议缓存键：

```text
input_sha256
+ OperatorVersion
+ PromptBinding template/version/hash
+ normalized parameters
+ model ID/revision
+ reasoning mode
```

缓存要求：

- 只复用完全相同的受治理请求；
- evidence 指向原调用和缓存命中关系；
- Prompt 或模型版本变化自动失效；
- Repair Run 可选择绕过缓存；
- 不缓存失败、截断或契约无效结果。

### P0：完成视觉 Golden Set 和 Acceptance

至少覆盖：

- 明确 match；
- 明确 mismatch；
- 光照导致黑色/深蓝混淆；
- 局部黑衣；
- 多人不同颜色；
- 黑色配饰但非黑衣；
- 遮挡；
- 插画、截图、合成；
- 无人物；
- uncertain。

然后继续完整 60-case：

- Run；
- QC；
- failed asset repair；
- 三次失败 abandoned；
- 用户 exclude；
- 重算 QC；
- 最终 export；
- 文件数、目录、Manifest、evidence 和 SHA256 核对。

### P1：完善并发调度

当前线程池适合 Remote I/O。后续按算子类型区分：

- Remote I/O：线程池/异步连接池；
- CPU 重计算：进程池或原生释放 GIL 的库；
- GPU：独立 GPU Worker 和显存调度；
- Dataset batch：保持 Dataset 级执行；
- DAG 独立分支：未来可基于依赖图并行，但不能破坏节点输入状态。

不要因为一个 Operator 标记 `parallel_safe=True` 就假设所有 Provider 实例、SDK Client 和模型缓存都线程安全。

### P1：审计查询和性能面板

增加：

- Run events 分页；
- node results 按 source/node/status/decision 过滤；
- 每资产完整节点轨迹；
- Prompt、模型、usage 和 request ID 查询；
- 并发选择和串行回退原因；
- P50/P95/P99 和 token 汇总；
- 429/重试/缓存命中率；
- JSONL/CSV 审计导出。

### P1/P2：继续可移植安装和 Data-Juicer 准入

延续 v7/v8 计划：

- 干净 Windows；
- Linux CPU；
- Linux GPU Worker；
- `catalog/cpu/remote/linux-gpu` Profile；
- 217 discoverable 与 admitted/released 分离；
- CPU 算子 Golden Set；
- GPU 模型许可证、revision、SHA256 和独立评测；
- Provider Registry 修复和离线阻断验证。

## 6. 必须记住的经验，不要重复踩坑

### 6.1 不要看到 `SUCCEEDED` 就认定用户目标完成

同时检查：

```text
TaskSpec required capabilities
Pipeline real operators
PromptBinding
run_node_results
semantic evidence
QC semantic_quality_verified
实际 files/
```

### 6.2 不要让编译器补业务语义

缺猫狗标签、输出类别或筛选条件时，应澄清或阻止编译，不能偷偷提供默认猫狗。

### 6.3 不要用关键词补丁修模型结构错误

`mixed_label` 对象/字符串、requirement 字典/列表等差异，应在 Domain Schema 边界统一标准化。

### 6.4 不要原地覆盖 Prompt

Prompt 是版本事实。错误版本保留审计但撤销执行资格，新行为新增版本。

### 6.5 不要先改 Prompt，再检查旧 Worker

出现错误数据集、错误进度或旧 Operator 时，先检查 Worker lease、PID、source revision、Run ID 和 source plan。

### 6.6 不要为每个视觉条件新增 Operator

通用 Remote VLM + 版本化 Prompt + 受约束标签 + 确定性策略，能够覆盖大量视觉筛选任务。

### 6.7 不要把 Native 和 Data-Juicer 做成二选一

Native 是高频快速路径；Data-Juicer 是外部 Provider 生态和批量/专用能力来源。

### 6.8 不要把 `artifacts/` 当作重复数据集

`files/` 是交付图片，`artifacts/` 是模型证据。两者职责不同。

### 6.9 不要把批量吞吐说成单图延迟

本次 4 路并发是约 6.4 秒/张吞吐，但单张 VLM 平均仍约 22.2 秒。

### 6.10 不要盲目增大并发

并发受 Worker、Operator Profile、API 配额、连接池、RPM/TPM、429 和尾延迟共同限制。

### 6.11 不要在思考开启时盲目降低 `max_tokens`

当前 reasoning token 最高达到 991。直接把 1024 降到 64 可能只截断推理，不返回 JSON。

### 6.12 不要共享跨资产可变上下文

每张图片必须有自己的 Context、active node、event sink 和 checkpoint。共享去重集合的算子不能走当前 Asset 并行。

### 6.13 不要用完成顺序代替资产顺序

并发完成顺序不稳定。恢复和发布必须以 frozen source plan 的 sequence 为准。

### 6.14 不要在取消后继续填满任务队列

只维持有界在途任务。取消或暂停后停止派发，新资产不能继续启动。

### 6.15 不要让一张失败拖垮整批

单资产 Operator 错误应记录 node result、reason 和 retryable 状态，其余资产继续；最终用 `PARTIAL` 和 Repair Run 表达。

### 6.16 不要把自动测试写成真实质量结论

当前证据等级要明确区分：

```text
234 条自动测试通过
Native VLM 真实 10 图 Run 通过
4 路真实任务并发通过
完整 Golden Set 尚未完成
60-case Acceptance 尚未完成
```

### 6.17 不要无差别提交工作区

当前多个接手文档、`docs/`、`CONTEXT.md` 和真实网关脚本仍未跟踪。提交前明确逐文件暂存。

## 7. 关键代码位置

```text
dataagent/domain/specs/models.py
  TaskSpec、ClassificationSpec、TaskSpecPatch 和标准化

dataagent/application/conversation_actions.py
  ConversationAction JSON 契约

dataagent/application/conversation.py
  模型优先决策、接地回退、bounded ReAct

dataagent/gateway.py
  JSON 修复、模型路由、Native Vision API 调用

dataagent/agents/processing/nodes.py
  通用视觉能力编译、PromptBinding、分类契约门禁

dataagent/operators/builtin/semantic.py
  VisualSemanticSelectionOperator 和下游确定性策略

dataagent/operators/builtin/vlm.py
  NativeRemoteVlmOperator、标签契约和逐资产 evidence

dataagent/resources/prompts/image-semantic-selection.v2.yaml
dataagent/resources/prompts/image-task-visual-tagging.v2.yaml
dataagent/resources/prompts/image-task-visual-tagging.v3.yaml
  当前视觉 Prompt 版本

dataagent/prompts.py
  Prompt registry 和最低可执行版本

dataagent/evaluation/quality.py
  语义 evidence 和 QC

dataagent/worker_lease.py
  Worker 单实例租约

apps/worker/runner.py
dataagent/application/run_worker.py
  Worker 启动、租约和配置注入

dataagent/config.py
  DATAAGENT_WORKER_CONCURRENCY

dataagent/execution/dataset_runner.py
  Asset 并行、安全回退、Checkpoint、取消和发布

dataagent/infrastructure/database.py
  RunStore、SQLite WAL、写锁、事件和 node results

tests/integration/test_parallel_dataset_execution.py
  并发、失败隔离、取消、Profile 上限和 Native VLM 完整链路

tests/unit/test_native_remote_vlm.py
  Native VLM 契约和 evidence

tests/unit/test_catalog_normalization.py
  Catalog、通用视觉能力和 Pipeline 编译

tests/unit/test_quality_evaluator.py
  语义 QC
```

## 8. 接手后的检查步骤

### 8.1 Git 和回归

```powershell
cd D:\newDataAgent
git status --short --branch
git log --oneline --decorate -30
.\.venv\Scripts\python.exe -m pytest -o addopts="" -q
git diff --check
```

预期：

```text
234 passed, 1 warning
```

### 8.2 启动

先停止旧窗口中的 API、Worker 和 TUI，再运行：

```powershell
.\.venv\Scripts\dataagent.exe start --owner local-user
```

检查：

```text
.dataagent/platform/worker.lock.json
.dataagent/launcher/<instance_id>/
API health 中的 instance/revision/version
```

### 8.3 并发验证

提交 Run 后在事件中检查：

```text
asset_parallelism_selected
requested_concurrency
effective_concurrency
asset_count
```

如果回退串行，检查：

```text
asset_parallelism_disabled
reason
operator_version_id
```

### 8.4 输出验证

对每个真实 Run 核对：

```text
run total == frozen source plan count
run progress == completed asset count
每张图每个节点有 node result
每次 Native VLM 有 request ID 和 usage
kept 图片存在于 files/
VLM evidence 存在于 artifacts/
manifest 路径有效
QC 与 DatasetVersion 一致
```

## 9. v8 之后提交索引

```text
1e10ae3 fix: validate and normalize task spec patches before execution
02b21af feat: compile generic visual selection pipelines
c0c2554 fix: prevent stale workers from claiming current runs
2883d8c fix: bind task criteria into visual selection prompts
edb08ce fix: verify visual selection evidence during QC
5d06d46 fix: revoke defective visual prompt versions
c06583d fix: recover malformed conversation model JSON
98b7828 fix: require explicit pipeline classification labels
3309685 feat: add governed native remote VLM operator
4a14202 feat: define bounded asset concurrency contract
1644c56 feat: execute parallel-safe assets concurrently
a4cede0 feat: guard bounded parallel asset execution
76865ce test: verify governed VLM parallel execution
```

## 10. 一句话接手结论

DataAgent 已从 v8 的“共享 Provider VLM、受治理 Tool Loop 和实时 TUI”推进到“任意视觉条件可正确编译、旧 Worker 不再抢错任务、Prompt 和 QC 能证明语义执行、Native Remote VLM 作为默认快速路径、4 路 Asset 并发真实运行”；下一阶段不要继续堆任务专用算子或盲目放大并发，应先关闭简单视觉判断的过度思考、收紧 token、复用 HTTP 连接、增加缓存和限流指标，再用 Golden Set 与完整 Acceptance 证明速度、成本和质量同时达标。

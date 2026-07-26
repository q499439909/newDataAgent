# DataAgent 开发接手文档 v8

> 交接日期：2026-07-23  
> 仓库：`D:\newDataAgent`  
> 当前分支：`codex/hybrid-operator-retrieval`  
> 当前 HEAD：`c5d67d4 feat: stream agent action traces to the TUI`  
> 上一版：`DataAgent-development-handoff-2026-07-23-v7.md`  
> 本文范围：v7 之后的共享 VLM 调用、一键启动、模型生成澄清、紧凑 Manifest、受治理 Agent Tool Loop 和 TUI 实时行动流。  
> 当前自动回归：`200 passed, 1 warning`  
> Git 状态：当前分支比远端同名分支领先 35 个提交；v7 记录的 HEAD 到当前 HEAD 新增 6 个提交。

## 1. 我们在做什么

DataAgent 是一个面向图片数据生产的 Agent 控制平面。用户用自然语言描述图片目录、筛选条件、分类目标和交付要求，系统把需求固化为版本化 `TaskSpec`，拆解能力、检索真实 Operator、编译三条可比较 Pipeline，经用户批准后由独立 Worker 执行，最终形成可审计、可修复、可导出的 DatasetVersion。

v7 已经完成 P0 主链路、真实 Remote VLM 单图和两图小集合冒烟。v8 阶段没有继续盲目扩算子，而是集中解决实际交互中反复出现的六类系统性问题：

1. 同一张图片为了真实性和分类连续调用两次相同 VLM，延迟和费用不合理；
2. 本地启动仍要求分别管理 API、Worker、TUI，容易连到旧进程；
3. 需求澄清仍残留猫狗任务固定问法和“好/继续/确认”词表；
4. Dataset Manifest 把 Provider 调试文件逐图片重复记录，交付清单过长；
5. Tool 层虽然已经存在，但真实对话没有经过 ToolRegistry；
6. 用户等待模型和规划时只能看到“正在思考”，无法看到受治理的行动进展。

当前主链路进一步收紧为：

```text
用户自然语言
  -> Requirement Analyzer 生成结构化 Intent / Action
  -> propose_control_action 进行 Schema + ActionPolicy 门禁
  -> TaskSpec 结构化缺口检测
  -> 模型按缺口生成自然语言澄清问题
  -> TaskSpec 确认
  -> Operator Retriever
  -> Pipeline Compiler
  -> Pipeline Validator
  -> TUI 实时展示脱敏行动摘要
  -> 用户批准真实 PipelineVersion
  -> Worker 执行 Operator
  -> Run events / node results / Provider evidence
  -> 紧凑交付 Manifest
  -> QC / Repair / Exclude / Export
```

控制面和执行面仍然分离。一键启动只是统一管理三个进程，不是把 API、Worker 和 TUI 合并成一个职责混乱的进程。

## 2. v7 之后讨论并确认的设计结论

### 2.1 可合并的 VLM 判断应一次完成，但不能新增无限复合算子

用户明确要求：

- `image_tagging_vlm_mapper` 仍是原子视觉标注算子；
- 不为“真实性 + 是否实拍 + 猫狗分类”创建新的任务专用 Operator；
- 同一张图、同一阶段、同一模型能够一次回答的判断，应由一次 VLM 调用返回完整视觉证据；
- 下游确定性策略节点再分别消费这些标签。

当前实现新增共享 Prompt：

```text
dataagent/resources/prompts/image-task-visual-tagging.v1.yaml
```

一次调用返回三组标签：

```text
authenticity:
  authentic | synthetic | uncertain

capture:
  direct_photo | edited_photo | screenshot |
  illustration | composite | uncertain_capture

task class:
  从 TaskSpec 注入的 allowed_labels 中选择
```

Pipeline 中只生成一个 `visual_tagging` VLM 节点，输出统一写入 `visual_tags`。真实性解析、类别解析和保留/拒绝策略仍由下游语义算子确定性执行。

以下情况不能强行合并：

- 两次 VLM 之间存在裁剪、分割、增强或其他图片变换；
- 两个判断依赖不同输入；
- 必须使用不同模型、Provider Variant 或安全策略；
- 输出契约无法稳定合并；
- 其中一个能力需要独立缓存、独立复核或独立许可证证据。

因此正确原则是：

> 合并兼容的模型推理调用，不合并 Operator 职责，不为每种任务组合复制一个新算子。

### 2.2 一键启动不等于取消 API、Worker、TUI 分层

此前用户需要分别启动 API、Worker 和 TUI，容易出现：

- 只重启 TUI，实际仍连接旧 API；
- API 已更新，但 Worker 仍运行旧代码；
- 端口 8000 被旧实例占用，却误以为当前源码已生效。

现在统一入口为：

```powershell
cd D:\newDataAgent
.\.venv\Scripts\dataagent.exe start --owner local-user
```

该命令会：

1. 创建本次本地运行的 `instance_id`；
2. 记录当前 Git source revision 和包版本；
3. 启动 API；
4. 通过 `/health` 校验 instance、revision 和 version；
5. 启动 Worker；
6. 最后启动交互式 TUI；
7. TUI 退出或收到中断时清理子进程；
8. API/Worker 日志保存到 `.dataagent/launcher/<instance_id>/`。

如果端口 8000 已由旧 DataAgent 实例占用，启动器会拒绝连接并要求先停止旧 API 和 Worker。不要把这个保护改成“自动连接任何健康 API”，否则旧进程问题会重新出现。

### 2.3 代码可以确定缺哪些字段，但不能写死用户问题

澄清阶段必须区分：

```text
结构化缺口检测
  系统负责，必须确定性

自然语言提问
  模型负责，结合完整 TaskSpec 动态生成
```

当前结构化缺口包括：

```text
hard_constraints.authenticity_scope
preferences.mixed_policy
preferences.unknown_policy
hard_constraints.preserve_source
preferences.output_layout
```

代码不再保存“是否排除插画、截图、明显合成……”这类任务固定问题。`ModelGateway.task_clarifications()` 根据当前 TaskSpec 和缺口字段生成问题，并必须做到：

- 每个缺口只问一次；
- 已回答字段不能重复询问；
- 不得新增用户没有提出的要求；
- 返回的问题字段必须与 `ambiguities` 精确一致。

当模型不可用时，系统只回退到“请补充字段 `<field>`”这种通用提示，不回退到猫狗任务模板。

同时删除了规格修订层中的固定确认词过滤：

```text
好
好的
可以
继续
确认
同意
是
yes
ok
```

确认、继续、采用默认值等语义由模型输出的结构化 Action 决定。TaskSpec 修订层只处理已经通过契约校验的结构化补丁，不再猜一段自然语言是不是口头确认。

### 2.4 交付 Manifest 和执行审计必须分层

此前 Data-Juicer 的：

```text
output.jsonl
output_stats.jsonl
```

会作为 artifact 沿 Pipeline 传播，并在每张图片的 DatasetAsset 中重复出现，造成 Manifest 很长且看起来记录了两遍甚至更多遍。

现在：

- 交付 Manifest 只保留真正属于 DatasetAsset 的交付产物；
- Data-Juicer 的进程输出文件从逐图片 Manifest 中移除；
- Provider 执行文件去重后集中写入 Run 事件：

```text
provider_execution_evidence_published
```

- Manifest 中为空的可选字段不再输出；
- 紧凑 Manifest 仍可完整反序列化为同一个 `DatasetVersion`；
- API/VersionStore 中的规范 Domain Model 不因紧凑落盘格式而丢失默认值。

不要为了缩短 Manifest 删除 SHA256、来源、决策、原因、血缘或审计引用。优化目标是消除重复和空字段，不是牺牲可追溯性。

### 2.5 真实对话必须经过 ToolRegistry，但 Tool 仍不能直接执行 Operator

v7 只有受治理 Tool 骨架。v8 已把真实 ConversationService 接入 `GovernedToolLoop`。

当前行动链：

```text
Requirement Analyzer
  -> propose_control_action
  -> ActionPolicy
  -> AgentRuntime / LangGraph
  -> retrieve_operators
  -> compile_pipeline_artifact
  -> validate_pipeline_artifact
```

事实查询使用：

```text
query_control_facts
```

每次调用记录：

```text
id
stage
stage_label
kind
tool
display_name
status
parameters
duration_ms
summary
evidence_ids
error_type
```

安全规则：

- `api_key`、token、password、secret、credential 自动替换为 `***`；
- 超长内容不进入 Trace，只保存字符数和 SHA256；
- Pipeline 参数只显示 ID、版本、策略和节点数；
- 不保存或展示模型隐式思维链；
- 展示的是可审计行动摘要，不是模型内部 reasoning；
- 最近 25 轮 Action Trace 保存在 Conversation context；
- Action Trace 不重新注入模型控制上下文，避免上下文膨胀。

ToolRegistry 中仍然禁止：

```text
execute_operator
execute_provider_operator
run_shell
run_python
write_arbitrary_yaml
delete_dataset
```

### 2.6 当前 Tool Loop 不是供应商原生 function calling

必须准确描述当前能力：

- 模型仍通过 `conversation_action_json_schema()` 返回结构化 Intent / Action；
- 应用层根据已验证 Action 调用窄 Tool；
- ToolRegistry 已进入真实执行和审计路径；
- 失败仍可进入现有 bounded ReAct 修复循环；
- 但模型网关尚未把 ToolSpec 作为供应商 API 的原生 `tools=` / `tool_calls` 传入。

因此当前是：

> 模型提出结构化动作，应用层执行受治理 Tool Loop。

还不是：

> 模型在一次供应商原生 ReAct 会话中自由选择 ToolSpec。

后续如升级原生 function calling，仍必须保留 ToolRegistry、ActionPolicy、confirmation 和证据渲染，不能让原生 tool call 绕过现有治理。

### 2.7 行动摘要必须实时出现，而不是请求结束后一次性展示

仅在最终响应中附带 `action_trace` 虽然可审计，但用户等待模型时仍感觉“卡住”。当前新增流式端点：

```text
POST /api/conversations/{conversation_id}/messages/stream
Content-Type: application/x-ndjson
```

事件顺序：

```text
{"type":"action", ...}
{"type":"action", ...}
...
{"type":"final", "response": ...}
```

TUI 会在每个步骤完成时立即展示：

```text
行动摘要
✓ 正在理解需求
  调用  Requirement Analyzer
  参数  已脱敏参数
  结果  结构化 Intent 摘要
  耗时  ...

✓ 正在检索算子
  调用  Operator Retriever
  结果  Found N candidate operators
  证据  operator version IDs
```

非流式 `/messages` 接口仍保留，旧客户端可继续使用。流式内容不展示隐式思维链。

当前“实时”的准确边界也要写清楚：

- TUI 发出请求后先显示“DataAgent 正在处理”；
- Requirement Analyzer 完成后立即发送模型行动摘要；
- Control Action Guard 完成后立即发送门禁摘要；
- 当前 Retriever、Compiler、Validator 由 `_post_action_tools()` 顺序执行，三个 Trace 会在该规划辅助函数返回后、最终 `final` 之前发出；
- 这已经不是“整轮结束后一次性展示”，但还不是 token 级流，也不是每个规划 Tool 完成后的独立 callback；
- 如果后续 Tool 本身变慢，应把 `action_sink` 下沉进 `_post_action_tools()`，逐 Tool 推送，而不是让 TUI 猜测进度。

## 3. v7 之后已经完成了什么

### 3.1 共享 VLM 视觉证据

提交：

```text
02f20fd feat: share VLM tagging across visual capabilities
```

完成内容：

- 新增 `image-task-visual-tagging.v1.yaml`；
- 真实性、实拍属性和闭集分类共用一次 Remote VLM 调用；
- Pipeline 只保留一个 `visual_tagging` 节点；
- 下游语义 Operator 从统一 `visual_tags` 读取证据；
- 没有新增猫狗专用或任务组合专用 Operator；
- 增加 Prompt 和 Pipeline 编译测试。

注意：代码和自动测试已经证明只编译一个共享节点，但尚未用完整 37/60 张真实集合重新测量调用数、总耗时、费用和准确率。

### 3.2 本地 Stack 一键启动

提交：

```text
2a06e86 feat: launch the local agent stack with one command
```

新增：

```text
dataagent/local_stack.py
dataagent start
```

完成内容：

- 一个命令管理 API、Worker、TUI；
- 子进程仍使用独立模块入口；
- 增加 instance/source revision/version 健康校验；
- 检测并拒绝旧 API；
- 保存 API/Worker 日志；
- TUI 退出后回收子进程；
- README 已补充使用方法。

### 3.3 模型生成 TaskSpec 澄清

提交：

```text
eef53f4 feat: generate task clarifications from structured gaps
```

完成内容：

- `ambiguities` 改为稳定字段路径；
- 推荐默认值改为任务无关的结构化 Patch；
- 澄清问题由模型结合完整 TaskSpec 生成；
- 严格校验问题字段覆盖；
- 删除任务固定提问模板；
- 删除确认词/filler 词表；
- 明确“排除闭集标签之外内容”时写入 `unknown_policy=reject`；
- 补充对话流、LangGraph 和单元测试。

### 3.4 紧凑 Manifest 和 Provider Evidence

提交：

```text
7f07609 feat: separate delivery manifests from provider evidence
```

完成内容：

- 空列表、空字典和空可选字段不再写入 Manifest；
- Provider `output.jsonl` / `output_stats.jsonl` 不再逐资产重复；
- 执行证据去重后写入 Run event；
- 紧凑 Manifest 保持 Domain Model round-trip；
- Dataset API 和版本事实保持兼容。

### 3.5 受治理 Agent Tool Loop 与行动审计

提交：

```text
62465ba feat: expose governed agent tool action traces
```

新增：

```text
dataagent/tools/loop.py
```

完成内容：

- ConversationService 的非 Chat 动作先经过 `propose_control_action`；
- TaskSpec 确认后实际调用 Retriever、Compiler、Validator；
- Control Fact 查询实际经过 ToolRegistry；
- Action Trace 统一脱敏、计时和记录 evidence IDs；
- 最近 25 轮 Trace 可从 Conversation 读取；
- TUI 展示行动摘要；
- 不展示 Chain of Thought。

### 3.6 TUI 实时行动流

提交：

```text
c5d67d4 feat: stream agent action traces to the TUI
```

完成内容：

- 新增 NDJSON 流式对话 API；
- ConversationService 支持 `action_sink`；
- TUI Client 解析 action/final 事件；
- Session 保持流式和非流式兼容；
- TUI 每收到一个 action 就立即展示；
- 最终响应不重复渲染已经流过的 action；
- 增加 API、Client、Session、TUI 顺序测试。

## 4. 当前进行到哪里

### 4.1 当前实现状态

| 能力 | 状态 |
|---|---|
| P0 TaskSpec / Pipeline / Worker / Dataset / Repair / Export 闭环 | 已完成 |
| Data-Juicer Remote VLM 单图真实冒烟 | 已通过 |
| 两图 Worker + QC + Export 真实冒烟 | 已通过 |
| 同一图片共享一次 VLM 视觉标注 | 代码与自动测试完成 |
| API/Worker/TUI 一键启动 | 已完成 |
| 模型动态生成澄清问题 | 已完成 |
| 固定确认词和任务问题模板移除 | 已完成 |
| 紧凑 Manifest | 已完成 |
| Provider 调试证据迁移到 Run event | 已完成 |
| 真实 Conversation Tool Loop | 已接入 |
| TUI 实时行动摘要 | 已完成 |
| 供应商原生 function/tool calling | 未实现 |
| 共享 VLM 的 37/60-case 真实性能与准确率复测 | 未执行 |
| 60-case 完整付费 Acceptance Campaign | 未执行 |
| 60-case Repair/Abandon/Exclude/Export 全链真实验收 | 未执行 |
| 干净 Windows/Linux 一键安装与启动矩阵 | 未执行 |

最准确的当前结论：

> DataAgent 的本地产品交互、Tool 治理和交付清单又向前收紧了一步；自动回归和最小真实 Provider 冒烟已通过，但共享 VLM 优化后的完整真实验收、完整 Acceptance Campaign 和干净机器验证仍未完成。

### 4.2 当前自动测试

最终执行：

```text
200 passed, 1 warning
```

唯一警告仍来自 Starlette `TestClient` 对当前 `httpx` 适配方式的弃用提示，不是业务测试失败。

v7 之后新增覆盖：

- 共享 VLM Prompt 和单节点 Pipeline 编译；
- 本地 Stack 子进程命令、健康身份和旧实例拒绝；
- 结构化缺口与任务无关默认值；
- 模型生成澄清问题；
- 不再使用确认词词表；
- Manifest 紧凑序列化和反序列化；
- Provider 执行证据与交付 Artifact 分离；
- GovernedToolLoop 输入脱敏和 evidence；
- Conversation Action Trace 持久化；
- Retriever / Compiler / Validator 实际调用；
- TUI 行动摘要；
- NDJSON action/final 流；
- TUI 流式顺序和非流式兼容。

### 4.3 当前 Git 和工作区

```text
branch: codex/hybrid-operator-retrieval
HEAD: c5d67d4
ahead of origin/codex/hybrid-operator-retrieval: 35 commits
```

当前仍存在未跟踪文件：

```text
CONTEXT.md
DataAgent-development-handoff-2026-07-20-v4.md
DataAgent-development-handoff-2026-07-20-v5.md
DataAgent-development-handoff-2026-07-22-v6.md
DataAgent-development-handoff-2026-07-23-v7.md
DataAgent-development-handoff-2026-07-23-v8.md
docs/
scripts/e2e_real_gateway.py
```

这些文件没有被六个功能提交顺带纳入 Git。下一位开发者不要执行无差别 `git add .`，应先确认文档和脚本是否准备正式入库。

## 5. 下一步计划

### P0 收口优先级 1：用当前 HEAD 做真实交互验收

1. 停止旧 API、Worker 和 TUI；
2. 使用新命令启动：

```powershell
.\.venv\Scripts\dataagent.exe start --owner local-user
```

3. 确认 TUI 能实时显示 Requirement Analyzer 和 Tool 行动；
4. 创建真实猫狗任务；
5. 确认澄清问题与本轮 TaskSpec 对应，不重复询问已回答字段；
6. 确认三条 Pipeline 展示真实 Operator、参数、PromptBinding 和策略差异；
7. 确认 Pipeline 中只有一个共享 `visual_tagging` Remote VLM 节点；
8. 启动小集合 Run；
9. 核对每张图片只发生一次 Remote VLM 调用；
10. 核对真实性、capture 属性和分类标签都能被下游节点正确消费。

### P0 收口优先级 2：共享 VLM 高风险子集

不要立刻跑完整 60 张。先选 5 至 10 个高风险样本：

```text
cat
dog
mixed
neither
synthetic
screenshot
illustration
composite
edited photo
uncertain
```

记录：

- 调用次数；
- 单图和整批耗时；
- Provider 输出；
- Output Adapter 结果；
- 每节点决策；
- 最终分类；
- 真实性和 capture 标签；
- PromptBinding ID、版本和 hash；
- 错误、跳过和重试；
- 费用估算。

共享调用只有在准确率不下降、契约稳定且下游策略可解释时才能进入完整 Campaign。

### P0 收口优先级 3：完整 Acceptance Campaign

延续 v7 计划：

1. 为 60-case 设置调用、费用和耗时预算；
2. 执行完整 Remote/Native Pipeline；
3. 验证真实 Repair Run 只处理失败资产；
4. 验证三次失败进入 abandoned；
5. 验证第四次自动修复被阻断；
6. 用户显式 exclude；
7. 重新 QC；
8. 从最终 SUCCEEDED DatasetVersion 导出；
9. 核对文件数、目录、Manifest、excluded report 和 SHA256；
10. 归档脱敏验收摘要和 checksums。

### P1：Provider 韧性、缓存和成本

1. 区分连接、读取、单资产和 Dataset 超时；
2. 只对 429、5xx 和瞬时网络问题有限重试；
3. 输出契约错误、参数错误和内容拒绝禁止盲目重试；
4. 增加小规模受控并发；
5. 建立缓存键：

```text
input_sha256
+ OperatorVersion
+ PromptBinding
+ parameters
+ model revision
```

6. 建立共享 VLM Golden Set、延迟和费用基线；
7. 增加 Run 级调用数和费用上限。

### P1：Tool Calling 下一阶段

当前不急于扩充更多 Tool。优先评估：

1. Gateway 是否支持供应商原生 `tools=` / `tool_calls`；
2. 从 `ToolSpec.input_model.model_json_schema()` 生成原生 Tool Schema；
3. 原生 Tool Result 仍转换为当前 `ToolResult`；
4. 原生调用仍经过 ActionPolicy 和 confirmation；
5. Tool failure 作为结构化 observation 回到 bounded ReAct；
6. 禁止开放 Shell、Python、任意文件和直接 Operator 执行；
7. 保留当前 Action Trace 和 NDJSON 流。

不要为了“更像 Agent”让模型直接控制 Worker 或 Provider。

### P1：审计规模化

1. Run events 和 node results 分页；
2. 按 source、decision、status、node、reason 过滤；
3. Provider evidence 单独查询；
4. JSONL/CSV 审计导出；
5. 单资产完整节点轨迹；
6. Action Trace 独立持久化表，而不是长期放在 Conversation context；
7. 为流式端点增加取消、断线和重连语义。

### P1/P2：可移植安装和更多算子准入

继续执行 v7 计划：

- 干净 Windows `catalog/cpu/remote` Profile；
- Linux CPU；
- Linux GPU Worker；
- 安装幂等、Registry 修复和离线阻断；
- 217 个发现项与 Admission Catalog 分离；
- CPU 图片算子按 Golden Set 批量准入；
- 模型算子绑定独立 Output Adapter；
- GPU/模型算子经过许可证、revision、SHA256 和独立评测后再发布。

## 6. 必须记住的经验，不要重复踩坑

### 6.1 不要通过新增复合 Operator 解决每个多目标任务

Operator 应保持可复用的原子能力。可兼容的模型判断通过一次 Prompt 和结构化输出共享证据，下游策略节点分别消费。否则会出现：

```text
猫狗真实性分类算子
猪狗真实性分类算子
车辆真实性分类算子
……
```

这会让 Catalog、测试、准入和版本管理无限膨胀。

### 6.2 “共享一次调用”必须经过编译条件判断

不能只因为两个节点都叫 VLM 就合并。必须检查输入状态、模型、Variant、Prompt 契约、策略和输出字段是否兼容。两次调用之间有图片变换时绝不能合并。

### 6.3 不要把代码测试中的单节点误写成真实性能收益已经验证

当前自动测试证明 Pipeline 只编译一个共享 VLM 节点，但真实 37/60-case 的调用数、耗时、费用和准确率尚未重新测量。文档必须区分：

```text
编译行为通过
自动集成通过
真实小集合通过
完整 Acceptance 通过
```

### 6.4 不要为了启动方便破坏进程边界

API、Worker、TUI 仍有不同职责。一键启动器只是进程监督器。不要把 Worker 循环塞进 API，也不要让 TUI 直接执行 Operator。

### 6.5 只重启 TUI 不能更新 API 和 Worker

今后本地验证优先使用 `dataagent start`。遇到旧端口实例时先停止旧 Stack，不要绕过 instance/revision 检查。

### 6.6 不要在代码中维护确认语义词典

“好”“继续”“可以了”“就这样”“没问题”等表达无限多，词表必然漏掉并误伤。模型负责语义，Action Schema 负责结构，Policy 负责状态合法性。

### 6.7 结构化缺口可以确定性，问题文案必须动态

系统可以知道缺 `preferences.unknown_policy`，但不应在代码里固定猫狗任务的问法。自然语言问题必须根据 TaskSpec、分类集合和已回答内容生成。

### 6.8 不要把推荐默认值写成自然语言需求

默认值应写入明确字段，例如：

```text
preferences.mixed_policy = review
hard_constraints.preserve_source = true
```

不要把“按安全方式复制到新目录”追加进 `semantic_requirements`，否则确认词和系统话术会污染 TaskSpec。

### 6.9 不要把执行调试文件复制进每个 DatasetAsset

Provider stdout/stderr、output JSONL、stats JSONL 属于 Run 执行证据，不是每张交付图片的独立 Artifact。交付 Manifest 和执行审计必须分层。

### 6.10 紧凑 Manifest 仍必须可完整恢复

可以省略空默认字段，但不能改变 Domain 语义。每次修改 Manifest serializer 都要做 `DatasetVersion.model_validate(payload)` round-trip。

### 6.11 Tool Trace 不是思维链

允许展示：

- 调用了什么；
- 输入参数的脱敏摘要；
- 执行耗时；
- 结构化结果；
- evidence IDs；
- 错误类型和下一步。

禁止展示或伪造：

- 隐式 Chain of Thought；
- 模型内部逐 token reasoning；
- 没有证据支持的控制面事实；
- 密钥和完整敏感配置。

### 6.12 Tool 进入真实链路后仍不能拥有第二套状态机

`propose_control_action` 只校验提议，真正状态变更仍由 ConversationService、ActionPolicy、AgentRuntime 和 LangGraph 完成。Tool executor 不直接改数据库。

### 6.13 不要把当前 Tool Loop 描述成原生 function calling

当前模型先输出 Action，再由应用调用 Tool。以后接原生 `tool_calls` 时，可以替换模型交互适配层，但不能绕过现有 Registry 和 Policy。

### 6.14 实时行动流必须有最终事件

流式协议必须遵守：

```text
0..N 个 action
1 个 final
```

发生错误时返回结构化 error。TUI 如果没有收到 final，必须明确报错，不能把半轮行动当成完成。

### 6.15 Action Trace 不能无限注入模型上下文

当前只保留最近 25 轮，并从模型 control context 中移除。以后数量增长后应迁移到独立审计表并分页，不要让历史 Trace 吞掉上下文窗口。

### 6.16 仍要遵守 v7 的所有关键边界

尤其不要重复以下错误：

- 把 217 个 discoverable 写成 217 个 released；
- 让模型手写任意 Pipeline YAML；
- 把空 VLM 输出当正常 unknown；
- 给所有 Data-Juicer 算子套同一个解析器；
- 从 PARTIAL 直接导出；
- 自动排除 abandoned；
- 覆盖旧 Run、DatasetVersion 或验收证据；
- 把本机 `.dataagent` 证据误认为已经进入 Git。

## 7. 关键代码位置

```text
dataagent/agents/processing/nodes.py
  共享 VLM 节点编译、TaskSpec 动态变量和 Pipeline 策略

dataagent/resources/prompts/image-task-visual-tagging.v1.yaml
  一次视觉调用的版本化 Prompt 与输出契约

dataagent/operators/builtin/semantic.py
  下游真实性和分类策略消费 visual_tags

dataagent/local_stack.py
dataagent/cli.py
  一键启动、进程监督和实例身份校验

dataagent/agents/requirement/clarification.py
  TaskSpec 结构化缺口和推荐默认值

dataagent/gateway.py
  Conversation Action 契约和模型生成澄清问题

dataagent/application/conversation.py
  模型优先决策、bounded ReAct、Tool Loop、Action Trace 和持久化

dataagent/tools/loop.py
  Tool 调用计时、参数脱敏、摘要和 evidence IDs

dataagent/tools/
  ToolSpec、Registry、Result、Planning、Artifact、Inspection 和 Control Tool

dataagent/application/dataset_versions.py
  紧凑 Manifest 写入

dataagent/execution/dataset_runner.py
  Provider evidence 与 Dataset delivery artifact 分离

apps/api/main.py
  非流式对话 API 和 NDJSON 流式对话 API

apps/tui/api_client.py
apps/tui/session.py
apps/tui/app.py
  action/final 流解析、状态同步和实时行动展示
```

## 8. 接手后的检查步骤

### 8.1 Git 和测试

```powershell
cd D:\newDataAgent
git status --short --branch
git log --oneline --decorate -40
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

当前预期：

```text
200 passed, 1 warning
```

### 8.2 一键启动

先关闭旧窗口中的 API、Worker 和 TUI，然后：

```powershell
.\.venv\Scripts\dataagent.exe start --owner local-user
```

帮助：

```powershell
.\.venv\Scripts\dataagent.exe start --help
```

如果提示端口 8000 属于其他 DataAgent instance，不要绕过检查；停止旧进程后重试。

### 8.3 交互验收

建议输入：

```text
D:\data\mix 去掉不真实、非实拍直出、不清晰、不是猫狗的图片，把猫和狗分开
```

检查：

- 澄清问题是否覆盖本任务真实缺口；
- 是否识别“不是猫狗”对应 unknown reject；
- Action Trace 是否实时显示；
- 是否显示真实 Tool 名称；
- 参数是否脱敏；
- 是否有 evidence IDs；
- Pipeline 是否显示真实节点和策略差异；
- 是否只编译一个 `visual_tagging` VLM 节点。

### 8.4 Run 和 Manifest

完成小集合 Run 后核对：

```text
Run events
run_node_results
provider_execution_evidence_published
DatasetVersion
manifest.json
QCReport
实际分类目录
```

Manifest 中不应再在每个 asset 下重复出现 Data-Juicer `output.jsonl` 和 `output_stats.jsonl`。

## 9. v7 之后提交索引

```text
02f20fd feat: share VLM tagging across visual capabilities
2a06e86 feat: launch the local agent stack with one command
eef53f4 feat: generate task clarifications from structured gaps
7f07609 feat: separate delivery manifests from provider evidence
62465ba feat: expose governed agent tool action traces
c5d67d4 feat: stream agent action traces to the TUI
```

每个功能均在独立提交前完成定向测试，并在最终执行完整回归。

## 10. 一句话接手结论

DataAgent 已从 v7 的“具备 Tool 骨架和 P0 生产闭环”推进到“共享一次 VLM 视觉证据、一键管理本地 Stack、模型动态澄清、紧凑交付 Manifest、真实受治理 Tool Loop 和 TUI 实时行动流”；当前最优先工作不是继续增加话术或复合算子，而是用当前 HEAD 重启完整 Stack，验证共享 VLM 的真实一次调用和多标签准确率，再完成受预算约束的 60-case Acceptance、Repair/Abandon/Exclude/Export 验收。

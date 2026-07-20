# DataAgent 模型路由与多 Agent 上下文策略 v1.0

> 日期：2026-07-18  
> 最近修订：2026-07-19
> 状态：设计基线  
> 适用范围：规划模型、四 Agent、TUI 对话、视觉评估、代码生成和图片生成 Operator

## 1. 结论摘要

DataAgent 当前不需要为每个 Agent 配置一个不同模型，也不应让一个模型承担所有任务。
近期采用三个核心能力槽即可：

```text
FAST_TEXT_MODEL=glm-5.2
REASONING_MODEL=glm-5.2
VISION_MODEL=qwen3.7-plus
```

图片生成不属于普通对话模型路由，应作为独立 Operator 配置：

```text
IMAGE_GENERATION_MODEL=wan2.7-image
IMAGE_GENERATION_PRO_MODEL=wan2.7-image-pro
TEXT_IMAGE_MODEL=qwen-image-2.0-pro
```

核心原则：

- GLM-5.2 作为普通对话、规划、复杂推理和代码任务的正式默认模型；
- Qwen3.7-Plus 负责视觉理解，不替代专业分割、人脸或检测模型；
- `FAST_TEXT_MODEL` 仍保留为独立能力槽，当前与 `REASONING_MODEL` 共同映射到 GLM-5.2；
- Requirement、Processing、Strategy 可以共享同一个推理模型；
- 不根据厂商推荐页直接决定模型优劣，必须使用 DataAgent 自己的评测集；
- 新模型只有在角色级 Golden Set 上证明收益后，才加入正式路由。

## 2. 为什么保留 GLM-5.2

GLM-5.2 是面向长周期任务、Agent 和编码场景的旗舰文本模型。其官方能力重点包括：

- 稳定的长上下文和长周期任务执行；
- 仓库级代码理解、修改、调试与测试；
- 多档 thinking effort，用于平衡质量、延迟和成本；
- 复杂文本推理、架构设计和多步骤 Agent 任务。

GLM 官方模型卡显示，它在 SWE-bench Pro、NL2Repo、DeepSWE、ProgramBench 和
Terminal Bench 等编码评测上具有很强竞争力。厂商基准不能代替 DataAgent 自测，但足以
说明 GLM-5.2 不是次要或过渡模型。

参考：

- [GLM-5.2 官方模型卡](https://huggingface.co/zai-org/GLM-5.2/blob/main/README.md)
- [GLM-5.2 官方说明](https://z.ai/blog/glm-5.2)

此前较少推荐 GLM-5.2，主要是因为参考了百炼的模型选型页。百炼页面适合判断接口能力和
地域可用性，但作为阿里平台，会自然地把 Qwen 作为默认推荐，不能单独用于跨厂商排名。

## 3. 模型能力与 API 能力必须分开判断

模型本身的能力不等于当前托管 API 暴露的能力。例如：

- GLM 官方服务可能提供 1M 上下文和多档 thinking effort；
- 百炼托管接口可能使用不同上下文上限、参数或兼容层；
- 模型支持结构化输出，不代表当前 Anthropic/OpenAI 兼容接口完全支持相同参数；
- 浮动模型别名可能在平台升级后指向不同快照。

因此每个模型接入时必须验证：

1. 实际 endpoint 和地域；
2. 最大上下文和最大输出；
3. thinking 参数是否真正生效；
4. JSON/结构化输出和 Function Calling；
5. 图片输入、批量调用和缓存能力；
6. 超时、限流、错误码和重试语义；
7. 是否可以固定模型快照。

当前 `ModelGateway` 统一发送：

```python
"thinking": {"type": "disabled"}
```

这意味着当前实现没有释放 GLM-5.2 的强推理能力。后续应按任务风险允许
`disabled/medium/high`，而不是全局固定。

## 4. 近期模型路由

### 4.1 三个核心能力槽

| 能力槽 | 主模型 | 适用任务 |
|---|---|---|
| `FAST_TEXT_MODEL` | `glm-5.2` | TUI 意图分类、普通对话、短摘要、简单查询改写和低风险抽取 |
| `REASONING_MODEL` | `glm-5.2` | TaskSpec、Pipeline、复杂约束、策略、代码和故障分析 |
| `VISION_MODEL` | `qwen3.7-plus` | 图片语义判断、OCR、标签、视觉复核和困难样本解释 |

这三个槽是“能力角色”，不是“Agent 名字”。多个 Agent 可以共享同一个能力槽。

### 4.2 四 Agent 推荐

| Agent | 默认模型 | 说明 |
|---|---|---|
| Requirement Agent | `glm-5.2` | 需求澄清、约束拆解、TaskSpec 生成 |
| Retrieval Agent | `glm-5.2` | 查询改写、字段映射和复杂检索规划 |
| Processing Agent | `glm-5.2` | Operator 选择、参数生成、Pipeline 设计和代码类任务 |
| Strategy Agent | `glm-5.2` | 多方案比较、成本质量权衡和迭代策略 |

Requirement、Processing 和 Strategy 不需要因为名称不同就使用不同模型。只有实际评测证明
某个角色需要不同能力时，才增加独立模型槽。

### 4.3 TUI 对话

TUI 路由建议：

1. “你好”“帮助”“你是什么模型”等确定性请求继续使用本地快速路径，不调用模型；
2. 普通聊天、意图识别使用 `FAST_TEXT_MODEL`，当前正式默认值为 `glm-5.2`；
3. 明确的数据任务、复杂澄清和方案解释使用 `REASONING_MODEL`，当前同样为 `glm-5.2`；
4. 涉及图片内容时调用 `qwen3.7-plus`，不把图片发送给纯文本模型。

## 5. 可选模型的定位

以下模型暂时作为评测候选，不立即增加生产配置槽：

| 模型 | 候选用途 |
|---|---|
| `qwen3.7-max` | 极复杂文本规划和长周期任务的挑战者 |
| `deepseek-v4-pro` | 策略反例、文本复核和异构 Critic |
| `deepseek-v4-flash` | 低成本文本任务候选 |
| `kimi-k2.7-code` | Operator/Adapter 代码和测试草稿 |
| `kimi-k2.6` | 通用 Agent、代码和视觉复核候选 |
| `MiniMax-M2.5` | Spec-first 编程和 Agent 工作候选 |
| `glm-5.1`、`glm-5` | 兼容回退，不用于新功能首选 |
| `qwen3.6-plus` | Qwen3.7-Plus 不可用时的兼容回退 |

候选模型进入生产路由前，应在同一套任务、prompt、工具和 token 预算下比较：

- TaskSpec 字段正确率；
- JSON/Pydantic 合规率；
- Pipeline 可执行率；
- Operator 选择准确率；
- 代码测试通过率；
- 视觉 Golden Set 准确率和分歧切片；
- P50/P95 延迟、token 与调用成本；
- 超时、限流和重试后的任务成功率。

## 6. 图片处理与生成模型

### 6.1 通用视觉模型适合的任务

`qwen3.7-plus`适合：

- 图片语义筛选；
- 标签、描述和 OCR；
- 内容合规初筛；
- 美学候选标注；
- 水印存在性初筛；
- 生成图片的语义验收；
- 专业模型分歧样本的解释。

### 6.2 通用视觉模型不能替代的专业算子

- 图像分割需要稳定、可复现的像素级 Mask；
- 人像 ID 需要固定 Embedding、阈值标定、隐私和授权治理；
- 大规模美学评分需要固定模型版本和可重复分数；
- 精细水印定位需要检测或分割输出；
- 通用 VLM 的自然语言判断不能冒充专业模型量化指标。

这些能力仍优先使用经过评测的专业开源模型。通用视觉 API 只负责辅助标注、复核和解释。

### 6.3 图片生成 Operator

| 场景 | 推荐模型 |
|---|---|
| 普通批量生成、速度优先 | `wan2.7-image` |
| 高质量、角色一致性、多图参考、品牌色 | `wan2.7-image-pro` |
| 海报、图表、中英文文字、负向提示词 | `qwen-image-2.0-pro` |
| 文字类快速生成和多变体 | `qwen-image-2.0` |

图片生成模型应封装为独立 Operator，并记录：

- 模型 ID 和固定快照；
- prompt、negative prompt 和 prompt 版本；
- 输入参考图及其 SHA256；
- 输出图片 SHA256；
- 尺寸、数量、耗时、费用和 Provider request ID；
- 内容安全结果和人工审核状态。

## 7. 多 Agent 到底共享什么上下文

多 Agent 不是完全互不通信。正确边界是：

> 不共享自由文本思考过程和隐藏思维链，但共享经过约束的正式状态、版本引用和证据。

当前四 Agent 共享 `WorkOrderGraphState`，包括：

- `task_spec`；
- `retrieval_plan`；
- `pipeline_variants`；
- `representative_pipelines`；
- `selected_pipeline_id`；
- `sampling_plan`；
- `next_action` 和 `trace`。

这些字段由主图依次传给下一个 Agent。ConversationThread 的完整聊天历史不会自动进入每个
Agent，模型隐藏思考也不会跨 Agent 传播。

## 8. 切换模型会不会丢上下文

不会因为模型不同而自动丢上下文。大模型 API 本身是无状态的：

- 使用同一个模型，也必须由应用重新发送消息和状态；
- 使用不同模型，只要发送相同的结构化输入，也能得到同一份任务上下文；
- 模型之间不会共享隐藏记忆，也不会直接互相通信。

真正的风险来自：

- 不同模型对同一字段的解释尺度不同；
- JSON、工具调用和错误恢复能力不同；
- 上下文窗口、token 计算和图片输入格式不同；
- 对话中途频繁切换会造成语气和决策标准波动；
- 浮动模型别名升级后可能改变行为。

控制措施：

1. Agent 之间只传 Pydantic 校验过的结构化对象；
2. 每个 WorkOrder 固定路由版本，不在同一任务中随机切换；
3. 升级模型时生成新路由版本并重新跑 Golden Set；
4. 记录模型 ID、快照、prompt 版本、参数、token、延迟和 request ID；
5. 不传递隐藏思维链，只传结论、证据、约束和未决问题；
6. 发生升级时由 Router 明确记录升级原因，例如低置信度、Schema 失败或任务复杂度超限。

## 9. ModelRoutingPolicy 的建议结构

当前不需要十几个环境变量。建议先实现：

```python
class ModelRoutingPolicy:
    fast_text_model: str
    reasoning_model: str
    vision_model: str
```

路由输入至少包括：

```text
task_kind
requires_vision
risk_level
complexity
expected_schema
latency_budget
cost_budget
work_order_routing_version
```

路由输出至少包括：

```text
model_id
model_snapshot
thinking_effort
max_tokens
timeout_seconds
fallback_chain
routing_reason
```

不要把路由写成大量 `if agent_name == ...`。应该根据视觉、复杂度、风险、时延和输出契约选择
能力槽，再由能力槽解析为具体模型。

## 10. 分阶段实施

### 阶段一：保持现有两槽配置

```env
CODE_MODEL=glm-5.2
PRIMARY_VISION_MODEL=qwen3.7-plus
```

先不要切换现有主模型，补齐 GLM-5.2 thinking 参数的 API 探测和回归测试。

### 阶段二：保留独立快速文本槽

新增：

```env
FAST_TEXT_MODEL=glm-5.2
```

TUI 普通对话、意图识别和短文本任务继续通过独立能力槽调用；当前因模型可用性验证结果，正式映射到 GLM-5.2。

### 阶段三：实现能力路由和升级

- 默认短文本走 `FAST_TEXT_MODEL`，当前为 GLM-5.2；
- 复杂规划和代码走 GLM-5.2；
- 图片输入走 Qwen3.7-Plus；
- Schema 校验失败、低置信度或高风险任务允许升级；
- 每次路由写入运行证据。

### 阶段四：用评测决定是否增加模型

只有当 DeepSeek、Kimi、MiniMax 或 Qwen Max 在明确角色上稳定优于现有模型，才增加新的正式
能力槽。不能因为模型列表中存在或厂商宣传更强就立即增加生产复杂度。

## 11. 最终决策

近期 DataAgent 的推荐组合是：

```text
GLM-5.2          -> TUI 对话、核心规划、Agent 推理、Pipeline 和代码
Qwen3.7-Plus     -> 图片理解和视觉评估
Wan2.7-Image     -> 普通图片生成 Operator
Wan2.7-Image-Pro -> 高质量图片生成 Operator
Qwen-Image-2.0-Pro -> 文字密集和负向提示词图片生成
```

这不是按厂商划分 Agent，而是按能力划分任务。模型切换不会天然破坏上下文；上下文连续性由
DataAgent 的结构化状态、版本对象和 Router 负责。

## 12. 2026-07-18 实施状态

本轮已完成第一版代码落地：

- 新增 `ModelRoutingPolicy`，按 `task_kind` 和能力槽路由，不以 Agent 名称作为路由键；
- 普通对话和意图理解使用 `FAST_TEXT_MODEL`；
- Requirement、Processing、Strategy 和代码类任务统一映射到 `REASONING_MODEL`；
- 图片语义评估使用 `VISION_MODEL`；
- 三个图片生成模型已登记为独立 Operator 路由配置，不接入普通对话 Gateway；当前尚未实现可执行的生图 Operator；
- 保留 `CODE_MODEL`、`PRIMARY_VISION_MODEL` 作为旧环境变量兼容入口，新配置优先；
- 2026-07-19 起，`FAST_TEXT_MODEL` 的正式默认值改为 `glm-5.2`；Qwen3.6-Flash 因当前 API Key 返回模型级 `403 AccessDenied`，不再作为默认值；
- 当前继续关闭 thinking，待百炼接口能力探测与角色级 Golden Set 通过后再启用。

尚未完成的部分包括：低置信度自动升级、WorkOrder 路由版本冻结、逐次路由证据持久化、可执行
生图 Operator，以及候选模型的角色级 Golden Set。新模型在这些评测完成前不得加入正式能力槽。

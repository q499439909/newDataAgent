# DataAgent CLI

DataAgent 是一个面向图片数据生产的本地 CLI。它把自然语言需求转换为 `TaskSpec`，在同一批图片上比较“保留优先、均衡、质量优先”三类 Pipeline，经边界样本审核后执行全量任务，并输出不可变数据版本和可复用 Pipeline。

仓库正在按最终 PRD 迁移为“四个专业 Agent + LangGraph + 统一控制面”的完整平台。原 CLI 继续可用；新增控制面已经支持四 Agent 子图、两处 HITL interrupt、SQLite checkpoint、正式版本追加存储、分类算子库、真实图片节点预览，以及独立 Worker 驱动的 DatasetVersion 生产闭环。

## 安装

```powershell
cd D:\newDataAgent
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

项目使用独立 `.venv`，避免 LangGraph SDK 与机器上其他 Python 工具产生依赖冲突。

## 启动控制面 API

```powershell
.\.venv\Scripts\dataagent-api.exe
```

默认地址为 `http://127.0.0.1:8000`，OpenAPI 文档为 `http://127.0.0.1:8000/docs`。当前控制面提供：

- 启动、查询和恢复同一 LangGraph Agent thread；
- TaskSpec 确认和 Pipeline 选择 interrupt；
- 需求规划、检索、数据处理和数据策略四个子图；
- 三类用户 Pipeline 与每类内部变体；
- Owner 级 thread 隔离；
- SQLite checkpoint 和不可变领域版本存储；
- 分类算子列表；
- 任务数据源范围内的真实节点图片预览。

API 暂以 `X-Owner-ID` 请求头传递本地 Owner，上线认证模块后将由 Session 自动注入，客户端不能自行指定他人 Owner。

## 启动本地执行 Worker

另开一个 PowerShell 窗口运行：

```powershell
.\.venv\Scripts\dataagent-worker.exe
```

本地部署配置使用 SQLite 持久队列和单 Worker。Agent 审批完成后，通过 `POST /api/work-orders/{work_order_id}/runs` 提交 Run；请求必须带 `Idempotency-Key`，重复提交同一键只返回原 Run。Worker 只接受已确认 TaskSpec 和已批准 PipelineVersion，逐图保存断点并支持暂停、恢复和取消。进程异常退出后，重新启动 Worker 会从已保存的资产断点继续。

当前本地 Worker 支持 `local_directory` 图片源。成功 Run 将只读校验原图，在 `DATAAGENT_HOME/platform/datasets` 下发布不可变 DatasetVersion、Manifest 和保留图片副本；空输出或原图执行期间发生变化时禁止发布。发布后由独立 Quality Evaluator 对保留资产执行全量硬规则检查，生成版本化 QCReport；尚未接入 Golden Set 时会明确标记语义质量未经验证，不把清晰度等代理指标描述为真实准确率。

相关控制面接口：

- `GET /api/work-orders/{work_order_id}/runs`：查看工单 Run；
- `GET /api/runs/{run_id}`：查看进度和结果；
- `POST /api/runs/{run_id}/control`：执行 `pause`、`resume` 或 `cancel`；
- `GET /api/datasets/{dataset_version_id}`：查看 DatasetVersion 和资产血缘。
- `GET /api/qc-reports/{qc_report_id}`：查看质量结论、指标、失败资产和返工建议。

## 启动 Agentic TUI

API 和 Worker 运行后，再开一个 PowerShell 窗口：

```powershell
.\.venv\Scripts\dataagent-tui.exe --owner local-user
```

TUI 启动后会创建持久 `ConversationThread`，普通文本由配置的规划模型理解和回复。可以先闲聊、询问使用方式或当前模型，也可以自然描述数据目标；只有识别到明确任务后才会继续追问图片目录、约束和审批，不会把普通问题当成工单。也可使用 `/new D:\images | 筛选清晰图片并去重` 快速创建任务。当前驾驶舱支持 TaskSpec 与 Pipeline 审批、状态同步、Run 提交与查看、暂停、恢复、取消，以及 DatasetVersion 和 QCReport 摘要；输入 `/help` 查看可选快捷命令。

启动时会显示 conversation ID。关闭后可恢复同一段消息历史和关联工单：

```powershell
.\.venv\Scripts\dataagent-tui.exe --owner local-user --conversation conversation_xxx
```

TUI 只调用 FastAPI，不直接访问 SQLite。当前版本尚未接入附件、流式 token 输出、服务端事件订阅和 Web 图片审核深链接，这些能力会在 Web 工作台里程碑继续补齐。

DataAgent 默认依次读取当前目录的 `model.env`、`model.env.txt` 和 `.env`。真实密钥文件已被 `.gitignore` 排除。配置示例见 `.env.example`。

## 环境检查

```powershell
dataagent doctor
dataagent doctor --check-api
```

`--check-api` 会向规划模型发送一个很小的健康检查请求；输出不会显示 API Key。

## 完整流程

### 1. 创建任务

```powershell
dataagent task create `
  --source "D:\images\incoming" `
  --requirement "筛选短边至少 1440、人物清晰的生活照片，去重并输出清单"
```

创建结果会显示 Task ID 和规划模型生成的 `TaskSpec`。API 不可用时，CLI 会明确提示并回退到保守的本地解析，不会伪装成模型规划结果。

如需修正规划结果，先准备符合 `TaskSpec` 结构的 JSON，再在确认前执行：

```powershell
dataagent task update-spec task_xxxxxxxxxxxx --file .\task-spec.json
```

### 2. 确认需求并试跑

```powershell
dataagent task confirm task_xxxxxxxxxxxx
dataagent trial run task_xxxxxxxxxxxx --sample-size 100 --vision-limit 8
```

三类 Pipeline 使用完全相同的样本。`--vision-limit` 控制视觉模型抽样调用数，避免对数千张图片无上限调用 API。未经过人工审核的结果会标记为代理评估。

### 3. 审核边界样本

```powershell
dataagent review start task_xxxxxxxxxxxx
```

默认审核均衡方案，CLI 会逐条显示图片路径、Pipeline 判断、拒绝原因、尺寸、亮度、清晰度和视觉模型结论。也可以指定候选：

```powershell
dataagent review start task_xxxxxxxxxxxx --pipeline pipe_xxxxxxxxxxxx --limit 20
```

### 4. 全量执行

```powershell
dataagent run start task_xxxxxxxxxxxx --pipeline pipe_xxxxxxxxxxxx
```

全量任务默认每 100 张更新一次进度。DataAgent 会校验执行前后的全部原图哈希；任何原图发生变化都会中止发布。筛选结果写入 Manifest，图片转换产物写入新的数据版本目录。

如果 TaskSpec 含语义条件，全量执行会逐图调用视觉模型。只有明确接受“结果不能声称满足语义要求”时才使用 `--skip-semantic` 跳过。

### 5. 查看与复用

```powershell
dataagent task list
dataagent trial report task_xxxxxxxxxxxx
dataagent run show run_xxxxxxxxxxxx
dataagent dataset show dataset_xxxxxxxxxxxx
dataagent pipeline list
dataagent pipeline reuse pipe_xxxxxxxxxxxx --source "D:\images\next-batch"
```

复用历史 Pipeline 时仍会建立新任务，并与另外两种策略在新数据的小样本上重新比较，不会直接跳过回归试跑。

## Milvus 探测

安装可选依赖后，可以探测现有 Collection 的向量字段和原图定位字段：

```powershell
python -m pip install -e .[milvus]
dataagent milvus discover `
  --uri "http://127.0.0.1:19530" `
  --collection image_collection
```

Token 默认从 `MILVUS_TOKEN` 环境变量读取且不会输出。只有同时发现向量字段和图片路径/URI 候选字段时，CLI 才会把 Collection 标记为可进入处理映射配置。

## 本地数据

默认工作目录为 `D:\newDataAgent\.dataagent`：

```text
.dataagent/
├── dataagent.db
├── reports/
│   └── task_*-trial.json
└── datasets/
    └── dataset_*/
        ├── manifest.json
        └── files/
```

可以通过 `DATAAGENT_HOME` 改到其他磁盘。原始图片目录始终只读使用，不原地覆盖或删除。

## 算子库开发模式

算子通过统一的 Operator、Provider 和 Runtime 协议注册。当前内置八个已发布的 CPU 参考算子，使九个主类别都有可运行实现；另有美学评分、图像分割、水印识别、人像 ID 四个开发态模型算子。模型算子默认使用确定性的 Mock 后端，不需要 GPU，也不会下载权重；Mock 结果可以用于 Pipeline 编译和节点预览，但生产提交会拒绝 Draft 或 Mock 算子。

```powershell
$env:DATAAGENT_ALLOW_MODEL_DOWNLOAD = "false"
$env:DATAAGENT_DATAJUICER_ENABLED = "true"
$env:DATAAGENT_DATAJUICER_PYTHON = "D:\path\to\data-juicer-env\python.exe"
$env:DATAAGENT_DATAJUICER_PROCESS_BIN = "D:\path\to\data-juicer-env\Scripts\dj-process.exe"
```

也可以复制 `dataagent.local.env.example` 为被 Git 忽略的 `dataagent.local.env`。Data-Juicer 是可选 Provider；建议指向独立环境，控制面不会导入它的重依赖。发现阶段只读取元数据，不实例化模型；当前执行阶段只放行带 `cpu` 标签的 Filter。未允许下载时，执行器会同时启用 Hugging Face、uv、pip 离线策略，并阻止 Data-Juicer LazyLoader 自动安装依赖。真实模型后端需要固定 revision、SHA256 和许可证后再单独接入。

控制面接口：

- `GET /api/operator-providers`：Provider 健康状态；
- `GET /api/operator-providers/datajuicer/operators?query=shape`：检索外部算子；
- `POST /api/work-orders/{work_order_id}/operator-providers/datajuicer/execute`：在 WorkOrder 已确认的本地数据目录内执行 CPU Filter。

## 当前 CLI 边界

- 已实现本地图片目录的规划、三方案试跑、视觉抽样评估、边界审核、全量执行、不可变版本和 Pipeline 复用。
- 已提供 Milvus Schema 探测；自然语言检索仍需补充现有文本编码器和字段映射配置。
- Docker 生成算子沙箱和异步多任务 Worker 属于下一阶段接入点；当前 CLI 不会在没有沙箱时执行大模型生成的代码。
- 当前清晰度和亮度属于无参考代理指标，必须结合视觉模型和需求提出者审核使用。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

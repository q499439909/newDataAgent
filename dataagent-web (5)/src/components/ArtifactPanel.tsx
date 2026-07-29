import React, { useEffect, useState } from 'react';
import { useApp } from '../context/AppContext';
import { OPERATOR_LEVEL_MAP, PipelineVersion, WorkOrder } from '../types';
import { listRunEvents } from '../api/dataagentClient';
import {
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  FileText,
  GitBranch,
  ListChecks,
  Maximize2,
  Minimize2,
  Play,
  RotateCcw,
  SlidersHorizontal,
  X,
} from 'lucide-react';

export type ArtifactKind = 'task_spec' | 'pipeline' | 'quality' | 'run' | 'files';

interface ArtifactPanelProps {
  activeKind: ArtifactKind;
  onSelectKind: (kind: ArtifactKind) => void;
  onClose: () => void;
  isFullscreen: boolean;
  onToggleFullscreen: () => void;
}

const artifactLabels: Record<ArtifactKind, string> = {
  task_spec: 'TaskSpec',
  pipeline: 'Pipeline',
  quality: '边界/QC',
  run: '运行产物',
  files: '文件',
};

const artifactIcons: Record<ArtifactKind, React.ElementType> = {
  task_spec: FileText,
  pipeline: GitBranch,
  quality: ListChecks,
  run: Play,
  files: SlidersHorizontal,
};

function rawState(workOrder: WorkOrder | null): Record<string, any> {
  return workOrder?.agentTurn?.state || {};
}

function hasArtifact(workOrder: WorkOrder | null, kind: ArtifactKind): boolean {
  if (!workOrder) return false;
  const state = rawState(workOrder);
  const latestRun = workOrder.latestRunObservation || state.latest_run_observation;
  if (kind === 'task_spec') return Boolean(workOrder.currentTaskSpec);
  if (kind === 'pipeline') return Boolean(workOrder.candidatePipelines?.length || workOrder.selectedPipelineId);
  if (kind === 'quality') {
    return Boolean(
      workOrder.nodePreviews?.length ||
      workOrder.qcReport ||
      latestRun?.qc_report_id ||
      latestRun?.qc_status,
    );
  }
  if (kind === 'run') {
    return Boolean(latestRun || workOrder.activeRunId || state.active_run_id || state.next_action?.includes?.('run'));
  }
  if (kind === 'files') {
    return Boolean(
      latestRun?.dataset_version_id ||
      latestRun?.failed_asset_uris?.length ||
      latestRun?.repair_candidate_uris?.length ||
      latestRun?.evidence_refs?.length,
    );
  }
  return false;
}

function EmptyArtifact({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-600">
      <div className="font-semibold text-slate-900">{title}</div>
      <p className="mt-1 text-xs leading-relaxed">{detail}</p>
    </div>
  );
}

function JsonBlock({ value }: { value: any }) {
  if (value === undefined || value === null) return null;
  return (
    <pre className="max-h-72 overflow-auto rounded-xl bg-slate-950 p-3 text-[11px] leading-relaxed text-slate-100">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

function MetricGrid({ metrics }: { metrics: Record<string, any> }) {
  const entries = Object.entries(metrics || {});
  if (!entries.length) {
    return <EmptyArtifact title="暂无真实指标" detail="后端还没有返回 metrics，前端不会展示估算值或默认值。" />;
  }
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
      {entries.map(([key, value]) => (
        <div key={key} className="rounded-xl border border-slate-200 bg-white p-3">
          <div className="text-[11px] text-slate-500">{key}</div>
          <div className="mt-1 break-words text-sm font-semibold text-slate-900">{String(value)}</div>
        </div>
      ))}
    </div>
  );
}

function TaskSpecArtifact({ workOrder }: { workOrder: WorkOrder }) {
  const spec = workOrder.currentTaskSpec;
  if (!spec) {
    return <EmptyArtifact title="TaskSpec 尚未生成" detail="请先在左侧对话中提交需求，等待主 Agent 生成 TaskSpec。" />;
  }
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h3 className="text-base font-bold text-slate-900">{spec.name}</h3>
            <p className="mt-1 text-xs text-slate-500">{spec.version} · {spec.status}</p>
          </div>
          <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-[11px] font-semibold text-emerald-800">
            {spec.createdBy}
          </span>
        </div>
      </div>

      {[
        ['硬约束', spec.hardConstraints],
        ['语义约束', spec.semanticConstraints],
        ['输出要求', spec.outputRequirements],
        ['验收标准', spec.acceptanceCriteria],
        ['待澄清项', spec.ambiguities],
      ].map(([title, items]) => (
        <div key={String(title)} className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="mb-2 text-xs font-bold text-slate-900">{String(title)}</div>
          {(items as string[]).length ? (
            <ul className="space-y-2 text-xs leading-relaxed text-slate-700">
              {(items as string[]).map((item, index) => <li key={index}>{index + 1}. {item}</li>)}
            </ul>
          ) : (
            <div className="text-xs text-slate-400">后端未返回该字段。</div>
          )}
        </div>
      ))}

      {spec.clauseTraces?.length ? (
        <div className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="mb-2 text-xs font-bold text-slate-900">Clause Traces</div>
          <div className="space-y-2">
            {spec.clauseTraces.map((trace, index) => (
              <div key={index} className="rounded-lg border border-slate-100 bg-slate-50 p-3 text-xs">
                <div className="font-semibold text-slate-900">{trace.role}</div>
                <div className="mt-1 text-slate-700">{trace.source_text}</div>
                <JsonBlock value={trace.normalized_effect} />
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function PipelineArtifact({ workOrder }: { workOrder: WorkOrder }) {
  const { approveCurrentPipeline, submitCurrentDatasetRun, showToast } = useApp();
  const pipelines = workOrder.candidatePipelines || [];
  const [selectedPipelineId, setSelectedPipelineId] = useState(workOrder.selectedPipelineId || pipelines[0]?.id || '');
  const selected = pipelines.find(item => item.id === selectedPipelineId) || pipelines[0];

  const metricText = (value: number | null | undefined, suffix = '', prefix = '') => (
    value === null || value === undefined ? '待后端试跑生成' : `${prefix}${value}${suffix}`
  );

  const handleApply = async (pipeline: PipelineVersion) => {
    setSelectedPipelineId(pipeline.id);
    if (workOrder.waitingFor === 'pipeline_approval') {
      await approveCurrentPipeline(pipeline.id);
      return;
    }
    if (workOrder.agentTurn?.state?.next_action === 'submit_dataset_run') {
      await submitCurrentDatasetRun();
      return;
    }
    showToast('已选中 Pipeline。当前后端还没有进入审批或运行提交阶段。');
  };

  if (!pipelines.length) {
    return <EmptyArtifact title="Pipeline 尚未生成" detail="只有后端返回候选 Pipeline 后，这里才会展示方案、算子链和真实 metrics。" />;
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="text-[11px] font-semibold text-slate-500">当前选择</div>
        <div className="mt-1 text-sm font-bold text-slate-900">{selected?.name || '-'}</div>
      </div>

      <div className="space-y-3">
        {pipelines.map((pipeline) => {
          const isSelected = selected?.id === pipeline.id;
          return (
            <div key={pipeline.id} className={`rounded-xl border bg-white p-4 ${isSelected ? 'border-emerald-500 ring-2 ring-emerald-100' : 'border-slate-200'}`}>
              <button type="button" onClick={() => setSelectedPipelineId(pipeline.id)} className="flex w-full items-start justify-between gap-3 text-left">
                <div>
                  <div className="text-sm font-bold text-slate-900">{pipeline.name}</div>
                  <p className="mt-1 text-xs leading-relaxed text-slate-600">{pipeline.summary}</p>
                </div>
                {isSelected ? <CheckCircle2 className="h-5 w-5 shrink-0 text-emerald-600" /> : <ChevronDown className="h-5 w-5 shrink-0 text-slate-300" />}
              </button>

              {isSelected && (
                <div className="mt-4 space-y-4">
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div className="rounded-lg bg-slate-50 p-2">
                      <div className="text-slate-500">样本保留率</div>
                      <div className="font-bold text-slate-900">{metricText(pipeline.retentionRatePct, '%')}</div>
                    </div>
                    <div className="rounded-lg bg-slate-50 p-2">
                      <div className="text-slate-500">ModelScore</div>
                      <div className="font-bold text-slate-900">{metricText(pipeline.modelScoreEstimate)}</div>
                    </div>
                    <div className="rounded-lg bg-slate-50 p-2">
                      <div className="text-slate-500">p95 延迟</div>
                      <div className="font-bold text-slate-900">{metricText(pipeline.avgLatencyMs, ' ms')}</div>
                    </div>
                    <div className="rounded-lg bg-slate-50 p-2">
                      <div className="text-slate-500">千张成本</div>
                      <div className="font-bold text-slate-900">{metricText(pipeline.estimatedCostPer1k, '', '¥')}</div>
                    </div>
                  </div>

                  {pipeline.metricsSource === 'unavailable' && (
                    <div className="rounded-lg border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-800">
                      后端未返回试跑指标，当前不展示前端估算值。
                    </div>
                  )}

                  <div className="space-y-1.5">
                    <div className="text-[11px] font-bold text-slate-500">算子链</div>
                    {pipeline.nodes.map((node, index) => {
                      const levelInfo = OPERATOR_LEVEL_MAP[node.level];
                      return (
                        <div key={node.id} className="flex items-center gap-2 rounded-lg border border-slate-100 bg-slate-50 p-2 text-xs">
                          <span className="font-mono text-slate-400">#{index + 1}</span>
                          <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold ${levelInfo.badgeBg} ${levelInfo.badgeText}`}>
                            {node.level}
                          </span>
                          <span className="min-w-0 truncate font-medium text-slate-800">{node.operatorName}</span>
                        </div>
                      );
                    })}
                  </div>

                  <button
                    type="button"
                    onClick={() => handleApply(pipeline)}
                    className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-600 px-3 py-2 text-xs font-bold text-white hover:bg-emerald-700"
                  >
                    {workOrder.agentTurn?.state?.next_action === 'submit_dataset_run' ? <Play className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                    {workOrder.waitingFor === 'pipeline_approval'
                      ? '选择此 Pipeline'
                      : workOrder.agentTurn?.state?.next_action === 'submit_dataset_run'
                        ? '提交全量执行'
                        : '选中查看'}
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function RunArtifact({ workOrder }: { workOrder: WorkOrder }) {
  const { currentUser, sendMainAgentMessage, showToast } = useApp();
  const state = rawState(workOrder);
  const observation = workOrder.latestRunObservation || state.latest_run_observation;
  const runId = observation?.run_id || workOrder.activeRunId || state.active_run_id;
  const interrupt = workOrder.agentTurn?.interrupts?.[0]?.value;
  const allowedActions = interrupt?.kind === 'run_outcome_resolution'
    ? (interrupt.allowed_actions || []) as string[]
    : [];
  const [runEvents, setRunEvents] = useState<Record<string, any>[]>([]);
  const [eventsUnavailable, setEventsUnavailable] = useState(false);

  useEffect(() => {
    if (!runId) {
      setRunEvents([]);
      return;
    }
    let cancelled = false;
    listRunEvents(currentUser.id, runId)
      .then(events => {
        if (!cancelled) {
          setRunEvents(events);
          setEventsUnavailable(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setRunEvents([]);
          setEventsUnavailable(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [currentUser.id, runId]);

  const notificationEvents = runEvents.filter(event => [
    'run_outcome_notification_requested',
    'run_outcome_notified',
    'run_outcome_notification_failed',
  ].includes(String(event.event_type)));
  const latestNotification = notificationEvents[notificationEvents.length - 1];
  const notificationLabel = latestNotification?.event_type === 'run_outcome_notification_requested'
    ? '等待主 Agent 处理运行结果'
    : latestNotification?.event_type === 'run_outcome_notified'
      ? '主 Agent 已消费运行结果'
      : latestNotification?.event_type === 'run_outcome_notification_failed'
        ? '运行结果交接失败，Worker 将重试'
        : null;

  const submitResolution = async (action: string) => {
    await sendMainAgentMessage(`请按后端允许动作处理运行结果：${action}`);
    showToast(`已向主 Agent 提交运行结果处置动作：${action}`);
  };

  if (!observation && !runId) {
    return <EmptyArtifact title="暂无运行产物" detail="后端还没有投影 active_run_id 或 latest_run_observation。" />;
  }

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="text-[11px] font-semibold text-slate-500">Run</div>
            <h3 className="mt-1 text-base font-bold text-slate-900">{runId}</h3>
          </div>
          <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-semibold text-slate-700">
            {observation?.run_status || state.next_action || 'active'}
          </span>
        </div>
      </div>

      {notificationLabel ? (
        <div className="rounded-xl border border-slate-200 bg-white p-4 text-xs">
          <div className="font-bold text-slate-900">运行结果交接</div>
          <div className="mt-2 rounded-lg bg-slate-50 p-2 text-slate-700">
            {notificationLabel}
          </div>
          <div className="mt-2 text-[11px] text-slate-500">
            该状态来自 Run event：{latestNotification.event_type}。`requested` 只表示交接已登记，不代表 QC 通过或任务完成。
          </div>
        </div>
      ) : eventsUnavailable ? (
        <div className="rounded-xl border border-slate-200 bg-white p-3 text-xs text-slate-500">
          当前未能读取 Run events；运行状态仍以 WorkOrder state 为准。
        </div>
      ) : null}

      {observation ? (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
            <div className="rounded-xl border border-slate-200 bg-white p-3">
              <div className="text-slate-500">QC 状态</div>
              <div className="mt-1 font-bold text-slate-900">{observation.qc_status || '后端未返回'}</div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-white p-3">
              <div className="text-slate-500">Retryable</div>
              <div className="mt-1 font-bold text-slate-900">{String(observation.retryable)}</div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-white p-3">
              <div className="text-slate-500">Pipeline Version</div>
              <div className="mt-1 break-all font-bold text-slate-900">{observation.pipeline_version_id}</div>
            </div>
            <div className="rounded-xl border border-slate-200 bg-white p-3">
              <div className="text-slate-500">TaskSpec Version</div>
              <div className="mt-1 break-all font-bold text-slate-900">{observation.task_spec_version_id}</div>
            </div>
          </div>

          <div className="rounded-xl border border-sky-200 bg-sky-50 p-3 text-xs leading-relaxed text-sky-900">
            QC 状态只按后端 `qc_status` 展示；是否已完成语义质量验证必须看独立 QC 报告字段，不在前端合并判断。
          </div>

          <MetricGrid metrics={observation.metrics || {}} />

          {observation.reason_codes?.length ? (
            <div className="rounded-xl border border-slate-200 bg-white p-4">
              <div className="mb-2 text-xs font-bold text-slate-900">Reason Codes</div>
              <div className="flex flex-wrap gap-2">
                {observation.reason_codes.map(code => (
                  <span key={code} className="rounded-lg bg-slate-100 px-2 py-1 text-[11px] font-semibold text-slate-700">{code}</span>
                ))}
              </div>
            </div>
          ) : null}

          {observation.error ? (
            <div className="rounded-xl border border-red-200 bg-red-50 p-3 text-xs text-red-800">
              {observation.error}
            </div>
          ) : null}
        </>
      ) : null}

      {allowedActions.length ? (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
          <div className="mb-2 flex items-center gap-2 text-xs font-bold text-amber-900">
            <AlertCircle className="h-4 w-4" />
            后端要求选择运行结果处置动作
          </div>
          <div className="flex flex-wrap gap-2">
            {allowedActions.map(action => (
              <button
                key={action}
                type="button"
                onClick={() => submitResolution(action)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs font-semibold text-amber-900 hover:bg-amber-100"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                {action}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function QualityArtifact({ workOrder }: { workOrder: WorkOrder }) {
  const observation = workOrder.latestRunObservation || rawState(workOrder).latest_run_observation;
  return (
    <div className="space-y-4">
      {workOrder.qcReport ? <JsonBlock value={workOrder.qcReport} /> : null}
      {observation?.qc_report_id || observation?.qc_status ? (
        <div className="rounded-xl border border-slate-200 bg-white p-4 text-xs">
          <div className="font-bold text-slate-900">Run QC 摘要</div>
          <div className="mt-2 space-y-1 text-slate-700">
            <div>qc_report_id: {observation.qc_report_id || '后端未返回'}</div>
            <div>qc_status: {observation.qc_status || '后端未返回'}</div>
          </div>
          <div className="mt-3 rounded-lg bg-sky-50 p-2 text-sky-900">
            `qc_status` 与 `semantic_quality_verified` 分开展示；后者只有后端 QC 报告返回时才显示。
          </div>
        </div>
      ) : null}
      {workOrder.nodePreviews?.length ? (
        <JsonBlock value={workOrder.nodePreviews} />
      ) : (
        <EmptyArtifact title="暂无边界样本或 QC 产物" detail="只有后端返回 NodePreview、QC report 或 run outcome QC 字段后，这里才展示。" />
      )}
    </div>
  );
}

function FilesArtifact({ workOrder }: { workOrder: WorkOrder }) {
  const observation = workOrder.latestRunObservation || rawState(workOrder).latest_run_observation;
  const groups = [
    ['dataset_version_id', observation?.dataset_version_id ? [observation.dataset_version_id] : []],
    ['failed_asset_uris', observation?.failed_asset_uris || []],
    ['repair_candidate_uris', observation?.repair_candidate_uris || []],
    ['evidence_refs', observation?.evidence_refs || []],
  ] as const;
  const hasAny = groups.some(([, values]) => values.length);
  if (!hasAny) {
    return <EmptyArtifact title="暂无文件产物" detail="后端还没有返回 dataset_version_id、失败样本 URI、修复候选 URI 或证据引用。" />;
  }
  return (
    <div className="space-y-4">
      {groups.map(([title, values]) => values.length ? (
        <div key={title} className="rounded-xl border border-slate-200 bg-white p-4">
          <div className="mb-2 text-xs font-bold text-slate-900">{title}</div>
          <div className="space-y-2">
            {values.map(value => (
              <button key={value} type="button" className="block w-full rounded-lg bg-slate-50 p-2 text-left text-xs text-slate-700 hover:bg-slate-100">
                {value}
              </button>
            ))}
          </div>
        </div>
      ) : null)}
    </div>
  );
}

export const ArtifactPanel: React.FC<ArtifactPanelProps> = ({
  activeKind,
  onSelectKind,
  onClose,
  isFullscreen,
  onToggleFullscreen,
}) => {
  const { activeWorkOrder } = useApp();
  const kinds = (Object.keys(artifactLabels) as ArtifactKind[]).filter(kind => hasArtifact(activeWorkOrder, kind));
  const availableKinds = kinds.length ? kinds : [activeKind];
  const visibleKind = availableKinds.includes(activeKind) ? activeKind : availableKinds[0];
  const ActiveIcon = artifactIcons[visibleKind];

  if (!activeWorkOrder) return null;

  return (
    <aside className={`flex h-full min-w-0 flex-col border-l border-slate-200 bg-white shadow-xl ${isFullscreen ? 'fixed inset-4 z-40 rounded-2xl border' : ''}`}>
      <div className="flex items-center justify-between gap-2 border-b border-slate-200 px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-bold text-slate-900">
            <ActiveIcon className="h-4 w-4 text-emerald-600" />
            <span className="truncate">{artifactLabels[visibleKind]}</span>
          </div>
          <div className="mt-0.5 truncate text-[11px] text-slate-500">{activeWorkOrder.name}</div>
        </div>
        <div className="flex items-center gap-1">
          <button type="button" onClick={onToggleFullscreen} className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900" title={isFullscreen ? '退出全屏' : '全屏查看'}>
            {isFullscreen ? <Minimize2 className="h-4 w-4" /> : <Maximize2 className="h-4 w-4" />}
          </button>
          <button type="button" onClick={onClose} className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900" title="关闭面板">
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="flex gap-1 overflow-x-auto border-b border-slate-100 px-3 py-2">
        {availableKinds.map((kind) => {
          const Icon = artifactIcons[kind];
          const isActive = visibleKind === kind;
          return (
            <button
              key={kind}
              type="button"
              onClick={() => onSelectKind(kind)}
              className={`inline-flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-semibold ${
                isActive ? 'bg-emerald-600 text-white' : 'bg-slate-50 text-slate-600 hover:bg-slate-100'
              }`}
            >
              <Icon className="h-3.5 w-3.5" />
              {artifactLabels[kind]}
            </button>
          );
        })}
      </div>

      <div className="flex-1 overflow-y-auto bg-slate-50/70 p-4">
        {visibleKind === 'task_spec' && <TaskSpecArtifact workOrder={activeWorkOrder} />}
        {visibleKind === 'pipeline' && <PipelineArtifact workOrder={activeWorkOrder} />}
        {visibleKind === 'quality' && <QualityArtifact workOrder={activeWorkOrder} />}
        {visibleKind === 'run' && <RunArtifact workOrder={activeWorkOrder} />}
        {visibleKind === 'files' && <FilesArtifact workOrder={activeWorkOrder} />}
      </div>
    </aside>
  );
};

import React, { useEffect, useState } from 'react';
import { useApp } from '../context/AppContext';
import { PipelineVersion, OPERATOR_LEVEL_MAP } from '../types';
import {
  Check,
  CheckCircle2,
  Info,
  Play,
  Sliders,
  Zap,
} from 'lucide-react';

export const PipelineComparisonView: React.FC = () => {
  const {
    activeWorkOrder,
    showToast,
    addAuditLog,
    approveCurrentPipeline,
    submitCurrentDatasetRun,
  } = useApp();

  const pipelines = activeWorkOrder?.candidatePipelines || [];
  const [selectedPipelineId, setSelectedPipelineId] = useState<string>('');
  const selectedPipeline = pipelines.find(p => p.id === selectedPipelineId) || pipelines[0];

  useEffect(() => {
    if (!selectedPipelineId && pipelines[0]) {
      setSelectedPipelineId(activeWorkOrder?.selectedPipelineId || pipelines[0].id);
    }
  }, [activeWorkOrder?.selectedPipelineId, pipelines, selectedPipelineId]);

  if (!activeWorkOrder) return null;

  const metricText = (
    value: number | null | undefined,
    suffix = '',
    prefix = '',
  ) => value === null || value === undefined ? '待后端试跑生成' : `${prefix}${value}${suffix}`;

  const handleApplyPipeline = async (pipe: PipelineVersion) => {
    setSelectedPipelineId(pipe.id);
    try {
      if (activeWorkOrder.waitingFor === 'pipeline_approval') {
        await approveCurrentPipeline(pipe.id);
        addAuditLog('APPROVE_PIPELINE_FROM_COMPARISON', 'PipelineVersion', pipe.id, `工单: ${activeWorkOrder.id}`);
        return;
      }
      if (activeWorkOrder.agentTurn?.state?.next_action === 'submit_dataset_run') {
        await submitCurrentDatasetRun();
        return;
      }
      showToast('已选中方案。当前后端还没有进入 Pipeline 审批或全量执行阶段。');
    } catch (error) {
      showToast(`方案操作失败：${error instanceof Error ? error.message : String(error)}`);
    }
  };

  if (!pipelines.length) {
    return (
      <div className="bg-white rounded-2xl border border-slate-200/80 shadow-sm p-8 space-y-4">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 rounded-xl bg-amber-50 text-amber-700 border border-amber-200 flex items-center justify-center">
            <Info className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-base font-bold text-slate-900">还没有可对比的 3 套 Pipeline 方案</h2>
            <p className="text-xs text-slate-500 mt-1">
              主 Agent 动作：{activeWorkOrder.mainAgentAction || '未同步'}；等待项：{activeWorkOrder.waitingFor || '无'}。
            </p>
          </div>
        </div>
        <p className="text-sm text-slate-600 leading-relaxed bg-slate-50 border border-slate-100 rounded-xl p-4">
          本页只展示后端真实返回的候选方案。请先在 Chat 里提供完整需求和本地数据源路径，确认 TaskSpec 后，主 Agent 才会调度检索 Agent 与数据处理 Agent 生成三套候选 Pipeline。
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div className="bg-white p-5 rounded-2xl border border-slate-200/80 shadow-sm flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center space-x-2">
            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-100 text-emerald-800 uppercase">
              Pipeline 候选对比
            </span>
            <span className="text-xs text-slate-400">
              指标来源：后端 AgentTurn / PipelineVersion.metrics
            </span>
          </div>
          <h2 className="text-lg font-bold text-slate-900 mt-1">
            3 套方案试跑对比
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            这里只展示后端返回的候选流水线和真实 metrics；没有 metrics 时会明确标记为待生成。
          </p>
        </div>

        <div className="flex items-center space-x-2">
          <span className="text-xs text-slate-500">当前方案:</span>
          <span className="font-bold text-emerald-800 text-xs bg-emerald-50 px-3 py-1.5 rounded-xl border border-emerald-200">
            {selectedPipeline?.name || '-'}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {pipelines.map((pipe) => {
          const isSelected = selectedPipelineId === pipe.id;
          const badgeColor = pipe.candidateType === 'quality_first'
            ? 'bg-cyan-100 text-cyan-900'
            : pipe.candidateType === 'retention_first'
              ? 'bg-teal-100 text-teal-900'
              : 'bg-emerald-100 text-emerald-900';
          const borderHighlight = isSelected
            ? 'border-emerald-500 ring-2 ring-emerald-500/20'
            : 'border-slate-200';

          return (
            <div
              key={pipe.id}
              className={`bg-white rounded-2xl border p-5 transition-all shadow-sm flex flex-col justify-between ${borderHighlight}`}
            >
              <div className="space-y-4">
                <div className="flex items-start justify-between">
                  <div>
                    <span className={`px-2.5 py-0.5 rounded-full text-[10px] font-bold ${badgeColor}`}>
                      {pipe.candidateType === 'retention_first' && '保留优先'}
                      {pipe.candidateType === 'balanced' && '均衡方案'}
                      {pipe.candidateType === 'quality_first' && '质量优先'}
                    </span>
                    <h3 className="font-bold text-slate-900 text-base mt-2">{pipe.name}</h3>
                  </div>
                  {isSelected && (
                    <div className="w-6 h-6 rounded-full bg-emerald-600 text-white flex items-center justify-center shrink-0">
                      <Check className="w-4 h-4" />
                    </div>
                  )}
                </div>

                <p className="text-xs text-slate-600 leading-relaxed bg-slate-50 p-3 rounded-xl border border-slate-100">
                  {pipe.summary}
                </p>

                <div className="space-y-2 text-xs divide-y divide-slate-100">
                  <div className="flex justify-between gap-3 pt-1">
                    <span className="text-slate-500">样本保留率</span>
                    <span className="font-bold text-emerald-700 text-right">{metricText(pipe.retentionRatePct, '%')}</span>
                  </div>
                  <div className="flex justify-between gap-3 pt-2">
                    <span className="text-slate-500">预估模型准确率</span>
                    <span className="font-bold text-emerald-700 text-right">{metricText(pipe.modelScoreEstimate)}</span>
                  </div>
                  <div className="flex justify-between gap-3 pt-2">
                    <span className="text-slate-500">单图平均延迟 p95</span>
                    <span className="font-medium text-slate-800 text-right">{metricText(pipe.avgLatencyMs, ' ms')}</span>
                  </div>
                  <div className="flex justify-between gap-3 pt-2">
                    <span className="text-slate-500">千张处理成本</span>
                    <span className="font-semibold text-slate-900 text-right">{metricText(pipe.estimatedCostPer1k, '', '¥ ')}</span>
                  </div>
                </div>

                {pipe.metricsSource === 'unavailable' && (
                  <div className="text-[11px] text-amber-700 bg-amber-50 border border-amber-100 rounded-lg p-2">
                    后端暂未返回受控试跑指标，本页不会展示前端估算值。
                  </div>
                )}

                <div className="space-y-1.5 pt-2">
                  <div className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
                    算子节点链 ({pipe.nodes.length} 个节点)
                  </div>
                  <div className="space-y-1">
                    {pipe.nodes.map((node, index) => {
                      const levelInfo = OPERATOR_LEVEL_MAP[node.level];
                      return (
                        <div key={node.id} className="flex items-center space-x-2 text-xs bg-slate-50 p-2 rounded-lg border border-slate-100">
                          <span className="text-[10px] font-mono text-slate-400">#{index + 1}</span>
                          <span className={`px-1.5 py-0.5 rounded text-[9px] font-semibold ${levelInfo.badgeBg} ${levelInfo.badgeText}`}>
                            {node.level}
                          </span>
                          <span className="font-medium text-slate-800 truncate">{node.operatorName}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>

              <button
                onClick={() => handleApplyPipeline(pipe)}
                className={`w-full mt-6 py-2.5 px-4 rounded-xl text-xs font-bold transition-all shadow-sm ${
                  isSelected
                    ? 'bg-emerald-600 text-white'
                    : 'bg-slate-100 hover:bg-emerald-600 text-slate-700 hover:text-white'
                }`}
              >
                <span className="inline-flex items-center justify-center gap-1.5">
                  {activeWorkOrder.agentTurn?.state?.next_action === 'submit_dataset_run'
                    ? <Play className="w-3.5 h-3.5" />
                    : <CheckCircle2 className="w-3.5 h-3.5" />}
                  {activeWorkOrder.waitingFor === 'pipeline_approval'
                    ? '选择此方案'
                    : activeWorkOrder.agentTurn?.state?.next_action === 'submit_dataset_run'
                      ? '提交全量执行'
                      : '选中此方案'}
                </span>
              </button>
            </div>
          );
        })}
      </div>

      <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 space-y-4">
        <div className="flex items-center gap-2">
          <Zap className="w-5 h-5 text-emerald-600" />
          <h3 className="text-base font-bold text-slate-900">节点预览</h3>
        </div>
        {activeWorkOrder.nodePreviews?.length ? (
          <p className="text-xs text-slate-500">
            后端已返回节点预览集，可在后续版本继续展开逐图对比。
          </p>
        ) : (
          <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 text-sm text-slate-600 flex items-start gap-2">
            <Sliders className="w-4 h-4 text-slate-400 mt-0.5 shrink-0" />
            <span>
              当前后端还没有生成 NodePreviewSet。这里只保留真实状态，不再展示旧 mock 图片结果。
            </span>
          </div>
        )}
      </div>
    </div>
  );
};

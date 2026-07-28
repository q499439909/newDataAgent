import React from 'react';
import { useApp } from '../context/AppContext';
import { FileCheck, Activity, Layers, CheckCircle, AlertCircle, ShieldAlert, RotateCcw } from 'lucide-react';

export const TaskSpecDetailView: React.FC = () => {
  const { activeWorkOrder, triggerIncrementalRerun } = useApp();

  if (!activeWorkOrder) return null;

  const spec = activeWorkOrder.currentTaskSpec;
  const feedback = activeWorkOrder.modelFeedback;

  return (
    <div className="space-y-6">
      {/* Header Banner */}
      <div className="bg-white p-5 rounded-2xl border border-slate-200/80 shadow-2xs flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center space-x-2">
            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-100 text-emerald-800 uppercase">
              TaskSpec v1.2
            </span>
            <span className="text-xs text-slate-400">工单 ID: {activeWorkOrder.id}</span>
          </div>
          <h2 className="text-lg font-bold text-slate-900 mt-1">
            形式化需求规格说明书与约束
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            数据任务规划 Agent（主 Agent）导出的标准化 TaskSpec、约束、能力需求与验收指标。
          </p>
        </div>

        <div className="flex items-center space-x-2">
          <span className="text-xs text-slate-500">TaskSpec 状态:</span>
          <span className="font-bold text-emerald-800 text-xs bg-emerald-50 px-3 py-1 rounded-xl border border-emerald-200">
            已人工确认冻结
          </span>
        </div>
      </div>

      {/* Task Target Description */}
      <div className="bg-white p-5 rounded-2xl border border-slate-200/80 shadow-2xs space-y-3">
        <h3 className="text-xs font-bold text-slate-900 uppercase tracking-wider flex items-center space-x-2">
          <Activity className="w-4 h-4 text-emerald-600" />
          <span>业务目标与场景要求</span>
        </h3>
        <p className="text-xs text-slate-700 leading-relaxed bg-slate-50 p-3.5 rounded-xl border border-slate-200/60 font-medium">
          {activeWorkOrder.targetDescription}
        </p>
      </div>

      {/* Hard Constraints & Target Metrics Grid */}
      {spec && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Hard Constraints Card */}
          <div className="bg-white p-5 rounded-2xl border border-slate-200/80 shadow-2xs space-y-3">
            <div className="flex items-center space-x-2 border-b border-slate-100 pb-2.5">
              <ShieldAlert className="w-4 h-4 text-emerald-600" />
              <h3 className="text-xs font-bold text-slate-900 uppercase tracking-wider">
                硬性约束 (Hard Constraints)
              </h3>
            </div>
            <ul className="space-y-2 text-xs text-slate-700">
              {spec.hardConstraints.map((hc, i) => (
                <li key={i} className="flex items-start space-x-2 bg-slate-50 p-2.5 rounded-lg border border-slate-100">
                  <CheckCircle className="w-3.5 h-3.5 text-emerald-600 mt-0.5 shrink-0" />
                  <span className="leading-relaxed">{hc}</span>
                </li>
              ))}
            </ul>
          </div>

          {/* Acceptance Target Metrics */}
          <div className="bg-white p-5 rounded-2xl border border-slate-200/80 shadow-2xs space-y-3">
            <div className="flex items-center space-x-2 border-b border-slate-100 pb-2.5">
              <FileCheck className="w-4 h-4 text-emerald-600" />
              <h3 className="text-xs font-bold text-slate-900 uppercase tracking-wider">
                模型验收指标 (Acceptance Metrics)
              </h3>
            </div>
            <div className="space-y-2">
              {spec.targetMetrics.map((m, i) => (
                <div key={i} className="flex items-center justify-between bg-slate-50 p-2.5 rounded-lg border border-slate-100 text-xs">
                  <span className="text-slate-600 font-medium">{m.metric}</span>
                  <span className="font-bold text-emerald-800 bg-white px-2.5 py-1 rounded border border-emerald-100">
                    {m.targetValue}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Model Feedback Rework Section if present */}
      {feedback && (
        <div className="bg-amber-50/80 p-5 rounded-2xl border border-amber-200 shadow-2xs space-y-3">
          <div className="flex items-center justify-between border-b border-amber-200/80 pb-2.5">
            <div className="flex items-center space-x-2">
              <AlertCircle className="w-4 h-4 text-amber-600" />
              <span className="font-bold text-amber-950 text-xs">
                ModelFeedback 异常归因与推荐处理 (Loop Supervisor)
              </span>
            </div>
            <span className="px-2 py-0.5 rounded text-[10px] font-mono font-bold bg-amber-200 text-amber-900">
              {feedback.attributionCode}
            </span>
          </div>

          <p className="text-xs text-amber-900 leading-relaxed font-medium">
            {feedback.recommendedAction}
          </p>

          <div className="flex items-center justify-between pt-1">
            <span className="text-[11px] text-amber-800">
              采用增量局部返工机制，仅重跑对应切片算子。
            </span>
            <button
              onClick={() => triggerIncrementalRerun(activeWorkOrder.id)}
              className="flex items-center space-x-1.5 bg-amber-600 hover:bg-amber-700 text-white px-3.5 py-1.5 rounded-xl text-xs font-semibold shadow-xs transition-all active:scale-95"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              <span>执行增量局部返工</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

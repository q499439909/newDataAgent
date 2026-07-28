import React from 'react';
import { useApp } from '../context/AppContext';
import { ShieldCheck, GitCommit, FileText, Clock, Layers, User as UserIcon } from 'lucide-react';

export const AuditHistoryView: React.FC = () => {
  const { auditLogs, activeWorkOrder, currentUser } = useApp();
  const [filterMode, setFilterMode] = React.useState<'my' | 'all'>('my');

  const visibleLogs = auditLogs.filter(log => {
    if (filterMode === 'my') {
      return log.actor === currentUser.name || log.actor === '系统';
    }
    return true; // all
  });

  return (
    <div className="space-y-6">
      
      {/* Header Banner */}
      <div className="bg-white p-5 rounded-2xl border border-slate-200/80 shadow-2xs flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <div className="flex items-center space-x-2">
            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-100 text-emerald-800">
              操作存证
            </span>
            <span className="text-xs text-slate-400">当前视角日志: {visibleLogs.length} 条</span>
          </div>
          <h2 className="text-lg font-bold text-slate-900 mt-1">
            审计与版本血缘
          </h2>
          <p className="text-xs text-slate-500 mt-0.5">
            记录 TaskSpec 修订、Pipeline 选择、质检操作与系统返工等不可变变更日志。
          </p>
        </div>

        {/* Filter Toggle */}
        <div className="flex bg-slate-100 p-1 rounded-xl border border-slate-200/80 text-xs">
          <button
            onClick={() => setFilterMode('my')}
            className={`px-3 py-1.5 rounded-lg font-bold transition-all ${
              filterMode === 'my'
                ? 'bg-white text-emerald-900 shadow-xs'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            仅看我的操作 ({currentUser.name})
          </button>
          <button
            onClick={() => setFilterMode('all')}
            className={`px-3 py-1.5 rounded-lg font-bold transition-all ${
              filterMode === 'all'
                ? 'bg-white text-emerald-900 shadow-xs'
                : 'text-slate-600 hover:text-slate-900'
            }`}
          >
            全平台审计日志
          </button>
        </div>
      </div>

      {/* Active WorkOrder Revision Chain Timeline */}
      {activeWorkOrder && (
        <div className="bg-white p-6 rounded-2xl border border-emerald-100 shadow-sm space-y-4">
          <h3 className="font-bold text-slate-900 text-sm flex items-center space-x-2">
            <GitCommit className="w-4 h-4 text-emerald-600" />
            <span>当前工单版本修订单链 ({activeWorkOrder.name})</span>
          </h3>

          <div className="flex items-center space-x-3 overflow-x-auto py-2">
            {activeWorkOrder.revisionChain.map((rev, idx) => (
              <React.Fragment key={idx}>
                <div className="bg-emerald-50 border border-emerald-200 px-3.5 py-2 rounded-xl text-xs font-mono font-bold text-emerald-900 shrink-0">
                  {rev}
                </div>
                {idx < activeWorkOrder.revisionChain.length - 1 && (
                  <div className="text-emerald-400 font-bold shrink-0">→</div>
                )}
              </React.Fragment>
            ))}
          </div>
        </div>
      )}

      {/* Audit Logs Table */}
      <div className="bg-white rounded-2xl border border-emerald-100 shadow-sm overflow-hidden">
        <div className="p-4 bg-slate-50 border-b border-slate-100 font-bold text-slate-800 text-xs flex items-center justify-between">
          <span>平台安全与操作审计日志 (Audit Event Log)</span>
          <span className="text-slate-400 font-normal">匹配记录: {visibleLogs.length} 条</span>
        </div>

        {visibleLogs.length === 0 ? (
          <div className="p-8 text-center text-slate-400 text-xs space-y-1">
            <p>暂无与当前视角匹配的操作审计记录</p>
            <p className="text-[10px] text-slate-400">执行平台操作（如切换算法方案、新建工单、提发算子）后自动录入</p>
          </div>
        ) : (
          <div className="divide-y divide-slate-100 text-xs">
            {visibleLogs.map((log) => (
              <div key={log.id} className="p-4 hover:bg-slate-50 transition-colors flex items-start justify-between">
                <div className="space-y-1">
                  <div className="flex items-center space-x-2">
                    <span className="font-bold text-emerald-800 font-mono">{log.action}</span>
                    <span className="text-[10px] bg-slate-100 text-slate-600 px-2 py-0.5 rounded">
                      {log.targetType}: {log.targetId}
                    </span>
                  </div>
                  <p className="text-slate-600 text-xs">{log.details}</p>
                </div>

                <div className="text-right text-[11px] text-slate-400 shrink-0 space-y-1">
                  <div className="flex items-center space-x-1 justify-end">
                    <UserIcon className="w-3 h-3" />
                    <span>{log.actor}</span>
                  </div>
                  <div>{log.timestamp}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

    </div>
  );
};

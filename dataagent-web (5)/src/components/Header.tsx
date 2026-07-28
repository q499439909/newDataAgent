import React from 'react';
import { useApp } from '../context/AppContext';
import { 
  PanelLeft, 
  CheckCircle2, 
  Sliders, 
  ShieldAlert
} from 'lucide-react';

interface HeaderProps {
  activeTab: 'workorders' | 'operators' | 'audit';
  setActiveTab: (tab: 'workorders' | 'operators' | 'audit') => void;
  isSidebarOpen: boolean;
  setIsSidebarOpen: (open: boolean) => void;
  onOpenNewWorkOrderModal: () => void;
}

export const Header: React.FC<HeaderProps> = ({
  activeTab,
  setActiveTab,
  isSidebarOpen,
  setIsSidebarOpen,
}) => {
  const { 
    activeWorkOrder, 
    toastMessage 
  } = useApp();

  const getStageLabel = (stage?: string) => {
    switch (stage) {
      case 'completed': return { text: '已全量交付', bg: 'bg-emerald-100 text-emerald-800 border-emerald-300' };
      case 'evaluating': return { text: '评估返工阶段', bg: 'bg-amber-100 text-amber-800 border-amber-300' };
      case 'processing': return { text: '算子清洗运行中', bg: 'bg-sky-100 text-sky-800 border-sky-300' };
      case 'sampling': return { text: '难例切片采样中', bg: 'bg-purple-100 text-purple-800 border-purple-300' };
      case 'retrieval': return { text: '向量近邻扩展中', bg: 'bg-indigo-100 text-indigo-800 border-indigo-300' };
      case 'spec': return { text: 'TaskSpec 规则拟定中', bg: 'bg-slate-100 text-slate-800 border-slate-300' };
      default: return { text: '草稿与规划', bg: 'bg-slate-100 text-slate-700 border-slate-300' };
    }
  };

  const currentStageInfo = getStageLabel(activeWorkOrder?.currentStage);

  return (
    <header className="sticky top-0 z-20 bg-white/95 backdrop-blur-md border-b border-slate-200/80 shadow-2xs">
      {/* Global Toast Banner */}
      {toastMessage && (
        <div className="bg-emerald-800 text-emerald-50 px-4 py-1.5 text-xs font-semibold flex items-center justify-center space-x-2 animate-in fade-in slide-in-from-top-1 duration-200 shadow-inner">
          <CheckCircle2 className="w-4 h-4 text-emerald-300 shrink-0" />
          <span>{toastMessage}</span>
        </div>
      )}

      <div className="px-4 py-2.5 flex items-center justify-between gap-3">
        {/* Left Side: Work Order Title / Spec Version / Execution Status */}
        <div className="flex items-center space-x-3 min-w-0">
          
          {/* Re-open Sidebar Toggle Button (Only visible when sidebar is collapsed) */}
          {!isSidebarOpen && (
            <button
              onClick={() => setIsSidebarOpen(true)}
              className="p-1.5 rounded-xl text-slate-600 hover:text-emerald-800 hover:bg-slate-100 border border-slate-200 transition-all shrink-0 cursor-pointer"
              title="展开侧边栏"
            >
              <PanelLeft className="w-4 h-4" />
            </button>
          )}

          {activeTab === 'workorders' ? (
            activeWorkOrder ? (
              <div className="flex items-center space-x-2.5 min-w-0">
                <div className="min-w-0 truncate">
                  <h1 className="font-bold text-sm text-slate-900 truncate leading-tight">
                    {activeWorkOrder.name}
                  </h1>
                </div>

                {/* TaskSpec Version Badge */}
                <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-100 text-slate-700 border border-slate-200/80 font-mono shrink-0">
                  {activeWorkOrder.currentTaskSpec?.version || 'v1.0'}
                </span>

                {/* Execution Status Badge */}
                <span className={`px-2 py-0.5 rounded-full text-[10px] font-bold border shrink-0 ${currentStageInfo.bg}`}>
                  {currentStageInfo.text}
                </span>
              </div>
            ) : (
              <span className="text-xs font-bold text-slate-500">尚未选中或创建工单</span>
            )
          ) : activeTab === 'operators' ? (
            <div className="flex items-center space-x-2 font-bold text-slate-900 text-sm">
              <Sliders className="w-4 h-4 text-emerald-600" />
              <span>算子与 Pipeline 社区算子库</span>
            </div>
          ) : (
            <div className="flex items-center space-x-2 font-bold text-slate-900 text-sm">
              <ShieldAlert className="w-4 h-4 text-emerald-600" />
              <span>平台操作审计与 TaskSpec 版本血缘</span>
            </div>
          )}
        </div>
      </div>
    </header>
  );
};

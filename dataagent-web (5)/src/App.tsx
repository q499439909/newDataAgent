import React, { useState } from 'react';
import { AppProvider, useApp } from './context/AppContext';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { AuthPage } from './components/AuthPage';
import { NewWorkOrderModal } from './components/NewWorkOrderModal';
import { ChatDialogueAgentCockpit } from './components/ChatDialogueAgentCockpit';
import { PipelineComparisonView } from './components/PipelineComparisonView';
import { BoundarySampleReviewer } from './components/BoundarySampleReviewer';
import { TaskSpecDetailView } from './components/TaskSpecDetailView';
import { OperatorLibraryView } from './components/OperatorLibraryView';
import { GlobalSearchModal } from './components/GlobalSearchModal';
import { OperatorDetailModal } from './components/OperatorDetailModal';
import { UploadOperatorModal } from './components/UploadOperatorModal';
import { TuiCockpitModal } from './components/TuiCockpitModal';
import { AuditHistoryView } from './components/AuditHistoryView';
import { 
  Bot, 
  Sliders, 
  ShieldAlert, 
  FileCheck, 
  ChevronRight, 
  Layers, 
  Plus,
  Info,
  Sparkles,
  ArrowRight
} from 'lucide-react';

function AppContent() {
  const { isAuthenticated, toastMessage, activeWorkOrder } = useApp();
  
  // Navigation & Subviews
  const [activeTab, setActiveTab] = useState<'workorders' | 'operators' | 'audit'>('workorders');
  const [subView, setSubView] = useState<'chat' | 'comparison' | 'boundary' | 'spec'>('chat');

  // Sidebar Open/Collapsed state
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);

  // New Work Order Modal
  const [isNewWorkOrderModalOpen, setIsNewWorkOrderModalOpen] = useState(false);

  // If unauthenticated, redirect to AuthPage
  if (!isAuthenticated) {
    return (
      <>
        {toastMessage && (
          <div className="fixed top-5 right-5 z-50 bg-slate-900 border border-emerald-500/40 text-emerald-300 px-4 py-3 rounded-xl shadow-2xl flex items-center space-x-2 text-xs font-semibold animate-in fade-in slide-in-from-top-2">
            <Info className="w-4 h-4 text-emerald-400" />
            <span>{toastMessage}</span>
          </div>
        )}
        <AuthPage />
      </>
    );
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-slate-50 text-slate-900 font-sans antialiased selection:bg-emerald-100 selection:text-emerald-900">
      
      {/* 1. Left Fixed Sidebar (ChatGPT style) */}
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        isSidebarOpen={isSidebarOpen}
        setIsSidebarOpen={setIsSidebarOpen}
        onOpenNewWorkOrderModal={() => setIsNewWorkOrderModalOpen(true)}
      />

      {/* 2. Main Workspace Right Area */}
      <div className="flex-1 flex flex-col h-screen overflow-y-auto min-w-0 bg-slate-50 relative">
        
        {/* Workspace Top Bar Header */}
        <Header
          activeTab={activeTab}
          setActiveTab={setActiveTab}
          isSidebarOpen={isSidebarOpen}
          setIsSidebarOpen={setIsSidebarOpen}
          onOpenNewWorkOrderModal={() => setIsNewWorkOrderModalOpen(true)}
        />

        {/* Main Work Area View Canvas */}
        <main className="flex-1 p-4 sm:p-6 lg:p-8 max-w-7xl mx-auto w-full">
          
          {/* TAB 1: WorkOrder Cockpit View */}
          {activeTab === 'workorders' && (
            <div className="space-y-6">
              
              {/* Sub-view Navigation Pills Header */}
              <div className="bg-white p-2 rounded-2xl border border-slate-200/80 shadow-2xs flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center space-x-1.5 overflow-x-auto custom-scrollbar p-0.5">
                  <button
                    onClick={() => setSubView('chat')}
                    className={`flex items-center space-x-2 px-3.5 py-2 rounded-xl text-xs font-bold transition-all ${
                      subView === 'chat'
                        ? 'bg-emerald-600 text-white shadow-md shadow-emerald-600/20'
                        : 'text-slate-600 hover:text-slate-900 hover:bg-slate-100'
                    }`}
                  >
                    <Bot className="w-4 h-4" />
                    <span>智能 Chat 对话与 Agent 试跑</span>
                  </button>

                  <button
                    onClick={() => setSubView('comparison')}
                    className={`flex items-center space-x-2 px-3.5 py-2 rounded-xl text-xs font-bold transition-all ${
                      subView === 'comparison'
                        ? 'bg-emerald-600 text-white shadow-md shadow-emerald-600/20'
                        : 'text-slate-600 hover:text-slate-900 hover:bg-slate-100'
                    }`}
                  >
                    <Sliders className="w-4 h-4" />
                    <span>3 套 Candidate 方案比对</span>
                  </button>

                  <button
                    onClick={() => setSubView('boundary')}
                    className={`flex items-center space-x-2 px-3.5 py-2 rounded-xl text-xs font-bold transition-all ${
                      subView === 'boundary'
                        ? 'bg-emerald-600 text-white shadow-md shadow-emerald-600/20'
                        : 'text-slate-600 hover:text-slate-900 hover:bg-slate-100'
                    }`}
                  >
                    <ShieldAlert className="w-4 h-4" />
                    <span>边界/争议样本人工审核</span>
                  </button>

                  <button
                    onClick={() => setSubView('spec')}
                    className={`flex items-center space-x-2 px-3.5 py-2 rounded-xl text-xs font-bold transition-all ${
                      subView === 'spec'
                        ? 'bg-emerald-600 text-white shadow-md shadow-emerald-600/20'
                        : 'text-slate-600 hover:text-slate-900 hover:bg-slate-100'
                    }`}
                  >
                    <FileCheck className="w-4 h-4" />
                    <span>TaskSpec 需求约束规格</span>
                  </button>
                </div>

                {activeWorkOrder && (
                  <div className="hidden md:flex items-center space-x-2 px-3 py-1 bg-slate-50 rounded-xl border border-slate-200/80 text-[11px] text-slate-500 font-mono">
                    <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                    <span>阶段: {activeWorkOrder.currentStage}</span>
                  </div>
                )}
              </div>

              {/* WorkOrder Canvas Display */}
              <div>
                {subView === 'chat' && (
                  <ChatDialogueAgentCockpit
                    onSelectPipelineComparison={() => setSubView('comparison')}
                    onSelectBoundaryReview={() => setSubView('boundary')}
                  />
                )}

                {subView === 'comparison' && (
                  <PipelineComparisonView />
                )}

                {subView === 'boundary' && (
                  <BoundarySampleReviewer
                    onComplete={() => setSubView('chat')}
                  />
                )}

                {subView === 'spec' && (
                  <TaskSpecDetailView />
                )}
              </div>

            </div>
          )}

          {/* TAB 2: Operator Asset Library */}
          {activeTab === 'operators' && (
            <OperatorLibraryView />
          )}

          {/* TAB 3: Audit & Lineage History */}
          {activeTab === 'audit' && (
            <AuditHistoryView />
          )}

        </main>
      </div>

      {/* Global Modals */}
      <NewWorkOrderModal
        isOpen={isNewWorkOrderModalOpen}
        onClose={() => setIsNewWorkOrderModalOpen(false)}
        onCreated={() => setActiveTab('workorders')}
      />
      <GlobalSearchModal />
      <OperatorDetailModal />
      <UploadOperatorModal />
      <TuiCockpitModal />

    </div>
  );
}

export default function App() {
  return (
    <AppProvider>
      <AppContent />
    </AppProvider>
  );
}

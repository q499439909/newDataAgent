import React, { useEffect, useState } from 'react';
import { AppProvider, useApp } from './context/AppContext';
import { Sidebar } from './components/Sidebar';
import { Header } from './components/Header';
import { AuthPage } from './components/AuthPage';
import { NewWorkOrderModal } from './components/NewWorkOrderModal';
import { ChatDialogueAgentCockpit } from './components/ChatDialogueAgentCockpit';
import { ArtifactKind, ArtifactPanel } from './components/ArtifactPanel';
import { OperatorLibraryView } from './components/OperatorLibraryView';
import { GlobalSearchModal } from './components/GlobalSearchModal';
import { OperatorDetailModal } from './components/OperatorDetailModal';
import { UploadOperatorModal } from './components/UploadOperatorModal';
import { TuiCockpitModal } from './components/TuiCockpitModal';
import { AuditHistoryView } from './components/AuditHistoryView';
import { Info } from 'lucide-react';

function AppContent() {
  const { isAuthenticated, toastMessage, activeWorkOrder } = useApp();
  const [activeTab, setActiveTab] = useState<'workorders' | 'operators' | 'audit'>('workorders');
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const [isNewWorkOrderModalOpen, setIsNewWorkOrderModalOpen] = useState(false);
  const [artifactKind, setArtifactKind] = useState<ArtifactKind>('task_spec');
  const [isArtifactOpen, setIsArtifactOpen] = useState(false);
  const [isArtifactFullscreen, setIsArtifactFullscreen] = useState(false);
  const [artifactWidth, setArtifactWidth] = useState(460);
  const [isResizingArtifact, setIsResizingArtifact] = useState(false);

  const openArtifact = (kind: ArtifactKind) => {
    setArtifactKind(kind);
    setIsArtifactOpen(true);
    setActiveTab('workorders');
  };

  useEffect(() => {
    if (!isResizingArtifact) return;

    const handleMouseMove = (event: MouseEvent) => {
      const nextWidth = Math.min(Math.max(window.innerWidth - event.clientX, 360), Math.min(820, window.innerWidth - 360));
      setArtifactWidth(nextWidth);
    };
    const handleMouseUp = () => setIsResizingArtifact(false);

    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [isResizingArtifact]);

  if (!isAuthenticated) {
    return (
      <>
        {toastMessage && (
          <div className="fixed top-5 right-5 z-50 flex items-center space-x-2 rounded-xl border border-emerald-500/40 bg-slate-900 px-4 py-3 text-xs font-semibold text-emerald-300 shadow-2xl">
            <Info className="h-4 w-4 text-emerald-400" />
            <span>{toastMessage}</span>
          </div>
        )}
        <AuthPage />
      </>
    );
  }

  const showArtifactPanel = activeTab === 'workorders' && isArtifactOpen && activeWorkOrder;

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-slate-50 font-sans text-slate-900 antialiased selection:bg-emerald-100 selection:text-emerald-900">
      <Sidebar
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        isSidebarOpen={isSidebarOpen}
        setIsSidebarOpen={setIsSidebarOpen}
        onOpenNewWorkOrderModal={() => setIsNewWorkOrderModalOpen(true)}
      />

      <div className="relative flex h-screen min-w-0 flex-1 flex-col overflow-hidden bg-slate-50">
        <Header
          activeTab={activeTab}
          setActiveTab={setActiveTab}
          isSidebarOpen={isSidebarOpen}
          setIsSidebarOpen={setIsSidebarOpen}
          onOpenNewWorkOrderModal={() => setIsNewWorkOrderModalOpen(true)}
        />

        <main className="min-h-0 flex-1 overflow-hidden">
          {activeTab === 'workorders' && (
            <div className="flex h-full min-w-0">
              <section className="min-w-0 flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8">
                <div className="mx-auto h-full w-full max-w-6xl">
                  <ChatDialogueAgentCockpit onOpenArtifact={openArtifact} />
                </div>
              </section>

              {showArtifactPanel && !isArtifactFullscreen && (
                <>
                  <div
                    role="separator"
                    aria-orientation="vertical"
                    title="拖动调整 Artifact Panel 宽度"
                    onMouseDown={() => setIsResizingArtifact(true)}
                    className="hidden w-1.5 cursor-col-resize bg-slate-200 hover:bg-emerald-400 lg:block"
                  />
                  <div className="hidden min-w-[360px] shrink-0 lg:block" style={{ width: artifactWidth }}>
                    <ArtifactPanel
                      activeKind={artifactKind}
                      onSelectKind={setArtifactKind}
                      onClose={() => setIsArtifactOpen(false)}
                      isFullscreen={false}
                      onToggleFullscreen={() => setIsArtifactFullscreen(true)}
                    />
                  </div>
                  <div className="fixed inset-x-3 bottom-3 top-20 z-30 lg:hidden">
                    <ArtifactPanel
                      activeKind={artifactKind}
                      onSelectKind={setArtifactKind}
                      onClose={() => setIsArtifactOpen(false)}
                      isFullscreen={false}
                      onToggleFullscreen={() => setIsArtifactFullscreen(true)}
                    />
                  </div>
                </>
              )}

              {showArtifactPanel && isArtifactFullscreen && (
                <ArtifactPanel
                  activeKind={artifactKind}
                  onSelectKind={setArtifactKind}
                  onClose={() => {
                    setIsArtifactOpen(false);
                    setIsArtifactFullscreen(false);
                  }}
                  isFullscreen
                  onToggleFullscreen={() => setIsArtifactFullscreen(false)}
                />
              )}
            </div>
          )}

          {activeTab === 'operators' && (
            <div className="h-full overflow-y-auto p-4 sm:p-6 lg:p-8">
              <div className="mx-auto max-w-7xl">
                <OperatorLibraryView />
              </div>
            </div>
          )}

          {activeTab === 'audit' && (
            <div className="h-full overflow-y-auto p-4 sm:p-6 lg:p-8">
              <div className="mx-auto max-w-7xl">
                <AuditHistoryView />
              </div>
            </div>
          )}
        </main>
      </div>

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

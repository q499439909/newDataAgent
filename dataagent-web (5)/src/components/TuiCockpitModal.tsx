import React, { useState } from 'react';
import { useApp } from '../context/AppContext';
import { Terminal, X, Play, RefreshCw, Layers, ExternalLink, ShieldAlert } from 'lucide-react';

export const TuiCockpitModal: React.FC = () => {
  const { isTuiCockpitOpen, setIsTuiCockpitOpen, activeWorkOrder, auditLogs } = useApp();
  const [tuiInput, setTuiInput] = useState('');
  const [commandLogs, setCommandLogs] = useState<string[]>([
    'dataagent-tui v1.1.0 (LangGraph Runtime Connected)',
    '==================================================',
    `WorkOrder Active: ${activeWorkOrder?.id} [${activeWorkOrder?.name}]`,
    'LangGraph Thread: thread_lg_20260721_drive01',
    'Current Node: [3/5] PIPELINE_EXPERIMENTING',
    'Agent status: DemandPlanner(Ready), RetrievalAgent(Done), CurationAgent(Running)',
    'Type "help" for available commands or "approve" to advance interrupt node.'
  ]);

  if (!isTuiCockpitOpen) return null;

  const handleCommand = (e: React.FormEvent) => {
    e.preventDefault();
    if (!tuiInput.trim()) return;

    const cmd = tuiInput.trim();
    setTuiInput('');

    setCommandLogs(prev => [
      ...prev,
      `> ${cmd}`,
      cmd === 'help' ? 'Commands: status, spec, pipelines, approve, Reruns, exit' :
      cmd === 'status' ? `Stage: ${activeWorkOrder?.currentStage.toUpperCase()} | TaskSpec v1.2 Confirmed` :
      cmd === 'approve' ? '[LangGraph interrupt] Approved. Advancing to next evaluation node.' :
      `Executed action "${cmd}". Control Plane synchronized.`
    ]);
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-md flex items-center justify-center p-4 animate-in fade-in duration-200">
      <div className="bg-slate-900 rounded-2xl shadow-2xl border border-slate-800 w-full max-w-3xl h-[600px] flex flex-col overflow-hidden font-mono text-xs">
        
        {/* TUI Terminal Header */}
        <div className="px-4 py-3 bg-slate-950 border-b border-slate-800 flex items-center justify-between text-slate-400">
          <div className="flex items-center space-x-2">
            <div className="flex space-x-1.5">
              <div className="w-3 h-3 rounded-full bg-red-500/80" />
              <div className="w-3 h-3 rounded-full bg-amber-500/80" />
              <div className="w-3 h-3 rounded-full bg-emerald-500/80" />
            </div>
            <span className="text-slate-300 font-bold ml-2">DataAgent TUI Agentic Cockpit</span>
          </div>

          <div className="flex items-center space-x-3">
            <span className="text-[10px] bg-emerald-950 text-emerald-400 px-2 py-0.5 rounded border border-emerald-800">
              LangGraph Thread Alive
            </span>
            <button onClick={() => setIsTuiCockpitOpen(false)} className="hover:text-white">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Terminal Output Stream */}
        <div className="p-4 flex-1 overflow-y-auto space-y-1.5 text-emerald-400">
          {commandLogs.map((log, i) => (
            <div key={i} className={log.startsWith('>') ? 'text-white font-bold' : ''}>
              {log}
            </div>
          ))}
        </div>

        {/* Command Input Bar */}
        <form onSubmit={handleCommand} className="p-3 bg-slate-950 border-t border-slate-800 flex items-center space-x-2">
          <span className="text-emerald-500 font-bold">$</span>
          <input
            type="text"
            value={tuiInput}
            onChange={(e) => setTuiInput(e.target.value)}
            placeholder="输入 TUI 命令 (如: approve, status, spec)..."
            className="flex-1 bg-transparent text-white focus:outline-none"
            autoFocus
          />
        </form>

      </div>
    </div>
  );
};

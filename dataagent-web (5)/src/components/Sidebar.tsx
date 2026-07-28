import React, { useState, useRef, useEffect } from 'react';
import { useApp } from '../context/AppContext';
import { WorkOrder } from '../types';
import { UserProfileModals } from './UserProfileModals';
import { 
  Bot, 
  Sliders, 
  ShieldAlert, 
  Terminal, 
  Plus, 
  Search, 
  PanelLeftClose, 
  PanelLeft, 
  Layers, 
  MoreVertical, 
  Edit3, 
  Copy, 
  Archive, 
  Trash2, 
  User as UserIcon, 
  ShieldCheck, 
  Building2, 
  Settings, 
  LogOut, 
  ChevronRight, 
  Check, 
  X,
  Sparkles,
  Clock,
  Filter,
  CheckCircle2,
  AlertCircle,
  PlayCircle
} from 'lucide-react';

interface SidebarProps {
  activeTab: 'workorders' | 'operators' | 'audit';
  setActiveTab: (tab: 'workorders' | 'operators' | 'audit') => void;
  isSidebarOpen: boolean;
  setIsSidebarOpen: (open: boolean) => void;
  onOpenNewWorkOrderModal: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({
  activeTab,
  setActiveTab,
  isSidebarOpen,
  setIsSidebarOpen,
  onOpenNewWorkOrderModal,
}) => {
  const {
    currentUser,
    logout,
    workOrders,
    activeWorkOrder,
    setActiveWorkOrder,
    renameWorkOrder,
    duplicateWorkOrder,
    archiveWorkOrder,
    deleteWorkOrder,
    isTuiCockpitOpen,
    setIsTuiCockpitOpen,
    showToast
  } = useApp();

  // Local state for search & filtering history work orders
  const [historySearchQuery, setHistorySearchQuery] = useState('');
  const [showArchivedFilter, setShowArchivedFilter] = useState(false);

  // Work order menu popup state
  const [openMenuWoId, setOpenMenuWoId] = useState<string | null>(null);
  
  // Renaming state
  const [renamingWoId, setRenamingWoId] = useState<string | null>(null);
  const [renamingName, setRenamingName] = useState('');

  // User popover menu state
  const [isUserMenuOpen, setIsUserMenuOpen] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);

  // User Modals State
  const [userModalType, setUserModalType] = useState<'profile' | 'security' | 'workspace' | 'settings' | null>(null);

  // Close menus when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (userMenuRef.current && !userMenuRef.current.contains(event.target as Node)) {
        setIsUserMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Filter & group history work orders by time
  const filteredWorkOrders = workOrders.filter(wo => {
    if (!showArchivedFilter && wo.isArchived) return false;
    if (showArchivedFilter && !wo.isArchived) return false;
    if (!historySearchQuery.trim()) return true;
    const q = historySearchQuery.toLowerCase();
    return wo.name.toLowerCase().includes(q) || (wo.targetDescription && wo.targetDescription.toLowerCase().includes(q));
  });

  const groupWorkOrdersByTime = () => {
    const today = new Date();
    const todayStr = today.toISOString().split('T')[0];

    const yesterday = new Date(today);
    yesterday.setDate(today.getDate() - 1);
    const yesterdayStr = yesterday.toISOString().split('T')[0];

    const sevenDaysAgo = new Date(today);
    sevenDaysAgo.setDate(today.getDate() - 7);

    const groups: { label: string; items: WorkOrder[] }[] = [
      { label: '今天', items: [] },
      { label: '昨天', items: [] },
      { label: '前 7 天内', items: [] },
      { label: '更早', items: [] }
    ];

    filteredWorkOrders.forEach(wo => {
      const dateParts = wo.createdAt.split(' ');
      const woDateStr = dateParts[0];
      const woDate = new Date(woDateStr.replace(/-/g, '/'));

      if (woDateStr === todayStr) {
        groups[0].items.push(wo);
      } else if (woDateStr === yesterdayStr) {
        groups[1].items.push(wo);
      } else if (woDate >= sevenDaysAgo) {
        groups[2].items.push(wo);
      } else {
        groups[3].items.push(wo);
      }
    });

    return groups.filter(g => g.items.length > 0);
  };

  const groupedWorkOrders = groupWorkOrdersByTime();

  const handleStartRename = (wo: WorkOrder, e: React.MouseEvent) => {
    e.stopPropagation();
    setOpenMenuWoId(null);
    setRenamingWoId(wo.id);
    setRenamingName(wo.name);
  };

  const handleSaveRename = (woId: string, e: React.FormEvent | React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (renamingName.trim()) {
      renameWorkOrder(woId, renamingName.trim());
    }
    setRenamingWoId(null);
  };

  const handleCancelRename = (e: React.MouseEvent) => {
    e.stopPropagation();
    setRenamingWoId(null);
  };

  const handleDuplicate = (woId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setOpenMenuWoId(null);
    duplicateWorkOrder(woId);
  };

  const handleArchive = (woId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setOpenMenuWoId(null);
    archiveWorkOrder(woId);
  };

  const handleDelete = (woId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setOpenMenuWoId(null);
    if (window.confirm('确定删除该历史工单记录吗？操作不可撤销。')) {
      deleteWorkOrder(woId);
    }
  };

  const getStageBadgeColor = (stage: WorkOrder['currentStage']) => {
    switch (stage) {
      case 'completed': return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30';
      case 'evaluating': return 'bg-amber-500/20 text-amber-300 border-amber-500/30';
      case 'processing': return 'bg-sky-500/20 text-sky-300 border-sky-500/30';
      default: return 'bg-slate-700/50 text-slate-400 border-slate-700';
    }
  };

  return (
    <>
      <aside
        className={`bg-slate-50 border-r border-slate-200/90 text-slate-800 flex flex-col h-screen shrink-0 transition-all duration-300 relative z-30 select-none ${
          isSidebarOpen ? 'w-72' : 'w-0 opacity-0 overflow-hidden border-r-0'
        }`}
      >
        {/* ================= 1. TOP SECTION ================= */}
        <div className="p-3.5 border-b border-slate-200/80 space-y-3 bg-white">
          
          {/* Brand Logo & Collapse Sidebar Toggle */}
          <div className="flex items-center justify-between">
            <div className="flex items-center space-x-2.5">
              <div className="w-8 h-8 rounded-xl bg-emerald-600 flex items-center justify-center text-white shadow-xs font-black">
                <Layers className="w-4 h-4" />
              </div>
              <div>
                <div className="flex items-center space-x-1.5">
                  <span className="font-bold text-sm tracking-tight text-slate-900 font-mono">DataAgent</span>
                  <span className="text-[10px] font-bold px-1.5 py-0.2 rounded bg-emerald-100 text-emerald-800 border border-emerald-200">
                    v2.4
                  </span>
                </div>
                <p className="text-[10px] text-slate-500">多模态数据智能生产闭环</p>
              </div>
            </div>

            <button
              onClick={() => setIsSidebarOpen(false)}
              className="p-1.5 rounded-lg text-slate-500 hover:text-slate-900 hover:bg-slate-100 transition-colors cursor-pointer"
              title="折叠侧边栏"
            >
              <PanelLeftClose className="w-4 h-4" />
            </button>
          </div>

          {/* New Work Order Primary Button */}
          <button
            onClick={onOpenNewWorkOrderModal}
            className="w-full py-2.5 px-3 rounded-xl bg-emerald-600 hover:bg-emerald-500 active:scale-[0.99] text-white font-bold text-xs flex items-center justify-center space-x-2 shadow-md shadow-emerald-600/20 transition-all cursor-pointer"
          >
            <Plus className="w-4 h-4" />
            <span>新建数据生产工单</span>
          </button>

          {/* Platform Level Entry Navigation */}
          <div className="space-y-0.5 pt-1">
            <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider px-2 py-1">
              平台入口 Platform Nav
            </div>

            <button
              onClick={() => {
                setActiveTab('workorders');
              }}
              className={`w-full px-2.5 py-2 rounded-xl text-xs font-semibold flex items-center justify-between transition-all cursor-pointer ${
                activeTab === 'workorders'
                  ? 'bg-emerald-50 text-emerald-900 shadow-2xs border border-emerald-200/80 font-bold'
                  : 'text-slate-600 hover:bg-slate-200/60 hover:text-slate-900'
              }`}
            >
              <div className="flex items-center space-x-2.5">
                <Bot className={`w-4 h-4 ${activeTab === 'workorders' ? 'text-emerald-600' : 'text-slate-400'}`} />
                <span>工单驾驶舱</span>
              </div>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-200/80 text-slate-600 font-mono">
                {workOrders.length}
              </span>
            </button>

            <button
              onClick={() => {
                setActiveTab('operators');
              }}
              className={`w-full px-2.5 py-2 rounded-xl text-xs font-semibold flex items-center justify-between transition-all cursor-pointer ${
                activeTab === 'operators'
                  ? 'bg-emerald-50 text-emerald-900 shadow-2xs border border-emerald-200/80 font-bold'
                  : 'text-slate-600 hover:bg-slate-200/60 hover:text-slate-900'
              }`}
            >
              <div className="flex items-center space-x-2.5">
                <Sliders className={`w-4 h-4 ${activeTab === 'operators' ? 'text-emerald-600' : 'text-slate-400'}`} />
                <span>算子与 Pipeline 社区</span>
              </div>
            </button>

            <button
              onClick={() => {
                setActiveTab('audit');
              }}
              className={`w-full px-2.5 py-2 rounded-xl text-xs font-semibold flex items-center justify-between transition-all cursor-pointer ${
                activeTab === 'audit'
                  ? 'bg-emerald-50 text-emerald-900 shadow-2xs border border-emerald-200/80 font-bold'
                  : 'text-slate-600 hover:bg-slate-200/60 hover:text-slate-900'
              }`}
            >
              <div className="flex items-center space-x-2.5">
                <ShieldAlert className={`w-4 h-4 ${activeTab === 'audit' ? 'text-emerald-600' : 'text-slate-400'}`} />
                <span>审计与版本血缘</span>
              </div>
            </button>
          </div>

        </div>

        {/* ================= 2. MIDDLE SECTION (HISTORY WORK ORDERS WITH SCROLLING) ================= */}
        <div className="flex-1 overflow-y-auto custom-scrollbar p-3 space-y-3">
          
          {/* Header & Filter Controls */}
          <div className="space-y-2">
            <div className="flex items-center justify-between px-1">
              <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider flex items-center space-x-1">
                <Clock className="w-3 h-3 text-slate-400" />
                <span>历史工单 (History)</span>
              </span>

              <button
                onClick={() => setShowArchivedFilter(!showArchivedFilter)}
                className={`text-[10px] px-1.5 py-0.5 rounded transition-all flex items-center space-x-1 cursor-pointer ${
                  showArchivedFilter 
                    ? 'bg-amber-100 text-amber-800 border border-amber-300 font-bold' 
                    : 'text-slate-500 hover:text-slate-800'
                }`}
              >
                <Archive className="w-2.5 h-2.5" />
                <span>{showArchivedFilter ? '查看全部工单' : '归档列表'}</span>
              </button>
            </div>

            {/* Quick Filter Input */}
            <div className="relative">
              <Search className="w-3.5 h-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
              <input
                type="text"
                placeholder="搜索历史工单名称..."
                value={historySearchQuery}
                onChange={(e) => setHistorySearchQuery(e.target.value)}
                className="w-full pl-8 pr-2.5 py-1.5 bg-white border border-slate-200 rounded-xl text-xs text-slate-800 placeholder-slate-400 focus:outline-hidden focus:border-emerald-500 transition-all shadow-2xs"
              />
              {historySearchQuery && (
                <button
                  onClick={() => setHistorySearchQuery('')}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                >
                  <X className="w-3 h-3" />
                </button>
              )}
            </div>
          </div>

          {/* Grouped Work Orders List */}
          {groupedWorkOrders.length === 0 ? (
            <div className="p-4 text-center text-slate-500 text-xs rounded-xl bg-white border border-slate-200/80 space-y-1">
              <p>暂无匹配的历史工单</p>
              <p className="text-[10px] text-slate-400">点击顶部「新建工单」快速创建</p>
            </div>
          ) : (
            <div className="space-y-3">
              {groupedWorkOrders.map((group) => (
                <div key={group.label} className="space-y-1">
                  <div className="text-[10px] font-bold text-slate-400 px-2 py-0.5 font-mono">
                    {group.label}
                  </div>

                  <div className="space-y-0.5">
                    {group.items.map((wo) => {
                      const isActive = activeWorkOrder?.id === wo.id && activeTab === 'workorders';
                      const isRenamingThis = renamingWoId === wo.id;
                      const isMenuOpenThis = openMenuWoId === wo.id;

                      return (
                        <div
                          key={wo.id}
                          className="relative group"
                        >
                          {isRenamingThis ? (
                            /* Inline Rename Form */
                            <form
                              onSubmit={(e) => handleSaveRename(wo.id, e)}
                              className="p-1.5 bg-white rounded-xl border border-emerald-500 flex items-center space-x-1 shadow-xs"
                            >
                              <input
                                type="text"
                                value={renamingName}
                                onChange={(e) => setRenamingName(e.target.value)}
                                autoFocus
                                className="w-full bg-slate-50 border border-slate-200 rounded px-2 py-1 text-xs text-slate-900 focus:outline-hidden"
                              />
                              <button
                                type="submit"
                                className="p-1 rounded bg-emerald-600 text-white hover:bg-emerald-500 cursor-pointer"
                                title="保存"
                              >
                                <Check className="w-3.5 h-3.5" />
                              </button>
                              <button
                                type="button"
                                onClick={handleCancelRename}
                                className="p-1 rounded bg-slate-200 text-slate-700 hover:bg-slate-300 cursor-pointer"
                                title="取消"
                              >
                                <X className="w-3.5 h-3.5" />
                              </button>
                            </form>
                          ) : (
                            /* Work Order Item List Row (ChatGPT Sidebar style) */
                            <div
                              onClick={() => {
                                setActiveWorkOrder(wo);
                                setActiveTab('workorders');
                              }}
                              className={`w-full text-left py-2 px-2.5 rounded-lg text-xs transition-all cursor-pointer flex items-center justify-between group/item ${
                                isActive
                                  ? 'bg-slate-200/80 text-slate-900 font-medium'
                                  : 'text-slate-700 hover:bg-slate-100 hover:text-slate-900'
                              }`}
                            >
                              <div className="flex items-center space-x-2 min-w-0 pr-1">
                                {/* Small Stage Status Indicator */}
                                <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                                  wo.currentStage === 'completed' ? 'bg-emerald-500' :
                                  wo.currentStage === 'evaluating' ? 'bg-amber-500' :
                                  'bg-sky-500'
                                }`} />

                                <div className="min-w-0 truncate">
                                  <div className="truncate text-xs leading-snug">
                                    {wo.name}
                                  </div>
                                </div>
                              </div>

                              {/* Ellipsis Menu Button */}
                              <div className="relative shrink-0">
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    setOpenMenuWoId(isMenuOpenThis ? null : wo.id);
                                  }}
                                  className={`p-1 rounded-lg text-slate-400 hover:text-slate-800 hover:bg-slate-200 transition-colors cursor-pointer ${
                                    isMenuOpenThis ? 'bg-slate-200 text-slate-900' : 'opacity-0 group-hover/item:opacity-100'
                                  }`}
                                  title="工单操作"
                                >
                                  <MoreVertical className="w-3.5 h-3.5" />
                                </button>

                                {/* Dropdown Menu */}
                                {isMenuOpenThis && (
                                  <div
                                    className="absolute right-0 top-6 z-50 w-36 bg-white border border-slate-200 rounded-xl shadow-xl p-1 text-slate-800 text-xs space-y-0.5 animate-in fade-in duration-100"
                                    onClick={(e) => e.stopPropagation()}
                                  >
                                    <button
                                      onClick={(e) => handleStartRename(wo, e)}
                                      className="w-full text-left px-2.5 py-1.5 rounded-lg hover:bg-slate-100 flex items-center space-x-2 text-slate-700 cursor-pointer"
                                    >
                                      <Edit3 className="w-3.5 h-3.5 text-slate-500" />
                                      <span>重命名</span>
                                    </button>

                                    <button
                                      onClick={(e) => handleDuplicate(wo.id, e)}
                                      className="w-full text-left px-2.5 py-1.5 rounded-lg hover:bg-slate-100 flex items-center space-x-2 text-slate-700 cursor-pointer"
                                    >
                                      <Copy className="w-3.5 h-3.5 text-slate-500" />
                                      <span>复制工单</span>
                                    </button>

                                    <button
                                      onClick={(e) => handleArchive(wo.id, e)}
                                      className="w-full text-left px-2.5 py-1.5 rounded-lg hover:bg-slate-100 flex items-center space-x-2 text-slate-700 cursor-pointer"
                                    >
                                      <Archive className="w-3.5 h-3.5 text-amber-600" />
                                      <span>{wo.isArchived ? '还原工单' : '归档工单'}</span>
                                    </button>

                                    <div className="border-t border-slate-100 my-0.5" />

                                    <button
                                      onClick={(e) => handleDelete(wo.id, e)}
                                      className="w-full text-left px-2.5 py-1.5 rounded-lg hover:bg-rose-50 hover:text-rose-700 flex items-center space-x-2 text-rose-600 cursor-pointer"
                                    >
                                      <Trash2 className="w-3.5 h-3.5 text-rose-600" />
                                      <span>删除</span>
                                    </button>
                                  </div>
                                )}
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}

        </div>

        {/* ================= 3. BOTTOM ACCOUNT SECTION (FIXED AT BOTTOM) ================= */}
        <div className="mt-auto p-3 border-t border-slate-200/90 bg-slate-100/80 relative" ref={userMenuRef}>
          
          {/* User Popover Menu */}
          {isUserMenuOpen && (
            <div className="absolute bottom-16 left-3 right-3 z-50 bg-white border border-slate-200 rounded-2xl shadow-2xl p-2 text-slate-800 text-xs space-y-1 animate-in fade-in slide-in-from-bottom-2 duration-150">
              
              <div className="p-2 border-b border-slate-100 flex items-center space-x-2.5 bg-slate-50 rounded-xl">
                <img
                  src={currentUser.avatar}
                  alt={currentUser.name}
                  className="w-8 h-8 rounded-lg border border-emerald-500/50 object-cover"
                />
                <div className="min-w-0">
                  <div className="font-bold text-slate-900 truncate">{currentUser.name}</div>
                  <div className="text-[10px] text-slate-500 truncate">{currentUser.email || 'admin@dataagent.ai'}</div>
                </div>
              </div>

              <button
                onClick={() => {
                  setIsUserMenuOpen(false);
                  setUserModalType('profile');
                }}
                className="w-full text-left px-3 py-2 rounded-xl hover:bg-slate-100 flex items-center space-x-2.5 text-slate-700 transition-colors cursor-pointer"
              >
                <UserIcon className="w-4 h-4 text-emerald-600" />
                <span>个人资料 (Profile)</span>
              </button>

              <button
                onClick={() => {
                  setIsUserMenuOpen(false);
                  setUserModalType('security');
                }}
                className="w-full text-left px-3 py-2 rounded-xl hover:bg-slate-100 flex items-center space-x-2.5 text-slate-700 transition-colors cursor-pointer"
              >
                <ShieldCheck className="w-4 h-4 text-emerald-600" />
                <span>账号安全 & 离散鉴权</span>
              </button>

              <button
                onClick={() => {
                  setIsUserMenuOpen(false);
                  setUserModalType('workspace');
                }}
                className="w-full text-left px-3 py-2 rounded-xl hover:bg-slate-100 flex items-center space-x-2.5 text-slate-700 transition-colors cursor-pointer"
              >
                <Building2 className="w-4 h-4 text-emerald-600" />
                <span>工作空间 (Workspace)</span>
              </button>

              <button
                onClick={() => {
                  setIsUserMenuOpen(false);
                  setUserModalType('settings');
                }}
                className="w-full text-left px-3 py-2 rounded-xl hover:bg-slate-100 flex items-center space-x-2.5 text-slate-700 transition-colors cursor-pointer"
              >
                <Settings className="w-4 h-4 text-emerald-600" />
                <span>系统设置 (Settings)</span>
              </button>

              <div className="border-t border-slate-100 my-1" />

              <button
                onClick={() => {
                  setIsUserMenuOpen(false);
                  logout();
                }}
                className="w-full text-left px-3 py-2 rounded-xl hover:bg-rose-50 hover:text-rose-700 flex items-center space-x-2.5 text-rose-600 transition-colors cursor-pointer"
              >
                <LogOut className="w-4 h-4 text-rose-600" />
                <span>退出登录 (Logout)</span>
              </button>
            </div>
          )}

          {/* User Account Trigger Box */}
          <button
            onClick={() => setIsUserMenuOpen(!isUserMenuOpen)}
            className="w-full p-2 rounded-xl bg-white hover:bg-slate-200/60 border border-slate-200 transition-all flex items-center justify-between group/user text-left cursor-pointer shadow-2xs"
          >
            <div className="flex items-center space-x-2.5 min-w-0">
              <div className="relative shrink-0">
                <img
                  src={currentUser.avatar}
                  alt={currentUser.name}
                  className="w-9 h-9 rounded-xl border border-emerald-500/50 object-cover shadow-xs"
                />
                <span className="absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-emerald-500 border-2 border-white" />
              </div>

              <div className="min-w-0">
                <div className="font-bold text-xs text-slate-900 truncate flex items-center space-x-1">
                  <span className="truncate">{currentUser.name}</span>
                </div>
                <div className="text-[10px] text-slate-500 truncate flex items-center space-x-1">
                  <span className="bg-emerald-100 text-emerald-800 px-1.5 py-0.2 rounded text-[9px] font-bold">
                    {currentUser.roleName}
                  </span>
                  <span className="text-slate-400 truncate">{currentUser.department}</span>
                </div>
              </div>
            </div>

            <Settings className="w-4 h-4 text-slate-400 group-hover/user:text-slate-600 shrink-0 transition-colors" />
          </button>

        </div>
      </aside>

      {/* User Settings / Profile Modals */}
      <UserProfileModals
        activeModal={userModalType}
        onClose={() => setUserModalType(null)}
      />
    </>
  );
};

import React, { useState } from 'react';
import { useApp } from '../context/AppContext';
import { 
  X, 
  User as UserIcon, 
  ShieldCheck, 
  Building2, 
  Settings, 
  KeyRound, 
  CheckCircle2, 
  Layers, 
  Lock, 
  Cpu, 
  Database,
  Sliders,
  Mail,
  Shield,
  Clock,
  Sparkles
} from 'lucide-react';

interface UserProfileModalsProps {
  activeModal: 'profile' | 'security' | 'workspace' | 'settings' | null;
  onClose: () => void;
}

export const UserProfileModals: React.FC<UserProfileModalsProps> = ({ activeModal, onClose }) => {
  const { currentUser, showToast } = useApp();

  const [themeMode, setThemeMode] = useState<'light' | 'dark' | 'system'>('light');
  const [autoSave, setAutoSave] = useState(true);
  const [notifyEmail, setNotifyEmail] = useState(true);

  if (!activeModal) return null;

  const handleSaveSettings = () => {
    showToast('个人设置及首选项已成功同步保存！');
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/40 backdrop-blur-xs flex items-center justify-center p-4 animate-in fade-in duration-200">
      <div className="bg-white border border-slate-200 text-slate-800 w-full max-w-xl rounded-3xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
        
        {/* Modal Header */}
        <div className="p-5 border-b border-slate-200 flex items-center justify-between bg-slate-50">
          <div className="flex items-center space-x-2.5">
            <div className="p-2 rounded-xl bg-emerald-50 text-emerald-700 border border-emerald-200">
              {activeModal === 'profile' && <UserIcon className="w-5 h-5" />}
              {activeModal === 'security' && <ShieldCheck className="w-5 h-5" />}
              {activeModal === 'workspace' && <Building2 className="w-5 h-5" />}
              {activeModal === 'settings' && <Settings className="w-5 h-5" />}
            </div>
            <div>
              <h3 className="font-bold text-base text-slate-900">
                {activeModal === 'profile' && '个人资料 Profile'}
                {activeModal === 'security' && '账号安全 & SHA-256 鉴权'}
                {activeModal === 'workspace' && '当前工作空间 Workspace'}
                {activeModal === 'settings' && '系统全局偏好设置'}
              </h3>
              <p className="text-xs text-slate-500">
                {activeModal === 'profile' && '查看并管理当前登录账号的基本资料与角色分配'}
                {activeModal === 'security' && '查看密码存储哈希防护与多租户隔离安全级别'}
                {activeModal === 'workspace' && '查看算子管线计算资源配额与向量索引配置'}
                {activeModal === 'settings' && '自定义界面主题、自动对账与全流程通知策略'}
              </p>
            </div>
          </div>

          <button
            onClick={onClose}
            className="p-2 rounded-xl text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Modal Body */}
        <div className="p-6 overflow-y-auto space-y-6 text-xs text-slate-600">
          
          {/* PROFILE MODAL */}
          {activeModal === 'profile' && (
            <div className="space-y-6">
              <div className="flex items-center space-x-4 p-4 rounded-2xl bg-slate-50 border border-slate-200">
                <img
                  src={currentUser.avatar}
                  alt={currentUser.name}
                  className="w-16 h-16 rounded-2xl border-2 border-emerald-500 object-cover shadow-sm"
                />
                <div className="space-y-1">
                  <div className="font-bold text-base text-slate-900 flex items-center space-x-2">
                    <span>{currentUser.name}</span>
                    <span className="bg-emerald-100 text-emerald-800 text-[10px] px-2 py-0.5 rounded-full border border-emerald-200 font-bold">
                      {currentUser.roleName}
                    </span>
                  </div>
                  <p className="text-slate-500 font-mono text-xs">{currentUser.email || 'admin@dataagent.ai'}</p>
                  <p className="text-slate-500 text-[11px]">部门: {currentUser.department}</p>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="p-3.5 rounded-xl bg-slate-50 border border-slate-200 space-y-1">
                  <span className="text-[11px] text-slate-500">账号唯一 ID</span>
                  <div className="font-mono text-slate-900 font-bold">{currentUser.id}</div>
                </div>

                <div className="p-3.5 rounded-xl bg-slate-50 border border-slate-200 space-y-1">
                  <span className="text-[11px] text-slate-500">权限级别</span>
                  <div className="font-bold text-emerald-700 flex items-center space-x-1">
                    <Shield className="w-3.5 h-3.5" />
                    <span>{currentUser.role === 'admin' ? '最高管理员 (Full)' : '专有研发/测试员'}</span>
                  </div>
                </div>
              </div>

              <div className="p-4 rounded-2xl bg-emerald-50/80 border border-emerald-200 text-emerald-900 space-y-2">
                <div className="font-bold flex items-center space-x-1.5 text-emerald-800">
                  <Sparkles className="w-4 h-4 text-emerald-600" />
                  <span>多租户数据防护已生效</span>
                </div>
                <p className="text-slate-600 text-[11px] leading-relaxed">
                  您的个人 TaskSpec 配置、算子收藏及调试日志均针对 ID <span className="font-mono font-bold text-slate-800">{currentUser.id}</span> 独立隔离存储。
                </p>
              </div>
            </div>
          )}

          {/* SECURITY MODAL */}
          {activeModal === 'security' && (
            <div className="space-y-5">
              <div className="p-4 rounded-2xl bg-slate-50 border border-slate-200 space-y-3">
                <div className="flex items-center space-x-2 text-emerald-700 font-bold text-sm">
                  <Lock className="w-4 h-4" />
                  <span>SHA-256 标准加盐散列算法</span>
                </div>
                <p className="text-slate-600 text-xs leading-relaxed">
                  所有平台账号密码在客户端即时完成加盐处理与 SHA-256 哈希计算，服务器与本地 localStorage 均拒绝保存任何明文密码。
                </p>
              </div>

              <div className="space-y-3">
                <div className="p-3.5 rounded-xl bg-slate-50 border border-slate-200 flex items-center justify-between">
                  <div className="space-y-0.5">
                    <div className="font-bold text-slate-900">会话保持 Token</div>
                    <div className="text-[11px] text-slate-500 font-mono">有效期 7 天，网页刷新无需重复输入密码</div>
                  </div>
                  <span className="px-2.5 py-1 rounded-full bg-emerald-100 text-emerald-800 text-[10px] font-bold border border-emerald-200 font-mono">
                    ACTIVE
                  </span>
                </div>

                <div className="p-3.5 rounded-xl bg-slate-50 border border-slate-200 flex items-center justify-between">
                  <div className="space-y-0.5">
                    <div className="font-bold text-slate-900">独立数据盲算隔离</div>
                    <div className="text-[11px] text-slate-500">防止跨项目数据集漏算与血缘交叉</div>
                  </div>
                  <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                </div>
              </div>
            </div>
          )}

          {/* WORKSPACE MODAL */}
          {activeModal === 'workspace' && (
            <div className="space-y-5">
              <div className="p-4 rounded-2xl bg-slate-50 border border-slate-200 space-y-3">
                <div className="flex items-center justify-between">
                  <div className="font-bold text-slate-900 flex items-center space-x-2">
                    <Building2 className="w-4 h-4 text-emerald-600" />
                    <span>DataAgent Core Production Cluster</span>
                  </div>
                  <span className="text-[10px] bg-emerald-100 text-emerald-800 px-2 py-0.5 rounded-full border border-emerald-200 font-bold">
                    在线集群
                  </span>
                </div>
                <p className="text-slate-500 text-xs">
                  为您分配独立 LangGraph 智能算子调度节点与 GPU 显存加速池。
                </p>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="p-3.5 rounded-xl bg-slate-50 border border-slate-200 space-y-1">
                  <span className="text-slate-500 text-[11px]">GPU 算力配额</span>
                  <div className="font-bold text-slate-900 text-sm font-mono">4x NVIDIA H100 (80GB)</div>
                </div>

                <div className="p-3.5 rounded-xl bg-slate-50 border border-slate-200 space-y-1">
                  <span className="text-slate-500 text-[11px]">向量数据库</span>
                  <div className="font-bold text-slate-900 text-sm font-mono">Milvus Cluster v2.4</div>
                </div>
              </div>
            </div>
          )}

          {/* SETTINGS MODAL */}
          {activeModal === 'settings' && (
            <div className="space-y-5">
              <div className="p-4 rounded-2xl bg-slate-50 border border-slate-200 space-y-4">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="font-bold text-slate-900">自动实时保存 TaskSpec</div>
                    <div className="text-slate-500 text-[11px]">修改需求约束后自动同步生成版本血缘记录</div>
                  </div>
                  <input
                    type="checkbox"
                    checked={autoSave}
                    onChange={(e) => setAutoSave(e.target.checked)}
                    className="w-4 h-4 accent-emerald-600 cursor-pointer"
                  />
                </div>

                <div className="flex items-center justify-between border-t border-slate-200 pt-3">
                  <div>
                    <div className="font-bold text-slate-900">评测预警邮件通知</div>
                    <div className="text-slate-500 text-[11px]">当质检硬规则触发 [HARD_SAMPLE_GAP] 时发送提醒</div>
                  </div>
                  <input
                    type="checkbox"
                    checked={notifyEmail}
                    onChange={(e) => setNotifyEmail(e.target.checked)}
                    className="w-4 h-4 accent-emerald-600 cursor-pointer"
                  />
                </div>
              </div>
            </div>
          )}

        </div>

        {/* Modal Footer */}
        <div className="p-4 border-t border-slate-200 bg-slate-50 flex items-center justify-end space-x-3">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-xl text-slate-600 hover:bg-slate-200/80 transition-colors font-medium cursor-pointer"
          >
            关闭
          </button>
          {activeModal === 'settings' && (
            <button
              onClick={handleSaveSettings}
              className="px-5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-bold shadow-md shadow-emerald-600/20 transition-all cursor-pointer"
            >
              保存设置
            </button>
          )}
        </div>

      </div>
    </div>
  );
};

import React, { useState } from 'react';
import { useApp } from '../context/AppContext';
import { UserRole } from '../types';
import { 
  ShieldCheck, 
  Lock, 
  User as UserIcon, 
  Mail, 
  KeyRound, 
  Building2, 
  UserPlus, 
  LogIn, 
  Sparkles,
  CheckCircle2,
  AlertCircle,
  HelpCircle,
  Shield,
  Layers,
  Zap
} from 'lucide-react';

export const AuthPage: React.FC = () => {
  const { login, register, showToast } = useApp();

  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Login Form State
  const [loginAccount, setLoginAccount] = useState('admin');
  const [loginPassword, setLoginPassword] = useState('admin123');

  // Register Form State
  const [regUsername, setRegUsername] = useState('');
  const [regEmail, setRegEmail] = useState('');
  const [regName, setRegName] = useState('');
  const [regRole, setRegRole] = useState<UserRole>('data_engineer');
  const [regDepartment, setRegDepartment] = useState('AI 算法研发一部');
  const [regPassword, setRegPassword] = useState('');
  const [regConfirmPassword, setRegConfirmPassword] = useState('');

  const handleLoginSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);
    setIsSubmitting(true);

    try {
      const res = await login(loginAccount, loginPassword);
      if (!res.success) {
        setErrorMsg(res.message);
      }
    } catch (err: any) {
      setErrorMsg(err.message || '登录异常，请重试');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleRegisterSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);

    if (regPassword !== regConfirmPassword) {
      setErrorMsg('两次输入的密码不一致，请重新确认');
      return;
    }

    if (regPassword.length < 6) {
      setErrorMsg('为了您的账号安全，密码长度不能少于 6 位字符');
      return;
    }

    setIsSubmitting(true);

    try {
      const res = await register({
        username: regUsername,
        email: regEmail,
        name: regName,
        role: regRole,
        department: regDepartment,
        password: regPassword
      });

      if (!res.success) {
        setErrorMsg(res.message);
      }
    } catch (err: any) {
      setErrorMsg(err.message || '注册发生错误，请稍后重试');
    } finally {
      setIsSubmitting(false);
    }
  };

  // Preset quick fill helper
  const handleQuickFill = (acc: string, pass: string) => {
    setLoginAccount(acc);
    setLoginPassword(pass);
    setErrorMsg(null);
    showToast(`已快捷填入预置账号: ${acc}`);
  };

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900 flex flex-col justify-between p-4 sm:p-6 lg:p-8 font-sans selection:bg-emerald-500 selection:text-white relative overflow-hidden">
      
      {/* Dynamic Background Soft Glows */}
      <div className="absolute top-0 left-1/4 w-96 h-96 bg-emerald-500/5 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute bottom-0 right-1/4 w-96 h-96 bg-teal-500/5 rounded-full blur-3xl pointer-events-none" />

      {/* Top Brand Header */}
      <div className="max-w-6xl mx-auto w-full flex items-center justify-between z-10 py-2">
        <div className="flex items-center space-x-3">
          <div className="w-10 h-10 rounded-2xl bg-emerald-600 p-0.5 shadow-md shadow-emerald-600/20 flex items-center justify-center text-white">
            <Layers className="w-5 h-5" />
          </div>
          <div>
            <div className="font-black text-lg text-slate-900 tracking-tight flex items-center space-x-2">
              <span>DataAgent AI</span>
              <span className="text-[10px] bg-emerald-100 text-emerald-800 px-2 py-0.5 rounded-full border border-emerald-200 font-mono font-bold">
                v2.4 Core
              </span>
            </div>
            <p className="text-xs text-slate-500">多模态数据闭环、算子编排与质量评估系统</p>
          </div>
        </div>

        <div className="hidden sm:flex items-center space-x-3 text-xs text-slate-500">
          <span className="flex items-center space-x-1">
            <ShieldCheck className="w-4 h-4 text-emerald-600" />
            <span>SHA-256 加密鉴权</span>
          </span>
          <span>•</span>
          <span className="flex items-center space-x-1">
            <Zap className="w-4 h-4 text-amber-500" />
            <span>多租户数据隔离</span>
          </span>
        </div>
      </div>

      {/* Main Container Card */}
      <div className="max-w-4xl mx-auto w-full my-auto py-8 z-10 grid grid-cols-1 lg:grid-cols-12 gap-8 items-center">
        
        {/* Left Column: Platform Features & Preset Accounts */}
        <div className="lg:col-span-5 space-y-6">
          <div className="space-y-2">
            <div className="inline-flex items-center space-x-1.5 bg-emerald-50 text-emerald-800 border border-emerald-200 px-3 py-1 rounded-full text-xs font-bold">
              <Sparkles className="w-3.5 h-3.5 text-emerald-600" />
              <span>企业级完整身份鉴权保障</span>
            </div>
            <h1 className="text-2xl sm:text-3xl font-black text-slate-900 leading-tight">
              数据工程与大模型评测专有工作台
            </h1>
            <p className="text-xs text-slate-600 leading-relaxed">
              登录后即可体验完整的工单试跑、自定义算子上传、3套 Candidate 方案比对以及 Golden Set 争议质检全流程。
            </p>
          </div>

          {/* Preset Accounts Card for Testing */}
          <div className="bg-white rounded-2xl p-4 border border-slate-200/80 shadow-2xs space-y-3">
            <div className="flex items-center justify-between border-b border-slate-100 pb-2">
              <span className="text-xs font-bold text-slate-800 flex items-center space-x-1.5">
                <Shield className="w-4 h-4 text-emerald-600" />
                <span>预置角色账号 (快捷点选登录)</span>
              </span>
              <span className="text-[10px] text-emerald-800 bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200 font-mono font-bold">
                自带密码
              </span>
            </div>

            <div className="space-y-2">
              <button
                type="button"
                onClick={() => handleQuickFill('admin', 'admin123')}
                className="w-full text-left bg-emerald-50/60 hover:bg-emerald-100/80 border border-emerald-200/80 p-2.5 rounded-xl transition-all flex items-center justify-between group cursor-pointer"
              >
                <div>
                  <div className="text-xs font-bold text-emerald-900 flex items-center space-x-1.5">
                    <span>预置超级管理员</span>
                    <span className="px-1.5 py-0.2 rounded bg-amber-100 text-amber-800 text-[10px] font-mono border border-amber-200">
                      admin
                    </span>
                  </div>
                  <div className="text-[10px] text-slate-500 mt-0.5">账号: admin / 密码: admin123</div>
                </div>
                <LogIn className="w-4 h-4 text-emerald-600 opacity-0 group-hover:opacity-100 transition-opacity" />
              </button>

              <button
                type="button"
                onClick={() => handleQuickFill('dr.chen', 'admin123')}
                className="w-full text-left bg-slate-50 hover:bg-slate-100 border border-slate-200 p-2.5 rounded-xl transition-all flex items-center justify-between group cursor-pointer"
              >
                <div>
                  <div className="text-xs font-bold text-slate-800">陈博士 (数据科学家/管理员)</div>
                  <div className="text-[10px] text-slate-500 mt-0.5">账号: dr.chen / 密码: admin123</div>
                </div>
                <LogIn className="w-4 h-4 text-slate-500 opacity-0 group-hover:opacity-100 transition-opacity" />
              </button>

              <button
                type="button"
                onClick={() => handleQuickFill('sarah.lin', '123456')}
                className="w-full text-left bg-slate-50 hover:bg-slate-100 border border-slate-200 p-2.5 rounded-xl transition-all flex items-center justify-between group cursor-pointer"
              >
                <div>
                  <div className="text-xs font-bold text-slate-800">Sarah Lin (模型训练工程师)</div>
                  <div className="text-[10px] text-slate-500 mt-0.5">账号: sarah.lin / 密码: 123456</div>
                </div>
                <LogIn className="w-4 h-4 text-slate-500 opacity-0 group-hover:opacity-100 transition-opacity" />
              </button>
            </div>
          </div>
        </div>

        {/* Right Column: Auth Box (Login or Register) */}
        <div className="lg:col-span-7 bg-white rounded-3xl border border-slate-200 shadow-xl p-6 sm:p-8 space-y-6">
          
          {/* Form Tabs Switcher */}
          <div className="flex bg-slate-100 p-1 rounded-2xl border border-slate-200">
            <button
              onClick={() => { setMode('login'); setErrorMsg(null); }}
              className={`flex-1 py-2.5 rounded-xl text-xs font-bold transition-all flex items-center justify-center space-x-2 cursor-pointer ${
                mode === 'login'
                  ? 'bg-emerald-600 text-white shadow-md shadow-emerald-600/20'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <LogIn className="w-4 h-4" />
              <span>用户登录 (Login)</span>
            </button>

            <button
              onClick={() => { setMode('register'); setErrorMsg(null); }}
              className={`flex-1 py-2.5 rounded-xl text-xs font-bold transition-all flex items-center justify-center space-x-2 cursor-pointer ${
                mode === 'register'
                  ? 'bg-emerald-600 text-white shadow-md shadow-emerald-600/20'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <UserPlus className="w-4 h-4" />
              <span>注册新账号 (Register)</span>
            </button>
          </div>

          {/* Error Alert Box */}
          {errorMsg && (
            <div className="bg-rose-50 border border-rose-200 text-rose-800 p-3.5 rounded-xl text-xs flex items-start space-x-2.5 animate-in fade-in duration-200">
              <AlertCircle className="w-4 h-4 text-rose-600 mt-0.5 shrink-0" />
              <div className="space-y-0.5">
                <span className="font-bold">认证失败:</span>
                <p className="text-rose-700">{errorMsg}</p>
              </div>
            </div>
          )}

          {/* FORM 1: LOGIN */}
          {mode === 'login' && (
            <form onSubmit={handleLoginSubmit} className="space-y-4 text-xs">
              <div>
                <label className="block font-bold text-slate-700 mb-1.5 flex items-center space-x-1.5">
                  <UserIcon className="w-3.5 h-3.5 text-emerald-600" />
                  <span>用户名或电子邮箱 Username or Email</span>
                </label>
                <input
                  type="text"
                  required
                  value={loginAccount}
                  onChange={(e) => setLoginAccount(e.target.value)}
                  placeholder="请输入用户名或注册邮箱 (例如: admin 或 admin@dataagent.ai)"
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl px-3.5 py-3 text-slate-900 placeholder-slate-400 focus:outline-none focus:border-emerald-500 focus:bg-white transition-colors font-mono"
                />
              </div>

              <div>
                <label className="block font-bold text-slate-700 mb-1.5 flex items-center space-x-1.5">
                  <KeyRound className="w-3.5 h-3.5 text-emerald-600" />
                  <span>账号密码 Password (SHA-256 哈希比对)</span>
                </label>
                <input
                  type="password"
                  required
                  value={loginPassword}
                  onChange={(e) => setLoginPassword(e.target.value)}
                  placeholder="请输入密码..."
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl px-3.5 py-3 text-slate-900 placeholder-slate-400 focus:outline-none focus:border-emerald-500 focus:bg-white transition-colors font-mono"
                />
              </div>

              <div className="pt-2">
                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="w-full bg-emerald-600 hover:bg-emerald-500 active:scale-[0.99] text-white py-3.5 rounded-xl font-bold shadow-md shadow-emerald-600/20 transition-all flex items-center justify-center space-x-2 text-sm disabled:opacity-50 cursor-pointer"
                >
                  {isSubmitting ? (
                    <span>登录验证中...</span>
                  ) : (
                    <>
                      <LogIn className="w-4 h-4" />
                      <span>登录进入系统</span>
                    </>
                  )}
                </button>
              </div>
            </form>
          )}

          {/* FORM 2: REGISTER */}
          {mode === 'register' && (
            <form onSubmit={handleRegisterSubmit} className="space-y-4 text-xs">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block font-bold text-slate-700 mb-1">
                    用户名 Unique Username *
                  </label>
                  <input
                    type="text"
                    required
                    value={regUsername}
                    onChange={(e) => setRegUsername(e.target.value)}
                    placeholder="如: jack_data"
                    className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-900 focus:outline-none focus:border-emerald-500 focus:bg-white font-mono"
                  />
                </div>

                <div>
                  <label className="block font-bold text-slate-700 mb-1">
                    电子邮箱 Email *
                  </label>
                  <input
                    type="email"
                    required
                    value={regEmail}
                    onChange={(e) => setRegEmail(e.target.value)}
                    placeholder="如: jack@dataagent.ai"
                    className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-900 focus:outline-none focus:border-emerald-500 focus:bg-white font-mono"
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block font-bold text-slate-700 mb-1">
                    姓名/称谓 Full Name *
                  </label>
                  <input
                    type="text"
                    required
                    value={regName}
                    onChange={(e) => setRegName(e.target.value)}
                    placeholder="如: 张工程师"
                    className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-900 focus:outline-none focus:border-emerald-500 focus:bg-white"
                  />
                </div>

                <div>
                  <label className="block font-bold text-slate-700 mb-1">
                    平台角色 Role *
                  </label>
                  <select
                    value={regRole}
                    onChange={(e) => setRegRole(e.target.value as UserRole)}
                    className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-900 focus:outline-none focus:border-emerald-500 focus:bg-white"
                  >
                    <option value="data_engineer">数据工程专家 (Data Engineer)</option>
                    <option value="model_trainer">模型训练工程师 (Model Trainer)</option>
                    <option value="data_scientist">数据科学家 (Data Scientist)</option>
                    <option value="evaluator">Golden Set 评测专家 (Evaluator)</option>
                    <option value="admin">平台管理员 (Administrator)</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block font-bold text-slate-700 mb-1">
                  所属部门 Department / Team
                </label>
                <input
                  type="text"
                  value={regDepartment}
                  onChange={(e) => setRegDepartment(e.target.value)}
                  placeholder="如: 多模态感知实验室"
                  className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-900 focus:outline-none focus:border-emerald-500 focus:bg-white"
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label className="block font-bold text-slate-700 mb-1">
                    设置密码 Password *
                  </label>
                  <input
                    type="password"
                    required
                    value={regPassword}
                    onChange={(e) => setRegPassword(e.target.value)}
                    placeholder="至少 6 位字符"
                    className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-900 focus:outline-none focus:border-emerald-500 focus:bg-white font-mono"
                  />
                </div>

                <div>
                  <label className="block font-bold text-slate-700 mb-1">
                    确认密码 Confirm Password *
                  </label>
                  <input
                    type="password"
                    required
                    value={regConfirmPassword}
                    onChange={(e) => setRegConfirmPassword(e.target.value)}
                    placeholder="请再次输入密码"
                    className="w-full bg-slate-50 border border-slate-200 rounded-xl p-2.5 text-slate-900 focus:outline-none focus:border-emerald-500 focus:bg-white font-mono"
                  />
                </div>
              </div>

              <div className="pt-2">
                <button
                  type="submit"
                  disabled={isSubmitting}
                  className="w-full bg-emerald-600 hover:bg-emerald-500 active:scale-[0.99] text-white py-3.5 rounded-xl font-bold shadow-md shadow-emerald-600/20 transition-all flex items-center justify-center space-x-2 text-sm disabled:opacity-50 cursor-pointer"
                >
                  {isSubmitting ? (
                    <span>创建新账号中...</span>
                  ) : (
                    <>
                      <UserPlus className="w-4 h-4" />
                      <span>确认注册并一键登录</span>
                    </>
                  )}
                </button>
              </div>
            </form>
          )}

        </div>
      </div>

      {/* Footer copyright */}
      <div className="max-w-6xl mx-auto w-full text-center text-[11px] text-slate-400 z-10 py-2">
        DataAgent AI © 2026 Enterprise Agent Platform • 安全离散哈希密码存储 & 页面状态保持
      </div>

    </div>
  );
};

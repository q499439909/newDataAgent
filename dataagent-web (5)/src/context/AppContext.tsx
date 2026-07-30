import React, { createContext, useContext, useState, useEffect } from 'react';
import { User, UserRole, OperatorSpec, PipelineVersion, WorkOrder, AuditLog, WorkOrderChatMessage } from '../types';
import { INITIAL_OPERATORS } from '../data/initialOperators';
import { INITIAL_PIPELINES, MOCK_NODE_PREVIEWS } from '../data/initialPipelines';
import { INITIAL_WORK_ORDERS } from '../data/initialWorkOrders';
import {
  createConversation,
  getAgentContinuation,
  getAgentState,
  listBackendOperators,
  resumeAgentAsync,
  sendConversationMessage,
  submitDatasetRun,
  StreamEvent,
} from '../api/dataagentClient';
import {
  mapBackendOperator,
  mapAgentTurnToWorkOrder,
  mergeConversationResponseIntoWorkOrder,
} from '../api/adapters';
import { 
  getActiveAuthSession, 
  saveAuthSession, 
  clearAuthSession, 
  authenticateUser, 
  registerUser,
  initializeUserStore 
} from '../utils/auth';

interface AppContextType {
  isAuthenticated: boolean;
  currentUser: User;
  setCurrentUser: (user: User) => void;
  login: (usernameOrEmail: string, pass: string) => Promise<{ success: boolean; message: string }>;
  register: (params: { username: string; email: string; name: string; role: UserRole; department: string; password: string }) => Promise<{ success: boolean; message: string }>;
  logout: (reason?: string) => void;

  operators: OperatorSpec[];
  userFavorites: string[]; // operator IDs
  toggleFavoriteOperator: (opId: string) => void;
  addCustomOperator: (op: Omit<OperatorSpec, 'id' | 'createdAt' | 'updatedAt' | 'starsCount' | 'usageCount'>) => void;
  applyForOperatorPromotion: (opId: string) => void;
  reviewOperatorPromotion: (opId: string, approve: boolean, comment?: string) => void;
  pipelines: PipelineVersion[];
  workOrders: WorkOrder[];
  activeWorkOrder: WorkOrder | null;
  pendingWorkOrderActions: Record<string, string>;
  setActiveWorkOrder: (wo: WorkOrder | null) => void;
  activeWorkOrderChatMessages: WorkOrderChatMessage[];
  setActiveWorkOrderChatMessages: (messages: WorkOrderChatMessage[]) => void;
  setWorkOrderChatMessages: (workOrderId: string, messages: WorkOrderChatMessage[]) => void;
  updateWorkOrderStage: (woId: string, stage: WorkOrder['currentStage']) => void;
  createNewWorkOrder: (name: string, description: string) => Promise<WorkOrder>;
  sendMainAgentMessage: (
    content: string,
    onStreamEvent?: (event: StreamEvent) => void,
    targetWorkOrder?: WorkOrder,
  ) => Promise<{ reply: string; workOrder: WorkOrder | null }>;
  approveCurrentTaskSpec: () => Promise<void>;
  approveCurrentOperatorPlan: () => Promise<void>;
  approveCurrentPipeline: (pipelineId?: string) => Promise<void>;
  submitCurrentDatasetRun: () => Promise<void>;
  renameWorkOrder: (woId: string, newName: string) => void;
  duplicateWorkOrder: (woId: string) => void;
  archiveWorkOrder: (woId: string) => void;
  deleteWorkOrder: (woId: string) => void;
  triggerIncrementalRerun: (woId: string) => void;
  
  // UI & Search State
  searchQuery: string;
  setSearchQuery: (q: string) => void;
  isSearchModalOpen: boolean;
  setIsSearchModalOpen: (open: boolean) => void;
  selectedOperatorForDetail: OperatorSpec | null;
  setSelectedOperatorForDetail: (op: OperatorSpec | null) => void;
  isUploadModalOpen: boolean;
  setIsUploadModalOpen: (open: boolean) => void;
  isTuiCockpitOpen: boolean;
  setIsTuiCockpitOpen: (open: boolean) => void;
  
  // Notifications & Audit
  toastMessage: string | null;
  showToast: (msg: string) => void;
  auditLogs: AuditLog[];
  addAuditLog: (action: string, targetType: string, targetId: string, details: string) => void;
}

const AppContext = createContext<AppContextType | undefined>(undefined);

export const AppProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  // Auth State
  const [isAuthenticated, setIsAuthenticated] = useState<boolean>(false);
  const [currentUser, setCurrentUser] = useState<User>({
    id: 'guest',
    name: '未登录用户',
    email: '',
    avatar: 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=120&q=80',
    role: 'data_engineer',
    roleName: '访客',
    department: '暂无部门'
  });

  const [operators, setOperators] = useState<OperatorSpec[]>(INITIAL_OPERATORS);
  const [userFavorites, setUserFavorites] = useState<string[]>(['op-watermark-01', 'op-mllm-verifier-04']);
  const [pipelines, setPipelines] = useState<PipelineVersion[]>(INITIAL_PIPELINES);
  const [workOrders, setWorkOrders] = useState<WorkOrder[]>(INITIAL_WORK_ORDERS);
  const [activeWorkOrder, setActiveWorkOrder] = useState<WorkOrder | null>(INITIAL_WORK_ORDERS[0]);
  const [pendingWorkOrderActions, setPendingWorkOrderActions] = useState<Record<string, string>>({});
  const [chatMessagesByWorkOrder, setChatMessagesByWorkOrder] = useState<Record<string, WorkOrderChatMessage[]>>({});
  const [hasLoadedWorkspaceState, setHasLoadedWorkspaceState] = useState(false);

  // Modals & Search
  const [searchQuery, setSearchQuery] = useState('');
  const [isSearchModalOpen, setIsSearchModalOpen] = useState(false);
  const [selectedOperatorForDetail, setSelectedOperatorForDetail] = useState<OperatorSpec | null>(null);
  const [isUploadModalOpen, setIsUploadModalOpen] = useState(false);
  const [isTuiCockpitOpen, setIsTuiCockpitOpen] = useState(false);

  // Toast & Audit
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);

  const chatStorageKey = currentUser.id && currentUser.id !== 'guest'
    ? `dataagent_chat_messages_${currentUser.id}`
    : '';
  const workOrdersStorageKey = currentUser.id && currentUser.id !== 'guest'
    ? `dataagent_work_orders_${currentUser.id}`
    : '';
  const activeWorkOrderStorageKey = currentUser.id && currentUser.id !== 'guest'
    ? `dataagent_active_work_order_${currentUser.id}`
    : '';

  // Initialize store and restore session on boot
  useEffect(() => {
    async function bootAuth() {
      await initializeUserStore();
      const existingSession = getActiveAuthSession();
      if (existingSession && existingSession.user) {
        setCurrentUser(existingSession.user);
        setIsAuthenticated(true);
        loadUserFavorites(existingSession.user.id);
      } else {
        setIsAuthenticated(false);
      }
    }
    bootAuth();
  }, []);

  useEffect(() => {
    if (!isAuthenticated || !chatStorageKey) return;
    try {
      const saved = localStorage.getItem(chatStorageKey);
      setChatMessagesByWorkOrder(saved ? JSON.parse(saved) : {});
    } catch (error) {
      setChatMessagesByWorkOrder({});
    }
  }, [isAuthenticated, chatStorageKey]);

  useEffect(() => {
    if (!isAuthenticated || !workOrdersStorageKey || !activeWorkOrderStorageKey) return;
    try {
      const savedWorkOrders = localStorage.getItem(workOrdersStorageKey);
      const savedActiveId = localStorage.getItem(activeWorkOrderStorageKey);
      if (savedWorkOrders) {
        const parsed = JSON.parse(savedWorkOrders) as WorkOrder[];
        if (Array.isArray(parsed) && parsed.length) {
          setWorkOrders(parsed);
          setActiveWorkOrder(parsed.find(item => item.id === savedActiveId) || parsed[0]);
        }
      }
    } catch (error) {
      // keep initial prototype work orders
    } finally {
      setHasLoadedWorkspaceState(true);
    }
  }, [activeWorkOrderStorageKey, isAuthenticated, workOrdersStorageKey]);

  useEffect(() => {
    if (!isAuthenticated || !chatStorageKey) return;
    try {
      localStorage.setItem(chatStorageKey, JSON.stringify(chatMessagesByWorkOrder));
    } catch (error) {
      // ignore local storage quota or privacy mode failures
    }
  }, [chatMessagesByWorkOrder, chatStorageKey, isAuthenticated]);

  useEffect(() => {
    if (!isAuthenticated || !hasLoadedWorkspaceState || !workOrdersStorageKey) return;
    try {
      localStorage.setItem(workOrdersStorageKey, JSON.stringify(workOrders));
    } catch (error) {
      // ignore local storage quota or privacy mode failures
    }
  }, [hasLoadedWorkspaceState, isAuthenticated, workOrders, workOrdersStorageKey]);

  useEffect(() => {
    if (!isAuthenticated || !hasLoadedWorkspaceState || !activeWorkOrderStorageKey || !activeWorkOrder) return;
    try {
      localStorage.setItem(activeWorkOrderStorageKey, activeWorkOrder.id);
    } catch (error) {
      // ignore local storage quota or privacy mode failures
    }
  }, [activeWorkOrder, activeWorkOrderStorageKey, hasLoadedWorkspaceState, isAuthenticated]);

  const loadUserFavorites = (userId: string) => {
    try {
      const favKey = `dataagent_favorites_${userId}`;
      const saved = localStorage.getItem(favKey);
      if (saved) {
        setUserFavorites(JSON.parse(saved));
      } else {
        setUserFavorites(['op-watermark-01', 'op-mllm-verifier-04']);
      }
    } catch (e) {
      // ignore
    }
  };

  const showToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => {
      setToastMessage(null);
    }, 3500);
  };

  const addAuditLog = (action: string, targetType: string, targetId: string, details: string) => {
    const newLog: AuditLog = {
      id: `log-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`,
      timestamp: new Date().toLocaleTimeString(),
      actor: currentUser.name || '系统',
      action,
      targetType,
      targetId,
      details
    };
    setAuditLogs(prev => [newLog, ...prev]);
  };

  const upsertWorkOrder = (next: WorkOrder) => {
    setWorkOrders(prev => {
      const matches = (workOrder: WorkOrder) => (
        workOrder.id === next.id
        || Boolean(
          workOrder.conversationId
          && next.conversationId
          && workOrder.conversationId === next.conversationId
        )
      );
      const exists = prev.some(matches);
      return exists ? prev.map(wo => (matches(wo) ? next : wo)) : [next, ...prev];
    });
    setActiveWorkOrder(previous => {
      if (!previous) return previous;
      const isSameWorkOrder = previous.id === next.id;
      const isSameConversation = Boolean(
        previous.conversationId
        && next.conversationId
        && previous.conversationId === next.conversationId
      );
      return isSameWorkOrder || isSameConversation ? next : previous;
    });
  };

  const activeWorkOrderChatMessages = activeWorkOrder
    ? chatMessagesByWorkOrder[activeWorkOrder.id] || []
    : [];

  const setActiveWorkOrderChatMessages = (messages: WorkOrderChatMessage[]) => {
    if (!activeWorkOrder) return;
    const workOrderId = activeWorkOrder.id;
    setWorkOrderChatMessages(workOrderId, messages);
  };

  const setWorkOrderChatMessages = (workOrderId: string, messages: WorkOrderChatMessage[]) => {
    setChatMessagesByWorkOrder(prev => ({
      ...prev,
      [workOrderId]: messages,
    }));
  };

  useEffect(() => {
    if (!isAuthenticated || !currentUser.id || currentUser.id === 'guest') return;
    let cancelled = false;
    listBackendOperators(currentUser.id)
      .then(items => {
        if (!cancelled && items.length) {
          setOperators(items.map(mapBackendOperator));
          addAuditLog('BACKEND_OPERATORS_SYNC', 'OperatorRegistry', 'backend', `同步 ${items.length} 个后端算子`);
        }
      })
      .catch(() => {
        if (!cancelled) {
          showToast('未连接到后端算子注册表，当前使用本地原型数据。');
        }
      });
    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, currentUser.id]);

  const handleLogin = async (usernameOrEmail: string, pass: string) => {
    const res = await authenticateUser(usernameOrEmail, pass);
    if (res.success && res.user) {
      setCurrentUser(res.user);
      setIsAuthenticated(true);
      saveAuthSession(res.user);
      loadUserFavorites(res.user.id);
      showToast(`欢迎回来，${res.user.name}！已成功登录。`);
      addAuditLog('USER_LOGIN', 'User', res.user.id, `角色: ${res.user.roleName}, 部门: ${res.user.department}`);
      return { success: true, message: res.message };
    } else {
      return { success: false, message: res.message };
    }
  };

  const handleRegister = async (params: {
    username: string;
    email: string;
    name: string;
    role: UserRole;
    department: string;
    password: string;
  }) => {
    const res = await registerUser(params);
    if (res.success && res.user) {
      setCurrentUser(res.user);
      setIsAuthenticated(true);
      saveAuthSession(res.user);
      loadUserFavorites(res.user.id);
      showToast(`账号注册成功！欢迎加入 DataAgent 平台，${res.user.name}。`);
      addAuditLog('USER_REGISTER', 'User', res.user.id, `新账号注册: ${res.user.email}`);
      return { success: true, message: res.message };
    } else {
      return { success: false, message: res.message };
    }
  };

  const handleLogout = (reason?: string) => {
    clearAuthSession();
    setIsAuthenticated(false);
    showToast(reason || '您已安全退出当前系统。');
  };

  const toggleFavoriteOperator = (opId: string) => {
    setUserFavorites(prev => {
      const exists = prev.includes(opId);
      const updated = exists ? prev.filter(id => id !== opId) : [...prev, opId];
      
      // Update star count
      setOperators(ops => ops.map(op => {
        if (op.id === opId) {
          return { ...op, starsCount: exists ? op.starsCount - 1 : op.starsCount + 1 };
        }
        return op;
      }));

      showToast(exists ? '已从个人收藏中移除' : '已成功加入个人收藏');
      addAuditLog(exists ? 'UNFAVORITE_OPERATOR' : 'FAVORITE_OPERATOR', 'Operator', opId, `操作人: ${currentUser.name}`);
      return updated;
    });
  };

  const addCustomOperator = (opData: Omit<OperatorSpec, 'id' | 'createdAt' | 'updatedAt' | 'starsCount' | 'usageCount'>) => {
    const newOp: OperatorSpec = {
      ...opData,
      id: `op-custom-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`,
      starsCount: 0,
      usageCount: 0,
      updatedAt: new Date().toISOString().split('T')[0]
    };

    setOperators(prev => [newOp, ...prev]);
    showToast(`自定义算子「${newOp.name}」已创建并存入个人算子库！`);
    addAuditLog('CREATE_OPERATOR', 'Operator', newOp.id, `归属人: ${newOp.ownerName}`);
  };

  const applyForOperatorPromotion = (opId: string) => {
    setOperators(prev => prev.map(op => {
      if (op.id === opId) {
        const updated: OperatorSpec = {
          ...op,
          reviewStatus: 'pending_review',
          reviewComment: '已提交公共社区库晋升申请，正等待平台架构师与合规专家复核。'
        };
        if (selectedOperatorForDetail?.id === opId) {
          setSelectedOperatorForDetail(updated);
        }
        return updated;
      }
      return op;
    }));

    showToast('已向管理员发起公共社区库晋升申请！');
    addAuditLog('PROMOTION_REQUEST', 'Operator', opId, `申请人: ${currentUser.name}`);
  };

  const reviewOperatorPromotion = (opId: string, approve: boolean, comment?: string) => {
    setOperators(prev => prev.map(op => {
      if (op.id === opId) {
        const updated: OperatorSpec = {
          ...op,
          isPublic: approve ? true : op.isPublic,
          reviewStatus: approve ? 'approved' : 'rejected',
          reviewComment: comment || (approve ? '管理员复核通过，允许晋升为公共社区算子' : '退回修改：请补充更多 Golden Set 测试案例')
        };
        if (selectedOperatorForDetail?.id === opId) {
          setSelectedOperatorForDetail(updated);
        }
        return updated;
      }
      return op;
    }));

    showToast(approve ? '已批准该算子晋升到公共社区库！' : '已退回该算子的公共晋升申请');
    addAuditLog(approve ? 'PROMOTION_APPROVED' : 'PROMOTION_REJECTED', 'Operator', opId, `审核人: ${currentUser.name}`);
  };

  const updateWorkOrderStage = (woId: string, stage: WorkOrder['currentStage']) => {
    setWorkOrders(prev => prev.map(wo => {
      if (wo.id === woId) {
        const updated = { ...wo, currentStage: stage };
        if (activeWorkOrder?.id === woId) setActiveWorkOrder(updated);
        return updated;
      }
      return wo;
    }));
    addAuditLog('WORKORDER_STAGE_UPDATE', 'WorkOrder', woId, `新阶段: ${stage}`);
  };

  const createNewWorkOrder = async (name: string, description: string): Promise<WorkOrder> => {
    try {
      const thread = await createConversation(currentUser.id);
      const conversationId = String(thread.id || thread.thread_id || '');
      if (conversationId) {
        const backendWo: WorkOrder = {
          id: `draft-${conversationId}`,
          name: name || '新的数据任务对话',
          targetDescription: description || '请在对话中提供数据源路径、处理目标和验收标准。',
          currentStage: 'spec',
          owner: currentUser.name,
          createdAt: new Date().toISOString().replace('T', ' ').substring(0, 16),
          revisionChain: ['conversation-created'],
          candidatePipelines: [],
          nodePreviews: [],
          conversationId,
          waitingFor: 'user_requirement',
          mainAgentAction: 'await_user_message',
          taskPlan: [
            { id: 'understand_requirement', label: '理解并结构化需求', status: 'in_progress' },
            { id: 'confirm_task_spec', label: '确认完整 TaskSpec', status: 'pending' },
            { id: 'retrieve_candidates', label: '检索算子和历史 Pipeline 候选', status: 'pending' },
            { id: 'compile_pipelines', label: '编译并校验三条候选 Pipeline', status: 'pending' },
          ],
        };
        upsertWorkOrder(backendWo);
        showToast('已连接后端主 Agent，会话工单已创建。');
        addAuditLog('CREATE_BACKEND_CONVERSATION', 'Conversation', conversationId, `创建人: ${currentUser.name}`);
        return backendWo;
      }
    } catch (error) {
      showToast('后端暂不可用，已切换为本地原型工单。');
    }

    const uid = Math.random().toString(36).substring(2, 7);
    const newWo: WorkOrder = {
      id: `wo-${Date.now()}-${uid}`,
      name: name || '自定义多模态数据生产任务',
      targetDescription: description,
      currentStage: 'spec',
      owner: currentUser.name,
      createdAt: new Date().toISOString().replace('T', ' ').substring(0, 16),
      revisionChain: ['rev-01-spec'],
      currentTaskSpec: {
        id: `ts-${Date.now()}-${uid}`,
        name,
        version: 'v1.0',
        createdBy: currentUser.name,
        hardConstraints: [
          '严禁包含未经脱敏的人脸生物特征',
          '彻底清洗版权台标与水印',
          '训练与测试集必须严格近重复去重隔离'
        ],
        semanticConstraints: [
          '覆盖各类极端对比度与天气场景',
          '提升难例长尾分布样本的比重'
        ],
        outputRequirements: [
          '交付 20,000 张标准化切片图像及 COCO 格式标注 Manifest'
        ],
        acceptanceCriteria: [
          '模型总体核心指标提升 >= +3.0%',
          '数据规则质量检验通过率 >= 98.0%'
        ],
        ambiguities: [
          '待确认：边缘糊图是否进行AI智能超分修复（默认选择不修复，直接剔除）'
        ],
        status: 'draft',
        targetMetrics: [
          { metric: '模型训练 Accuracy/mAP', baselineValue: '78.0%', targetValue: '81.5%' },
          { metric: '数据集干净度硬规则', baselineValue: '82.0%', targetValue: '>= 98.0%' }
        ]
      },
      candidatePipelines: INITIAL_PIPELINES,
      nodePreviews: MOCK_NODE_PREVIEWS
    };

    setWorkOrders(prev => [newWo, ...prev]);
    setActiveWorkOrder(newWo);
    showToast(`需求已接收，需求规划 Agent 正为您生成 TaskSpec 规格！`);
    addAuditLog('CREATE_WORKORDER', 'WorkOrder', newWo.id, `创建人: ${currentUser.name}`);
    return newWo;
  };

  const sendMainAgentMessage = async (
    content: string,
    onStreamEvent?: (event: StreamEvent) => void,
    targetWorkOrder?: WorkOrder,
  ): Promise<{ reply: string; workOrder: WorkOrder | null }> => {
    let target = targetWorkOrder || activeWorkOrder;
    if (!target?.conversationId) {
      target = await createNewWorkOrder('新的数据任务对话', '通过主 Agent 对话创建的任务');
    }
    if (!target.conversationId) {
      throw new Error('当前工单没有后端会话 ID');
    }

    const response = await sendConversationMessage(
      currentUser.id,
      target.conversationId,
      content,
      onStreamEvent,
    );
    const merged = mergeConversationResponseIntoWorkOrder(response, target);
    if (merged) {
      upsertWorkOrder(merged);
      addAuditLog(
        'MAIN_AGENT_TURN',
        'WorkOrder',
        merged.id,
        `主 Agent 动作: ${merged.mainAgentAction || 'chat'}`,
      );
    }
    return { reply: response.reply, workOrder: merged };
  };

  const approveCurrentTaskSpec = async () => {
    if (
      !activeWorkOrder?.agentTurn
      || activeWorkOrder.waitingFor !== 'task_spec_confirmation'
    ) {
      showToast('当前没有可审批的后端 TaskSpec。');
      return;
    }
    const target = activeWorkOrder;
    if (pendingWorkOrderActions[target.id]) return;
    setPendingWorkOrderActions(previous => ({
      ...previous,
      [target.id]: 'task_spec_confirmation',
    }));
    try {
      let continuation = await resumeAgentAsync(
        currentUser.id,
        target.id,
        {
          approved: true,
          expected_interrupt_kind: 'task_spec_confirmation',
        },
      );
      let updated = mapAgentTurnToWorkOrder(continuation.turn, target);
      upsertWorkOrder(updated);
      showToast('TaskSpec 已确认，后台正在检索算子并检查能力覆盖。');
      while (continuation.status === 'queued' || continuation.status === 'running') {
        await new Promise(resolve => window.setTimeout(resolve, 1000));
        continuation = await getAgentContinuation(
          currentUser.id,
          continuation.turn_id,
        );
        updated = mapAgentTurnToWorkOrder(continuation.turn, updated);
        upsertWorkOrder(updated);
      }
      if (continuation.status === 'failed') {
        throw new Error(continuation.error || 'TaskSpec 后台 Turn 失败');
      }
      addAuditLog('APPROVE_TASK_SPEC', 'WorkOrder', updated.id, `审批人: ${currentUser.name}`);
    } catch (error) {
      const authoritative = await getAgentState(currentUser.id, target.id);
      upsertWorkOrder(mapAgentTurnToWorkOrder(authoritative, target));
      showToast(
        `TaskSpec 确认失败：${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      setPendingWorkOrderActions(previous => {
        const next = { ...previous };
        delete next[target.id];
        return next;
      });
    }
  };

  const approveCurrentOperatorPlan = async () => {
    if (!activeWorkOrder?.agentTurn || activeWorkOrder.waitingFor !== 'operator_plan_confirmation') {
      showToast('当前没有待确认的算子能力方案。');
      return;
    }
    try {
      let continuation = await resumeAgentAsync(
        currentUser.id,
        activeWorkOrder.id,
        {
          approved: true,
          expected_interrupt_kind: 'operator_plan_confirmation',
        },
      );
      let updated = mapAgentTurnToWorkOrder(continuation.turn, activeWorkOrder);
      upsertWorkOrder(updated);
      showToast('算子能力方案已确认，Processing 已转入后台。');
      while (continuation.status === 'queued' || continuation.status === 'running') {
        await new Promise(resolve => window.setTimeout(resolve, 1000));
        continuation = await getAgentContinuation(
          currentUser.id,
          continuation.turn_id,
        );
        updated = mapAgentTurnToWorkOrder(continuation.turn, updated);
        upsertWorkOrder(updated);
      }
      if (continuation.status === 'failed') {
        throw new Error(continuation.error || 'Processing 后台 Turn 失败');
      }
      addAuditLog('APPROVE_OPERATOR_PLAN', 'WorkOrder', updated.id, `审批人: ${currentUser.name}`);
    } catch (error) {
      const authoritative = await getAgentState(currentUser.id, activeWorkOrder.id);
      upsertWorkOrder(mapAgentTurnToWorkOrder(authoritative, activeWorkOrder));
      throw error;
    }
  };

  const approveCurrentPipeline = async (pipelineId?: string) => {
    if (!activeWorkOrder?.agentTurn) {
      showToast('当前没有可审批的后端 Pipeline。');
      return;
    }
    const selectedId = pipelineId || activeWorkOrder.selectedPipelineId || activeWorkOrder.candidatePipelines?.[0]?.id;
    try {
      let continuation = await resumeAgentAsync(
        currentUser.id,
        activeWorkOrder.id,
        {
          approved: true,
          pipeline_id: selectedId,
          expected_interrupt_kind: 'pipeline_approval',
        },
      );
      let updated = mapAgentTurnToWorkOrder(continuation.turn, activeWorkOrder);
      upsertWorkOrder(updated);
      showToast('Pipeline 已批准，正在后台试运行选中的方案。');
      while (continuation.status === 'queued' || continuation.status === 'running') {
        await new Promise(resolve => window.setTimeout(resolve, 1000));
        continuation = await getAgentContinuation(
          currentUser.id,
          continuation.turn_id,
        );
        updated = mapAgentTurnToWorkOrder(continuation.turn, updated);
        upsertWorkOrder(updated);
      }
      if (continuation.status === 'failed') {
        throw new Error(continuation.error || 'Pipeline 试运行失败');
      }
      addAuditLog('APPROVE_PIPELINE', 'WorkOrder', updated.id, `Pipeline: ${selectedId || '-'}`);
    } catch (error) {
      const authoritative = await getAgentState(currentUser.id, activeWorkOrder.id);
      upsertWorkOrder(mapAgentTurnToWorkOrder(authoritative, activeWorkOrder));
      throw error;
    }
  };

  const submitCurrentDatasetRun = async () => {
    if (!activeWorkOrder?.agentTurn) {
      showToast('当前没有可提交运行的后端工单。');
      return;
    }
    const run = await submitDatasetRun(currentUser.id, activeWorkOrder.id);
    const runId = String(run.id || run.run_id || '');
    if (runId) {
      const updated = { ...activeWorkOrder, activeRunId: runId };
      setWorkOrders(prev => prev.map(wo => (wo.id === activeWorkOrder.id ? updated : wo)));
      setActiveWorkOrder(updated);
    }
    showToast(`全量数据运行已提交：${run.id || run.run_id}`);
    addAuditLog('SUBMIT_DATASET_RUN', 'WorkOrder', activeWorkOrder.id, `Run: ${run.id || run.run_id}`);
  };

  const renameWorkOrder = (woId: string, newName: string) => {
    if (!newName.trim()) return;
    setWorkOrders(prev => prev.map(wo => {
      if (wo.id === woId) {
        const updated = { 
          ...wo, 
          name: newName.trim(),
          currentTaskSpec: wo.currentTaskSpec ? { ...wo.currentTaskSpec, name: newName.trim() } : undefined
        };
        if (activeWorkOrder?.id === woId) setActiveWorkOrder(updated);
        return updated;
      }
      return wo;
    }));
    showToast(`工单已重命名为「${newName.trim()}」`);
    addAuditLog('RENAME_WORKORDER', 'WorkOrder', woId, `新名称: ${newName.trim()}`);
  };

  const duplicateWorkOrder = (woId: string) => {
    const source = workOrders.find(w => w.id === woId);
    if (!source) return;
    const uid = Math.random().toString(36).substring(2, 7);
    const newWo: WorkOrder = {
      ...source,
      id: `wo-${Date.now()}-${uid}`,
      name: `${source.name} (副本)`,
      createdAt: new Date().toISOString().replace('T', ' ').substring(0, 16),
      currentStage: 'spec',
      revisionChain: ['rev-01-cloned']
    };
    setWorkOrders(prev => [newWo, ...prev]);
    setActiveWorkOrder(newWo);
    showToast(`工单副本已成功创建！`);
    addAuditLog('DUPLICATE_WORKORDER', 'WorkOrder', newWo.id, `源工单: ${woId}`);
  };

  const archiveWorkOrder = (woId: string) => {
    let isNowArchived = false;
    setWorkOrders(prev => prev.map(wo => {
      if (wo.id === woId) {
        isNowArchived = !wo.isArchived;
        const updated = { ...wo, isArchived: isNowArchived };
        if (activeWorkOrder?.id === woId) setActiveWorkOrder(updated);
        return updated;
      }
      return wo;
    }));
    showToast(isNowArchived ? '工单已移入归档状态' : '工单已从归档状态恢复');
    addAuditLog('ARCHIVE_WORKORDER', 'WorkOrder', woId, `归档状态: ${isNowArchived}`);
  };

  const deleteWorkOrder = (woId: string) => {
    const target = workOrders.find(w => w.id === woId);
    setWorkOrders(prev => prev.filter(w => w.id !== woId));
    if (activeWorkOrder?.id === woId) {
      const remaining = workOrders.filter(w => w.id !== woId);
      setActiveWorkOrder(remaining.length > 0 ? remaining[0] : null);
    }
    showToast(`工单「${target?.name || woId}」已删除`);
    addAuditLog('DELETE_WORKORDER', 'WorkOrder', woId, '已删除');
  };

  const triggerIncrementalRerun = (woId: string) => {
    showToast('Loop Supervisor 已触发定向增量返工！仅重跑 [HARD_SAMPLE_GAP] 受到影响的大雾儿童推车难例切片。');
    updateWorkOrderStage(woId, 'processing');
    addAuditLog('INCREMENTAL_RERUN', 'WorkOrder', woId, '原因码: HARD_SAMPLE_GAP, 增量扩展 1,200 样本');
  };

  // Keyboard shortcut listener for Cmd+K / Ctrl+K
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setIsSearchModalOpen(prev => !prev);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  return (
    <AppContext.Provider
      value={{
        isAuthenticated,
        currentUser,
        setCurrentUser,
        login: handleLogin,
        register: handleRegister,
        logout: handleLogout,
        operators,
        userFavorites,
        toggleFavoriteOperator,
        addCustomOperator,
        applyForOperatorPromotion,
        reviewOperatorPromotion,
        pipelines,
        workOrders,
        activeWorkOrder,
        pendingWorkOrderActions,
        setActiveWorkOrder,
        activeWorkOrderChatMessages,
        setActiveWorkOrderChatMessages,
        setWorkOrderChatMessages,
        updateWorkOrderStage,
        createNewWorkOrder,
        sendMainAgentMessage,
        approveCurrentTaskSpec,
        approveCurrentOperatorPlan,
        approveCurrentPipeline,
        submitCurrentDatasetRun,
        renameWorkOrder,
        duplicateWorkOrder,
        archiveWorkOrder,
        deleteWorkOrder,
        triggerIncrementalRerun,
        searchQuery,
        setSearchQuery,
        isSearchModalOpen,
        setIsSearchModalOpen,
        selectedOperatorForDetail,
        setSelectedOperatorForDetail,
        isUploadModalOpen,
        setIsUploadModalOpen,
        isTuiCockpitOpen,
        setIsTuiCockpitOpen,
        toastMessage,
        showToast,
        auditLogs,
        addAuditLog
      }}
    >
      {children}
    </AppContext.Provider>
  );
};

export const useApp = () => {
  const context = useContext(AppContext);
  if (!context) throw new Error('useApp must be used within an AppProvider');
  return context;
};

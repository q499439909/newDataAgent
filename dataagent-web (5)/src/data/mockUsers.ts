import { User } from '../types';

export const MOCK_USERS: User[] = [
  {
    id: 'user-001',
    name: '陈博士',
    email: 'dr.chen@dataagent.ai',
    avatar: 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=120&q=80',
    role: 'admin',
    roleName: '首席数据科学家 / 平台管理员',
    department: 'AI 基础数据平台部'
  },
  {
    id: 'user-002',
    name: 'Sarah Lin',
    email: 'sarah.lin@dataagent.ai',
    avatar: 'https://images.unsplash.com/photo-1494790108377-be9c29b29330?auto=format&fit=crop&w=120&q=80',
    role: 'model_trainer',
    roleName: '模型训练工程师 (需求方)',
    department: '自动驾驶感知算法组'
  },
  {
    id: 'user-003',
    name: 'Alex Wang',
    email: 'alex.wang@dataagent.ai',
    avatar: 'https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?auto=format&fit=crop&w=120&q=80',
    role: 'data_engineer',
    roleName: '数据工程专家',
    department: '大规模数据管线工程部'
  },
  {
    id: 'user-004',
    name: '张评测',
    email: 'eval.zhang@dataagent.ai',
    avatar: 'https://images.unsplash.com/photo-1500648767791-00dcc994a43e?auto=format&fit=crop&w=120&q=80',
    role: 'evaluator',
    roleName: '独立 Golden Set 评测专家',
    department: '算法评测与验收中心'
  }
];

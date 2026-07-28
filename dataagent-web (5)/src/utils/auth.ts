import { User, UserRole } from '../types';

export interface StoredUser extends User {
  username: string;
  passwordHash: string;
  createdAt: string;
}

const STORAGE_USERS_KEY = 'dataagent_registered_users_v2';
const STORAGE_SESSION_KEY = 'dataagent_active_session_v2';

/**
 * SHA-256 password hash using standard Web Crypto API
 */
export async function hashPassword(password: string): Promise<string> {
  const encoder = new TextEncoder();
  const data = encoder.encode(`dataagent_salt_2026_sec_${password.trim()}`);
  const hashBuffer = await crypto.subtle.digest('SHA-256', data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Pre-seeded accounts including the requested administrator account and team members
 */
const INITIAL_PRESET_USERS: (Omit<StoredUser, 'passwordHash'> & { defaultPass: string })[] = [
  {
    id: 'user-admin-001',
    username: 'admin',
    name: '系统超级管理员',
    email: 'admin@dataagent.ai',
    avatar: 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=120&q=80',
    role: 'admin',
    roleName: '平台最高系统管理员',
    department: 'AI 平台架构与治理中心',
    defaultPass: 'admin123',
    createdAt: '2026-01-01T00:00:00.000Z'
  },
  {
    id: 'user-001',
    username: 'dr.chen',
    name: '陈博士',
    email: 'dr.chen@dataagent.ai',
    avatar: 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=120&q=80',
    role: 'admin',
    roleName: '首席数据科学家 / 平台管理员',
    department: 'AI 基础数据平台部',
    defaultPass: 'admin123',
    createdAt: '2026-01-01T00:00:00.000Z'
  },
  {
    id: 'user-002',
    username: 'sarah.lin',
    name: 'Sarah Lin',
    email: 'sarah.lin@dataagent.ai',
    avatar: 'https://images.unsplash.com/photo-1494790108377-be9c29b29330?auto=format&fit=crop&w=120&q=80',
    role: 'model_trainer',
    roleName: '模型训练工程师 (需求方)',
    department: '自动驾驶感知算法组',
    defaultPass: '123456',
    createdAt: '2026-01-02T00:00:00.000Z'
  },
  {
    id: 'user-003',
    username: 'alex.wang',
    name: 'Alex Wang',
    email: 'alex.wang@dataagent.ai',
    avatar: 'https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?auto=format&fit=crop&w=120&q=80',
    role: 'data_engineer',
    roleName: '数据工程专家',
    department: '大规模数据管线工程部',
    defaultPass: '123456',
    createdAt: '2026-01-03T00:00:00.000Z'
  },
  {
    id: 'user-004',
    username: 'eval.zhang',
    name: '张评测',
    email: 'eval.zhang@dataagent.ai',
    avatar: 'https://images.unsplash.com/photo-1500648767791-00dcc994a43e?auto=format&fit=crop&w=120&q=80',
    role: 'evaluator',
    roleName: '独立 Golden Set 评测专家',
    department: '算法评测与验收中心',
    defaultPass: '123456',
    createdAt: '2026-01-04T00:00:00.000Z'
  }
];

/**
 * Initialize storage with default users and hashes if not present
 */
export async function initializeUserStore(): Promise<StoredUser[]> {
  try {
    const raw = localStorage.getItem(STORAGE_USERS_KEY);
    if (raw) {
      const parsed: StoredUser[] = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length > 0) {
        return parsed;
      }
    }

    // Build initial preset users with SHA-256 hashes
    const initializedUsers: StoredUser[] = await Promise.all(
      INITIAL_PRESET_USERS.map(async u => ({
        id: u.id,
        username: u.username,
        name: u.name,
        email: u.email,
        avatar: u.avatar,
        role: u.role,
        roleName: u.roleName,
        department: u.department,
        passwordHash: await hashPassword(u.defaultPass),
        createdAt: u.createdAt
      }))
    );

    localStorage.setItem(STORAGE_USERS_KEY, JSON.stringify(initializedUsers));
    return initializedUsers;
  } catch (e) {
    console.error('Error initializing user store:', e);
    return [];
  }
}

/**
 * Get all registered stored users
 */
export async function getStoredUsers(): Promise<StoredUser[]> {
  return await initializeUserStore();
}

/**
 * Register a new user account with hashed password
 */
export async function registerUser(params: {
  username: string;
  email: string;
  name: string;
  role: UserRole;
  department: string;
  password: string;
}): Promise<{ success: boolean; message: string; user?: StoredUser }> {
  const users = await getStoredUsers();

  const cleanUsername = params.username.trim().toLowerCase();
  const cleanEmail = params.email.trim().toLowerCase();

  if (users.some(u => u.username.toLowerCase() === cleanUsername)) {
    return { success: false, message: '该用户名已被使用，请换一个用户名' };
  }

  if (users.some(u => u.email.toLowerCase() === cleanEmail)) {
    return { success: false, message: '该电子邮箱已被注册，请直接登录' };
  }

  const roleNameMap: Record<UserRole, string> = {
    admin: '平台系统管理员',
    model_trainer: '模型训练工程师',
    data_scientist: '数据科学家',
    data_engineer: '数据工程专家',
    evaluator: '评测验收专家',
    compliance_auditor: '合规安全审计员'
  };

  const passwordHash = await hashPassword(params.password);

  const newUser: StoredUser = {
    id: `usr-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`,
    username: cleanUsername,
    name: params.name.trim(),
    email: cleanEmail,
    avatar: `https://api.dicebear.com/7.x/bottts/svg?seed=${cleanUsername}`,
    role: params.role,
    roleName: roleNameMap[params.role] || '算法工程师',
    department: params.department.trim() || '数据算法部',
    passwordHash,
    createdAt: new Date().toISOString()
  };

  const updatedUsers = [...users, newUser];
  localStorage.setItem(STORAGE_USERS_KEY, JSON.stringify(updatedUsers));

  return { success: true, message: '账号注册成功！正在进入系统...', user: newUser };
}

/**
 * Authenticate login credentials against hashed passwords
 */
export async function authenticateUser(
  usernameOrEmail: string,
  passwordInput: string
): Promise<{ success: boolean; message: string; user?: StoredUser }> {
  const users = await getStoredUsers();
  const target = usernameOrEmail.trim().toLowerCase();

  const foundUser = users.find(
    u => u.username.toLowerCase() === target || u.email.toLowerCase() === target
  );

  if (!foundUser) {
    return { success: false, message: '输入的用户名/邮箱不存在，请检查或重新注册' };
  }

  const inputHash = await hashPassword(passwordInput);

  if (foundUser.passwordHash !== inputHash) {
    return { success: false, message: '密码错误，请输入正确的账号密码' };
  }

  return { success: true, message: '登录成功！', user: foundUser };
}

/**
 * Active Session Storage
 */
export interface AuthSession {
  user: User;
  token: string;
  loginTime: string;
  expiresAt: number; // timestamp
}

export function saveAuthSession(user: User): AuthSession {
  const session: AuthSession = {
    user: {
      id: user.id,
      name: user.name,
      email: user.email,
      avatar: user.avatar,
      role: user.role,
      roleName: user.roleName,
      department: user.department
    },
    token: `token_${user.id}_${Date.now()}_${Math.random().toString(36).substring(2, 9)}`,
    loginTime: new Date().toISOString(),
    expiresAt: Date.now() + 7 * 24 * 60 * 60 * 1000 // 7 days valid
  };

  localStorage.setItem(STORAGE_SESSION_KEY, JSON.stringify(session));
  return session;
}

export function getActiveAuthSession(): AuthSession | null {
  try {
    const raw = localStorage.getItem(STORAGE_SESSION_KEY);
    if (!raw) return null;
    const session: AuthSession = JSON.parse(raw);
    if (session && session.expiresAt > Date.now()) {
      return session;
    }
    // Expired
    clearAuthSession();
    return null;
  } catch (e) {
    clearAuthSession();
    return null;
  }
}

export function clearAuthSession(): void {
  localStorage.removeItem(STORAGE_SESSION_KEY);
}

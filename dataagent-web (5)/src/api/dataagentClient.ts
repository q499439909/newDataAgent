export interface AgentTurnResponse {
  work_order_id: string;
  thread_id: string;
  state: Record<string, any>;
  interrupts: Array<{ id?: string | null; value: Record<string, any> }>;
}

export interface ConversationMessageResponse {
  reply: string;
  turn?: AgentTurnResponse | null;
  run?: Record<string, any> | null;
  action_trace?: Record<string, any>[];
  conversation_id?: string;
  work_order_id?: string | null;
  messages?: Record<string, any>[];
}

export interface ConversationThread {
  id?: string;
  thread_id?: string;
  owner_id?: string;
  work_order_id?: string | null;
  context?: Record<string, any>;
  messages?: Record<string, any>[];
}

export interface StreamEvent {
  type: 'action' | 'final' | 'error';
  action?: Record<string, any>;
  response?: ConversationMessageResponse;
  message?: string;
  error_type?: string;
}

const API_BASE = (import.meta.env.VITE_DATAAGENT_API_BASE || '').replace(/\/$/, '');

function ownerHeaders(ownerId: string, extra?: HeadersInit): HeadersInit {
  return {
    'Content-Type': 'application/json',
    'X-Owner-ID': ownerId,
    ...(extra || {}),
  };
}

async function parseJsonResponse<T>(response: Response): Promise<T> {
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    throw new Error(data?.detail || data?.message || response.statusText);
  }
  return data as T;
}

export async function listBackendOperators(ownerId: string): Promise<Record<string, any>[]> {
  const response = await fetch(`${API_BASE}/api/operators?include_drafts=true`, {
    headers: ownerHeaders(ownerId),
  });
  return parseJsonResponse<Record<string, any>[]>(response);
}

export async function createConversation(ownerId: string): Promise<ConversationThread> {
  const response = await fetch(`${API_BASE}/api/conversations`, {
    method: 'POST',
    headers: ownerHeaders(ownerId),
    body: JSON.stringify({}),
  });
  return parseJsonResponse<ConversationThread>(response);
}

export async function sendConversationMessage(
  ownerId: string,
  conversationId: string,
  content: string,
  onStreamEvent?: (event: StreamEvent) => void,
): Promise<ConversationMessageResponse> {
  const response = await fetch(`${API_BASE}/api/conversations/${conversationId}/messages/stream`, {
    method: 'POST',
    headers: ownerHeaders(ownerId),
    body: JSON.stringify({ content }),
  });

  if (!response.ok || !response.body) {
    return parseJsonResponse<ConversationMessageResponse>(response);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let finalResponse: ConversationMessageResponse | null = null;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';

    for (const line of lines) {
      if (!line.trim()) continue;
      const event = JSON.parse(line) as StreamEvent;
      onStreamEvent?.(event);
      if (event.type === 'error') {
        throw new Error(event.message || event.error_type || 'Conversation stream failed');
      }
      if (event.type === 'final' && event.response) {
        finalResponse = event.response;
      }
    }
  }

  if (buffer.trim()) {
    const event = JSON.parse(buffer) as StreamEvent;
    onStreamEvent?.(event);
    if (event.type === 'error') {
      throw new Error(event.message || event.error_type || 'Conversation stream failed');
    }
    if (event.type === 'final' && event.response) {
      finalResponse = event.response;
    }
  }

  if (!finalResponse) {
    throw new Error('Conversation stream ended without a final response');
  }
  return finalResponse;
}

export async function resumeAgent(
  ownerId: string,
  workOrderId: string,
  decision: Record<string, any>,
): Promise<AgentTurnResponse> {
  const response = await fetch(`${API_BASE}/api/work-orders/${workOrderId}/agent/resume`, {
    method: 'POST',
    headers: ownerHeaders(ownerId),
    body: JSON.stringify({ decision }),
  });
  return parseJsonResponse<AgentTurnResponse>(response);
}

export async function submitDatasetRun(
  ownerId: string,
  workOrderId: string,
): Promise<Record<string, any>> {
  const response = await fetch(`${API_BASE}/api/work-orders/${workOrderId}/runs`, {
    method: 'POST',
    headers: ownerHeaders(ownerId, { 'Idempotency-Key': crypto.randomUUID() }),
    body: JSON.stringify({}),
  });
  return parseJsonResponse<Record<string, any>>(response);
}

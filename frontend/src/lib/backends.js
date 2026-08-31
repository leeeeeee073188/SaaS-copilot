const DEFAULT_BACKENDS = {
  python: {
    id: 'python',
    label: 'EchoMind SaaS API',
    baseUrl: import.meta.env.VITE_PYTHON_API_URL || '/api/python',
    port: '8000'
  }
}

export function createInitialSettings() {
  const saved = readSettings()
  return {
    backend: 'python',
    userId: saved.userId || 'saas_analyst',
    conversationId: saved.conversationId || '',
    endpoints: {
      python: saved.endpoints?.python || DEFAULT_BACKENDS.python.baseUrl
    }
  }
}

export function saveSettings(settings) {
  localStorage.setItem('echomind.frontend.settings', JSON.stringify(settings))
}

export function backendMeta(type, settings) {
  const meta = DEFAULT_BACKENDS.python
  return {
    ...meta,
    baseUrl: normalizeBaseUrl(settings.endpoints[type] || meta.baseUrl)
  }
}

export async function requestHealth(type, settings) {
  return requestJson(backendMeta(type, settings).baseUrl, '/health')
}

export async function requestMonitor(type, settings) {
  return requestJson(backendMeta(type, settings).baseUrl, '/monitor')
}

export async function requestKnowledgeStats(type, settings) {
  return requestJson(backendMeta(type, settings).baseUrl, '/knowledge/stats')
}

export async function requestSearch(type, settings, query, topK = 5) {
  const params = new URLSearchParams({ query, top_k: String(topK) })
  return requestJson(backendMeta(type, settings).baseUrl, `/search?${params}`, { method: 'POST' })
}

export async function requestChat(type, settings, message) {
  const meta = backendMeta(type, settings)
  const payload = buildChatPayload(type, settings, message)
  const raw = await requestJson(meta.baseUrl, '/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  })
  return normalizeChatResponse(type, raw)
}

export async function addKnowledge(type, settings, documents) {
  return requestJson(backendMeta(type, settings).baseUrl, '/knowledge/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ documents })
  })
}

export async function uploadKnowledge(type, settings, file) {
  const form = new FormData()
  form.append('file', file)
  return requestJson(backendMeta(type, settings).baseUrl, '/knowledge/upload', {
    method: 'POST',
    body: form
  })
}

export async function requestRecentToolTraces(settings, limit = 20) {
  const params = new URLSearchParams({ limit: String(limit) })
  return requestJson(backendMeta('python', settings).baseUrl, `/trace/tools?${params}`)
}

function buildChatPayload(type, settings, message) {
  return {
    message,
    user_id: settings.userId || 'anonymous',
    conv_id: settings.conversationId || undefined
  }
}

function normalizeChatResponse(type, raw) {
  return {
    backend: type,
    requestId: raw.request_id || raw.requestId || '',
    conversationId: raw.conversation_id || raw.conversationId || raw.conv_id || '',
    response: raw.response || '',
    intent: raw.intent || 'other',
    agentType: raw.agent_type || raw.agentType || '',
    primaryAgent: raw.primary_agent || raw.primaryAgent || '',
    supportingAgents: raw.supporting_agents || raw.supportingAgents || [],
    toolsUsed: raw.tools_used || raw.toolsUsed || [],
    escalated: Boolean(raw.escalated),
    latencyMs: Number(raw.latency_ms ?? raw.latencyMs ?? 0),
    knowledgeUsed: Boolean(raw.knowledge_used ?? raw.knowledgeUsed),
    verified: raw.verified,
    grounded: raw.grounded,
    raw
  }
}

async function requestJson(baseUrl, path, options = {}) {
  const url = `${normalizeBaseUrl(baseUrl)}${path}`
  const response = await fetch(url, options)
  const text = await response.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch {
    data = text
  }
  if (!response.ok) {
    const detail = typeof data === 'string' ? data : JSON.stringify(data)
    throw new Error(`${response.status} ${response.statusText}: ${detail}`)
  }
  return data
}

function normalizeBaseUrl(value) {
  return String(value || '').replace(/\/+$/, '')
}

function readSettings() {
  try {
    return JSON.parse(localStorage.getItem('echomind.frontend.settings') || '{}')
  } catch {
    return {}
  }
}

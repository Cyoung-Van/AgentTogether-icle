import type {
  AgentDoctor,
  AgentInfo,
  AgentRating,
  BaselineProbeRun,
  BaselineProbeStatus,
  DetectedAgent,
  EpisodeDetail,
  EpisodeSummary,
  Health,
  IntelligenceModels,
  IntelligenceStatus,
  ProviderInfo,
  RatingDimensions,
  TaskInfo,
  TaskProfile,
  TaskReportView,
  TaskRun,
} from '../types'

const BASE = '/api'
const TOKEN_KEY = 'icle.apiToken'

export function getApiToken(): string {
  return window.localStorage.getItem(TOKEN_KEY) ?? ''
}

export function setApiToken(token: string): void {
  const value = token.trim()
  if (value) window.localStorage.setItem(TOKEN_KEY, value)
  else window.localStorage.removeItem(TOKEN_KEY)
}

function authHeaders(): HeadersInit {
  const token = getApiToken()
  return token ? { 'X-ICLE-Token': token } : {}
}

async function errorFrom(response: Response): Promise<Error> {
  let detail = ''
  try {
    const body = await response.json()
    detail = typeof body.detail === 'string' ? body.detail : ''
  } catch {
    // non-JSON gateway errors keep the HTTP status below
  }
  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent('icle-auth-required'))
  }
  return new Error(detail || `${response.status} ${response.statusText}`)
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { headers: authHeaders() })
  if (!response.ok) throw await errorFrom(response)
  return response.json() as Promise<T>
}

async function post<T>(path: string, body: unknown, method = 'POST'): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: JSON.stringify(body),
  })
  if (!response.ok) throw await errorFrom(response)
  return response.json() as Promise<T>
}

async function del<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { method: 'DELETE', headers: authHeaders() })
  if (!response.ok) throw await errorFrom(response)
  return response.json() as Promise<T>
}

export const api = {
  health: () => get<Health>('/health'),
  intelligenceStatus: () => get<IntelligenceStatus>('/intelligence-status'),
  intelligenceModels: () => get<IntelligenceModels>('/intelligence-models'),
  selectIntelligenceModel: (providerId: string, modelId: string) =>
    post<IntelligenceModels>('/intelligence-model', { provider_id: providerId, model_id: modelId }),
  agents: () => get<{ agents: AgentInfo[] }>('/agents'),
  agentProfile: (id: string) => get<any>(`/agent-profile?agent_id=${encodeURIComponent(id)}`),
  refreshAgentEvaluations: () => post<any>('/agent-evaluations/refresh', {}),
  episodes: (params?: { search?: string; agent?: string; project?: string }) => {
    const query = new URLSearchParams()
    if (params?.search) query.set('search', params.search)
    if (params?.agent) query.set('agent', params.agent)
    if (params?.project) query.set('project', params.project)
    const suffix = query.size ? `?${query.toString()}` : ''
    return get<{ episodes: EpisodeSummary[]; total: number }>(`/episodes${suffix}`)
  },
  episode: (id: string) => get<EpisodeDetail>(`/episodes/${encodeURIComponent(id)}`),
  startReplay: (episodeId: string, agentId: string, mode: string) =>
    post<{ replay_id: string; status: string }>(`/episodes/${episodeId}/replays`, {
      agent_id: agentId,
      context_mode: mode,
    }),
  replayDetail: (id: string) => get<any>(`/replays/${id}`),
  replayTargets: () => get<{ targets: { agent_type: string; display_name: string; status: string; available: boolean; version: string | null }[] }>('/replay-targets'),
  compare: (a: string, b: string) => get<any>(`/compare?a=${a}&b=${b}`),
  judge: (body: { pair: string[]; kind: string; reason_tags: string[]; note: string }) =>
    post<any>('/judgments', body),
  recommend: (task: string, projectId?: string, profile?: TaskProfile | null) =>
    post<any>('/recommend', { task, project_id: projectId ?? null, profile: profile ?? null }),
  similar: (query: string, projectId?: string) => {
    const params = new URLSearchParams({ query })
    if (projectId) params.set('project_id', projectId)
    return get<{ similar: any[] }>(`/similar?${params.toString()}`).then((r) => r.similar)
  },
  activity: () => get<{ activity: any[] }>('/activity'),
  collabs: () => get<any>('/collabs'),
  collabTemplateRecommendation: (episodeId: string) =>
    get<any>(`/collab-template-recommendation?episode_id=${encodeURIComponent(episodeId)}`),
  runCollab: (
    mode: string,
    episodeId: string,
    agents: string[],
    options?: { template_id?: string; assignments?: { key: string; agent: string }[] },
  ) => post<any>('/collabs', {
    mode,
    episode_id: episodeId,
    agents,
    template_id: options?.template_id ?? '',
    assignments: options?.assignments ?? [],
  }),
  settings: () => get<any>('/settings'),
  pricingCatalog: () => get<any>('/pricing-catalog'),
  refreshPricingCatalog: () => post<any>('/pricing-catalog/refresh', {}),
  updateSettings: (body: {
    replay_per_day?: number
    default_language?: 'zh' | 'en'
    value_policy?: string
    monthly_cost_budget_usd?: number | null
    cost_budget_enabled?: boolean
    cost_warning_percent?: number
  }) => post<any>('/settings', body),

  // UI-16/17: providers
  providers: () => get<{ providers: ProviderInfo[] }>('/providers'),
  providerPresets: () =>
    get<{ presets: { id: string; display_name: string; type: string; base_url: string; key_prefix?: string; requires_key?: boolean }[] }>(
      '/providers/presets',
    ),
  detectProvider: (apiKey: string) =>
    post<{ detected: boolean; suggested: { display_name: string; type: string; base_url: string } | null; candidates: { display_name: string; type: string; base_url: string }[]; hint: string }>(
      '/providers/detect',
      { api_key: apiKey },
    ),
  createProvider: (body: {
    display_name: string
    type: string
    base_url: string
    api_key?: string | null
    roles: string[]
    models: { id: string; alias?: string }[]
  }) => post<{ provider: ProviderInfo }>('/providers', body).then((r) => r.provider),
  updateProvider: (
    providerId: string,
    body: Partial<{
      display_name: string
      type: string
      base_url: string
      api_key: string | null
      roles: string[]
      models: { id: string; alias?: string }[]
      selected_model: string
    }>,
  ) => post<{ provider: ProviderInfo }>(`/providers/${providerId}`, body, 'PATCH').then((r) => r.provider),
  deleteProvider: (providerId: string) => del(`/providers/${providerId}`),
  testProvider: (providerId: string) =>
    post<any>(`/providers/${providerId}/test`, {}).then((r) => r),
  providerModels: (providerId: string) =>
    get<{ provider_id: string; ok: boolean; models: { id: string; alias?: string }[]; error: string | null }>(
      `/providers/${providerId}/models`,
    ),

  // UI-18: manual agent ratings
  ratings: (agentId?: string) =>
    get<{ ratings: AgentRating[] }>(`/ratings${agentId ? `?agent_id=${agentId}` : ''}`).then(
      (r) => r.ratings,
    ),
  submitRating: (body: {
    agent_id: string
    episode_id: string
    dimensions: RatingDimensions
    overall_preference: number
    would_use_again: string
    comment: string
  }) => post<any>('/ratings', body),

  // v0.4 P15: control center
  overview: () => get<any>('/overview'),
  costCenter: () => get<any>('/cost'),
  taskCandidates: (taskId: string) => post<any>(`/tasks/${taskId}/candidates`, {}),
  // v0.4 P9: local agent discovery
  agentDiscovery: () => get<{ agents: DetectedAgent[] }>('/agents/discovery').then((r) => r.agents),
  executionTargets: () => get<{ targets: {
    agent_id: string
    agent_type: string
    display_name: string
    description?: string
    description_zh?: string
    kind: string
    version?: string | null
  }[] }>('/execution-targets').then((r) => r.targets),
  agentDoctor: () => get<AgentDoctor>('/agents/doctor'),
  linkAgent: (agentType: string) => post<any>(`/agents/${agentType}/link`, {}),
  unlinkAgent: (agentType: string) => post<any>(`/agents/${agentType}/unlink`, {}),
  linkAllAgents: () => post<any>('/agents/link-all', {}),
  captureAgent: (agentType: string) => post<any>(`/agents/${agentType}/capture`, {}),
  captureAgentLlm: (agentType: string) => post<any>(`/agents/${agentType}/capture-llm`, {}),
  listSessions: () => get<any>('/sessions'),
  sessionDetail: (agent: string, sessionId: string, offset = 0, limit = 200) =>
    get<any>(`/sessions/${agent}/${sessionId}?offset=${offset}&limit=${limit}`),
  previewSessionTask: (sessions: { agent_id: string; session_id: string }[]) =>
    post<any>('/sessions/task-preview', { sessions }),
  createTaskFromSessions: (body: {
    sessions: { agent_id: string; session_id: string }[]
    title: string
    description: string
    project_id: string
    project_path?: string
  }) => post<any>('/sessions/task', body),
  createSessionTask: (agent: string, sessionId: string, body: any) =>
    post<any>(`/sessions/${agent}/${sessionId}/task`, body),
  createTaskFromProposal: (agent: string, sessionId: string, body: any) =>
    post<any>(`/sessions/${agent}/${sessionId}/task-from-proposal`, body),
  skillFeedback: (body: any) => post<any>('/skills/feedback', body),
  skillStats: () => get<any>('/skills/stats'),
  analyzeSession: (agent: string, sessionId: string, lang?: string) =>
    post<any>(`/sessions/${agent}/${sessionId}/analyze`, { lang: lang ?? 'en' }),
  extractTasks: (body: any) => post<any>('/sessions/extract-tasks', body),
  // UI-19: result marks
  mark: (episodeId: string, agentId: string, mark: string, note: string) =>
    post<any>('/marks', { episode_id: episodeId, agent_id: agentId, mark, note }),

  // --- Task Studio (UI-20..30) ---
  tasks: () => get<{ tasks: TaskInfo[] }>('/tasks'),
  createTask: (body: { title: string; description: string; project_id: string; project_path?: string }) =>
    post<TaskInfo>('/tasks', body),
  task: (taskId: string) => get<TaskInfo>(`/tasks/${taskId}`),
  taskCostEstimates: (taskId: string) => get<any>(`/tasks/${taskId}/cost-estimates`),
  taskChildren: (taskId: string) => get<any>(`/tasks/${taskId}/children`),
  createTaskChildren: (taskId: string, children: any[]) =>
    post<any>(`/tasks/${taskId}/children`, { children }),
  routePrediction: (taskId: string, route: any) =>
    post<any>(`/tasks/${taskId}/route-prediction`, { route }),
  executionOptions: (taskId: string, policy?: string) =>
    post<any>(`/tasks/${taskId}/execution-options`, { policy: policy ?? 'balanced' }),
  splitProposal: (taskId: string, lang?: string) =>
    post<any>(`/tasks/${taskId}/split-proposal`, { lang: lang ?? 'en' }),
  splitRefine: (taskId: string, candidate: any, lang?: string) =>
    post<any>(`/tasks/${taskId}/split-refine`, { lang: lang ?? 'en', candidate }),
  listTemplates: (primaryType?: string) =>
    get<any>(`/templates${primaryType ? `?primary_type=${primaryType}` : ''}`),
  recommendTemplate: (taskId: string) =>
    post<any>(`/tasks/${taskId}/recommend-template`, {}),
  planFromTemplate: (taskId: string, templateId: string, lang?: string) =>
    post<any>(`/tasks/${taskId}/plan-from-template`, { lang: lang ?? 'en', template_id: templateId }),
  deleteTask: (taskId: string) => del(`/tasks/${taskId}`),
  endTask: (taskId: string) => post<TaskInfo>(`/tasks/${taskId}/end`, {}),
  archiveTask: (taskId: string) => post<TaskInfo>(`/tasks/${taskId}/archive`, {}),
  unarchiveTask: (taskId: string) => post<TaskInfo>(`/tasks/${taskId}/unarchive`, {}),
  updateTask: (taskId: string, body: Partial<{ title: string; description: string; project_id: string; project_path: string }>) =>
    post<TaskInfo>(`/tasks/${taskId}`, body, 'PATCH'),
  // UI-22/23: profile (manual + AI)
  setProfile: (taskId: string, profile: Partial<TaskProfile>) =>
    post<TaskInfo>(`/tasks/${taskId}/profile`, profile),
  analyzeTask: (taskId: string, lang?: string) =>
    post<{ task: TaskInfo; ai_suggested: boolean }>(`/tasks/${taskId}/analyze`, { lang: lang ?? 'en' }),
  // UI-24/25/26: plan
  savePlan: (taskId: string, strategy: string, steps: any[]) =>
    post<TaskInfo>(`/tasks/${taskId}/plan`, { strategy, steps }),
  planLlm: (taskId: string, lang?: string) =>
    post<any>(`/tasks/${taskId}/plan/llm`, { lang: lang ?? 'en' }),
  replanLlm: (taskId: string, body: { failure?: string; progress?: string; lang?: string }) =>
    post<any>(`/tasks/${taskId}/replan-llm`, { ...body, lang: body.lang ?? 'en' }),
  updatePlanSteps: (taskId: string, steps: any[], strategy?: string) =>
    post<TaskInfo>(`/tasks/${taskId}/plan/steps`, { steps, strategy }),
  approvePlan: (taskId: string) => post<TaskInfo>(`/tasks/${taskId}/plan/approve`, {}),
  // UI-29: execute
  executeTask: (taskId: string, agent?: string) =>
    post<{ run: TaskRun; task: TaskInfo }>(`/tasks/${taskId}/execute`, agent ? { agent } : {}),
  executeTree: (taskId: string, agents: Record<string, string>, llmAssign?: boolean) =>
    post<any>(`/tasks/${taskId}/execute-tree`, { agents, llm_assign: llmAssign ?? false }),
  assignAgents: (taskId: string, llm?: boolean) =>
    post<any>(`/tasks/${taskId}/assign-agents`, { llm: llm ?? false }),
  applyAgentAssignments: (taskId: string, assignments: Record<string, string>) =>
    post<any>(`/tasks/${taskId}/assign-agents/apply`, { assignments }),
  taskRun: (taskId: string) => get<TaskRun>(`/tasks/${taskId}/run`),
  // UI-30: evaluation
  acceptTask: (taskId: string, agent?: string) => {
    const query = agent ? `?agent=${encodeURIComponent(agent)}` : ''
    return post<TaskInfo>(`/tasks/${taskId}/accept${query}`, {})
  },
  judgeTaskLlm: (taskId: string, agent?: string) => {
    const query = agent ? `?agent=${encodeURIComponent(agent)}` : ''
    return post<{ rating: AgentRating; ledger_seq: number }>(`/tasks/${taskId}/judge-llm${query}`, {})
  },
  // Fixed evaluation form (J evidence)
  taskReport: (taskId: string) => get<TaskReportView>(`/tasks/${taskId}/report`),
  fillTaskReport: (taskId: string, form: Record<string, unknown>) =>
    post<{ step_id: string; metrics: Record<string, number> }>(`/tasks/${taskId}/report`, form),
  // Baseline-model probe: the only local source of Matrix A
  baselineProbeStatus: (taskId: string, agent: string) =>
    get<BaselineProbeStatus>(`/tasks/${taskId}/baseline-probe?agent=${encodeURIComponent(agent)}`),
  runBaselineProbe: (taskId: string, agent: string) =>
    post<BaselineProbeRun>(`/tasks/${taskId}/baseline-probe?agent=${encodeURIComponent(agent)}`, {}),
}

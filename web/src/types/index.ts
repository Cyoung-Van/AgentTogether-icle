// API client types — mirror of ICLE core objects (read models only).
export interface Health {
  status: string
  version: string
  episodes: number
}

export interface IntelligenceOperation {
  operation_id: string
  operation: string
  label: string
  label_zh: string
  started_at: string
  duration_ms: number
  outcome?: 'completed' | 'failed'
  ended_at?: string
}

export interface IntelligenceStatus {
  state: 'idle' | 'busy'
  active_count: number
  active: IntelligenceOperation[]
  last: IntelligenceOperation | null
  checked_at: string
  provider: {
    configured: boolean
    name: string
    display_name: string
    provider_id: string | null
    model: string
    error: string | null
  }
}

export interface IntelligenceModel {
  provider_id: string
  provider: string
  model_id: string
  model: string
  status: string
  selected: boolean
}

export interface IntelligenceModels {
  models: IntelligenceModel[]
  current: { provider_id: string; model_id: string } | null
}

export interface AgentInfo {
  agent_id: string
  agent_type?: string
  display_name?: string
  description?: string
  description_zh?: string
  status?: string
  installed?: boolean
  linked?: boolean
  execution_supported?: boolean
  capture_supported?: boolean
  capabilities?: string[]
  version?: string | null
  session_capture_count?: number
  provider_id?: string
  model_id?: string
  replays: number
  score?: number | null
  local_score?: number | null
  score_source?: 'external+local' | 'external' | 'local' | 'none'
  confidence?: string
  rank?: number | null
  measurement?: {
    publication_status?: string
    observed_axis_count?: number
    observed_axes?: string[]
  }
  model_identity?: { model: string | null; provider: string | null; source: string; confidence: string }
  external_baseline?: { score: number | null; status: string; confidence: string; match_strength?: 'agent+model' | 'model-only' | 'agent-only' | 'agent+model-family' | 'model-family'; reason: string; records: any[]; sources_checked?: any[] }
  decision_profile?: {
    measured_domains?: string[]
    strongest_domain?: string | null
    cost_profile_domains?: string[]
    unresolved_domains?: string[]
    profile_version?: string
    coverage?: {
      domain_count?: number
      domains_with_public_prior?: string[]
      domains_with_exact_model_experience?: string[]
      domains_with_api_cost?: string[]
      domains_with_execution_cost?: string[]
      agent_shell_evidence_count?: number
      exact_model_observation_count?: number
    }
    agent_shell_evidence?: { marks?: number; ratings?: number; replays?: number; total?: number; scope?: string }
    capabilities?: any[]
    measurement?: {
      schema_version?: string
      publication_status?: string
      ranking?: boolean
      subject_id?: string | null
      observed_axis_count?: number
      unavailable_axis_count?: number
      not_claimed?: string[]
      axes?: Record<string, {
        status?: string
        mean?: number | null
        a?: number | null
        b?: number | null
        j?: number | null
        c?: number | null
        reason?: string
        evidence_count?: number
      }>
    }
  }
  outcomes: number
  pairwise: number
  flags: string[]
}

// v0.4 P9: locally detected agent CLI (read-only scan result)
export interface DetectedAgent {
  detection_id: string
  agent_type: string
  display_name: string
  description?: string
  description_zh?: string
  executable_path: string | null
  version: string | null
  detection_sources: string[]
  native_home: string | null
  connection_modes: string[]
  execution_supported?: boolean
  capture_supported?: boolean
  status: string
  capabilities: string[]
  session_capture_count: number
  last_probe: string | null
  probe_errors: string[]
}

export interface AgentDoctor {
  schema_version: string
  agents: {
    agent_type: string
    display_name: string
    status: string
    checks: { executable: boolean; version: boolean; native_home: boolean; session_capture: boolean; acp: boolean | null }
    probe_errors: string[]
  }[]
  summary: { total: number; ready: number; needs_attention: number }
  created_at: string
}

export interface EpisodeSummary {
  episode_id: string
  project_id: string
  agent: string
  session: string
  request: string
  created_at: string
  replays: number
}

export interface ReplayInfo {
  replay_id: string
  episode_id: string
  agent: string
  status: string
  mode: string
  duration_ms: number
}

export interface EpisodeDetail {
  episode: {
    episode_id: string
    project_id: string
    source_session: string
    source_agent_revision: { agent_id: string }
    task_start: {
      original_user_request: string
      execution_provider: string
      project_snapshot: {
        project_path?: string
        git_revision?: string
        git_dirty?: boolean
        workspace_sha256?: string
      }
      turn_range?: { from_seq: number; to_seq: number } | null
    }
    created_at: string
  }
  replays: ReplayInfo[]
}

export interface ProviderModel {
  id: string
  alias?: string
}

export interface ProviderInfo {
  provider_id: string
  display_name: string
  type: string
  base_url: string
  secret_ref: string
  configured: boolean
  models: ProviderModel[]
  roles: string[]
  status: string
  last_checked_at: string | null
  selected_model?: string
}

export interface RatingDimensions {
  requirement_fit: number
  correctness: number
  efficiency: number
  autonomy: number
  maintainability: number
}

export interface AgentRating {
  rating_id: string
  source: string
  agent_id: string
  episode_id: string
  dimensions: RatingDimensions
  overall_preference: number
  would_use_again: string
  comment: string
  created_at: string
}

// --- Task Studio (UI-20..30) ---
export interface TaskProfile {
  primary_type: string
  subtype?: string
  difficulty: string
  risk: string
  context_requirement: string
  tool_requirement?: string[]
  estimated_duration?: string
  decomposition?: string
  review?: string
  reason?: string
  source?: string
}

export interface TaskStep {
  step_id: string
  title: string
  description: string
  type: string
  recommended_agent: string
  context_policy: string
  risk: string
  expected_output?: string
  verification?: string
  depends_on?: string[]
  status?: string
}

export interface TaskPlan {
  plan_id: string
  strategy: string
  steps: TaskStep[]
  status: string
  requires_user_approval?: boolean
  planner?: string
}

export interface TaskInfo {
  task_id: string
  title: string
  description: string
  project_id: string
  project_path?: string
  status: string
  profile: TaskProfile | null
  plan: TaskPlan | null
  runs?: string[]
  episode_id: string | null
  archived_at?: string | null
  source_sessions?: { agent_id: string; session_id: string }[]
  parent_task_id?: string | null
  sibling_order?: number | null
  strategy?: string
  step_count?: number
  created_at: string
  updated_at: string
}

export interface TaskRunStep {
  run_id: string
  task_id: string
  step_id: string
  agent: string
  status: string
  stdout_tail: string
  stderr_tail: string
  exit_code: number | null
  duration_ms: number
  report: AgentTaskReport | null
  report_status: 'observed' | 'missing' | 'invalid'
  report_origin?: 'agent' | 'user'
  created_at: string
}

export interface TaskRun {
  run_id: string
  task_id: string
  status: string
  steps: TaskRunStep[]
  final_step_id?: string | null
  workspace_init?: {
    mode: string
    snapshot_policy?: string
    source_commit?: string | null
    source_dirty?: boolean | null
    excluded_files?: string[]
    reason?: string
  }
  created_at?: string
}

// Fixed evaluation form: the per-task evidence J is generated from.
export interface AgentTaskReport {
  schema_version: string
  requirements_total?: number
  requirements_met?: number
  verification_ran?: boolean
  verification_passed?: boolean
  tests_total?: number
  tests_passed?: number
  input_tokens?: number
  output_tokens?: number
  cache_read_tokens?: number
  reasoning_tokens?: number
  duration_s?: number
  files_changed?: string[]
  commands_run?: string[]
  blocked?: boolean
  blocked_reason?: string
  summary?: string
  ignored_fields?: string[]
}

export interface TaskReportEntry {
  task_id: string
  step_id: string
  agent: string
  order: number
  report_status: 'observed' | 'missing' | 'invalid'
  report_origin: 'agent' | 'user'
  report: AgentTaskReport | null
}

// Baseline-model probe: same model, no agent harness → local Matrix A.
export interface BaselineProbeTarget {
  available: boolean
  agent: string
  reason?: string
  hint?: string
  model?: string
  provider?: string
  provider_id?: string
  execution_target?: string
  baseline_subject_id?: string
}

export interface BaselineProbeStatus {
  task_id: string
  agent: string
  target: BaselineProbeTarget
  baseline_subject_id: string | null
  probed_task_count: number
  required_task_count: number
  ready: boolean
  probed_task_ids: string[]
}

export interface BaselineProbeRun {
  task_id: string
  agent: string
  target: BaselineProbeTarget
  run_id: string
  status: string
  report: { metrics: Record<string, number>; report_status: string }
  measurement: { status: string; reason?: string; subject_id?: string }
}

export interface TaskReportView {
  task_id: string
  report_contract_id: string
  template: AgentTaskReport
  fields: string[]
  metric_contract: {
    metric_id: string
    min: number
    max: number
    direction: string
    weight: number
    required: boolean
  }[]
  intelligence_enabled: boolean
  entries: TaskReportEntry[]
  consolidated: {
    source: 'finishing_agent' | 'intelligence_summary' | 'single_agent' | 'none'
    report: AgentTaskReport | null
    report_status: 'observed' | 'missing'
    metrics: Record<string, number>
    reason: string
    inputs: { task_id: string; agent: string; order: number; report_status: string }[]
  }
}

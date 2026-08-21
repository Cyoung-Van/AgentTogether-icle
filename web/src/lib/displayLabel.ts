import type { Lang } from '../i18n'

type PhaseCopy = { title: string; description: string }

const TEMPLATE_NAMES: Record<string, string> = {
  feature: '功能开发',
  bugfix: '缺陷修复',
  refactor: '代码重构',
  migration: '迁移',
  review: '代码评审',
  release: '版本发布',
  incident: '故障响应',
  research: '调研',
  data_analysis: '数据分析',
  security_audit: '安全审计',
  documentation: '文档编写',
  production_readiness: '生产就绪',
  blank: '空白模板',
}

const TEMPLATE_PHASES: Record<string, Record<string, PhaseCopy>> = {
  feature: {
    requirements: { title: '需求澄清', description: '澄清需求、边界和验收标准' },
    analysis: { title: '现有系统分析', description: '阅读现有实现、接口和依赖' },
    design: { title: '设计', description: '设计数据结构、API 和交互，记录兼容性与风险' },
    implement: { title: '实现', description: '实现核心功能' },
    test: { title: '测试', description: '进行单元、集成和真实场景验证' },
    docs: { title: '文档与验收', description: '更新文档并完成验收' },
  },
  bugfix: {
    reproduce: { title: '复现问题', description: '稳定复现问题并保存失败案例' },
    root_cause: { title: '根因分析', description: '定位真正根因，排除表面症状' },
    fix_design: { title: '修复设计', description: '选择风险最小的修复方案' },
    implement: { title: '实施修复', description: '应用修复方案' },
    regression: { title: '回归测试', description: '为问题补充测试并运行现有测试套件' },
    boundary: { title: '边界验证', description: '检查相邻场景和副作用' },
  },
  refactor: {
    baseline: { title: '建立基线', description: '确认全部测试通过，并记录行为/性能基线' },
    analyze: { title: '分析', description: '识别代码异味、耦合和重复' },
    design: { title: '目标设计', description: '定义目标结构，明确哪些行为不得改变' },
    incremental: { title: '渐进式重构', description: '以小步变更完成重构' },
    verify: { title: '持续验证', description: '每个阶段后运行测试' },
    final: { title: '最终验证', description: '确认行为不变、没有性能回归并更新文档' },
  },
  migration: {
    inventory: { title: '现状盘点', description: '调查待迁移内容的当前状态' },
    dependency: { title: '依赖分析', description: '梳理依赖方和耦合关系' },
    design: { title: '迁移设计', description: '设计迁移路径和切换策略' },
    dry_run: { title: '演练', description: '在安全副本上执行迁移演练' },
    migrate: { title: '执行迁移', description: '执行正式迁移' },
    verify: { title: '迁移验证', description: '验证迁移后状态的正确性' },
    rollback: { title: '回滚准备', description: '记录并验证回滚路径' },
  },
  review: {
    intent: { title: '理解意图', description: '阅读任务、PR 或设计目标' },
    correctness: { title: '正确性评审', description: '确认是否满足需求并排查明显问题' },
    architecture: { title: '架构与可维护性', description: '检查边界、耦合和可维护性' },
    tests: { title: '测试评审', description: '确认测试是否覆盖关键场景' },
    risk: { title: '风险评审', description: '检查安全、性能和兼容性' },
    report: { title: '评审报告', description: '将发现分为阻断、重大、一般和建议' },
  },
  release: {
    scope: { title: '发布范围', description: '确认版本、功能范围和破坏性变更' },
    validation: { title: '发布前验证', description: '检查 CI、测试、安全和依赖' },
    prepare: { title: '准备发布', description: '更新版本、变更日志、文档和制品' },
    deploy: { title: '部署/发布', description: '先部署到预发布环境，再部署生产环境' },
    smoke: { title: '冒烟测试', description: '部署后验证核心路径' },
    monitor: { title: '回滚/监控', description: '观察指标并确认回滚准备就绪' },
    closeout: { title: '发布收尾', description: '整理发布说明并完成复盘' },
  },
  incident: {
    triage: { title: '事件分诊', description: '确认症状，评估影响范围和严重程度' },
    containment: { title: '影响遏制', description: '阻止影响继续扩大' },
    investigation: { title: '调查', description: '收集日志并定位故障来源' },
    mitigation: { title: '缓解/修复', description: '应用临时缓解措施或修复方案' },
    recovery: { title: '恢复验证', description: '确认服务恢复且关键指标正常' },
    root_cause: { title: '根因复盘', description: '恢复后分析根本原因' },
    followup: { title: '后续行动', description: '完成事后复盘和预防任务' },
  },
  research: {
    question: { title: '定义问题', description: '准确界定研究问题' },
    search: { title: '检索', description: '收集相关来源' },
    screening: { title: '来源筛选', description: '筛选权威且相关的来源' },
    extraction: { title: '提取证据', description: '提取带引用的关键事实' },
    cross_validation: { title: '交叉验证', description: '跨来源核验并识别冲突' },
    synthesis: { title: '综合分析', description: '综合研究发现' },
    conclusion: { title: '结论', description: '回答最初的问题' },
    sources: { title: '来源整理', description: '整理引用和局限性' },
  },
  data_analysis: {
    question: { title: '定义问题', description: '准确陈述分析问题' },
    acquire: { title: '检查/获取数据', description: '定位、访问并检查数据' },
    clean: { title: '清洗与校验数据', description: '清洗、校验数据并记录转换' },
    eda: { title: '探索性分析', description: '探索分布、缺口和异常' },
    model: { title: '分析/建模', description: '执行核心分析或建模' },
    validate: { title: '验证结果', description: '对结果进行合理性检查和验证' },
    visualize: { title: '可视化', description: '制作图表和数据表' },
    report: { title: '解读与报告', description: '撰写结论和局限性' },
  },
  security_audit: {
    scope: { title: '定义范围', description: '界定范围和威胁面：入口、信任边界、凭据' },
    config: { title: '配置评审', description: '检查部署和运行时配置' },
    code: { title: '代码评审', description: '检查应用代码中的漏洞' },
    deps: { title: '依赖评审', description: '检查依赖中的已知漏洞' },
    verify: { title: '验证发现', description: '确认疑似问题，发现不等于已确认漏洞' },
    classify: { title: '风险分级', description: '为每个发现评定严重程度和风险' },
    remediation: { title: '修复计划', description: '按优先级提出修复建议' },
  },
  documentation: {
    audience: { title: '定义读者与目标', description: '定义目标读者和文档目标' },
    audit: { title: '审查现有文档', description: '检查当前文档覆盖情况' },
    structure: { title: '设计结构', description: '规划信息架构' },
    write: { title: '编写/更新内容', description: '编写或更新文档内容' },
    examples: { title: '示例与用法', description: '补充示例和使用说明' },
    accuracy: { title: '准确性检查', description: '验证技术内容准确无误' },
    publish: { title: '链接/构建验证与发布', description: '验证链接和构建并发布' },
  },
  production_readiness: {
    functional: { title: '功能验证', description: '确认核心功能端到端可用' },
    testing: { title: '测试', description: '覆盖单元、集成和边界场景' },
    security: { title: '安全', description: '完成安全评审和加固' },
    performance: { title: '性能', description: '检查延迟、吞吐量和资源使用' },
    observability: { title: '可观测性', description: '配置日志、指标、追踪和告警' },
    data: { title: '数据/备份', description: '验证数据完整性和备份恢复' },
    deploy: { title: '部署', description: '建立可重复的部署路径' },
    rollback: { title: '回滚', description: '验证可用的回滚路径' },
    ops_docs: { title: '运维文档', description: '整理运行手册和运维文档' },
    gono: { title: '上线/不上线决策', description: '完成最终决策和签字确认' },
  },
}

const ENUM_LABELS_EN: Record<string, Record<string, string>> = {
  strategy: { DIRECT: 'Direct execution', PLAN_FIRST: 'Plan first', DECOMPOSE: 'Decompose', AUTHOR_REVIEWER: 'Author + reviewer', PARALLEL_COMPARE: 'Parallel compare', HANDOFF: 'Handoff' },
  step_type: { analysis: 'Analysis', design: 'Design', implementation: 'Implementation', review: 'Review', verification: 'Verification', research: 'Research', documentation: 'Documentation', other: 'Other' },
  context_policy: { CLEAN: 'Clean context', PROJECT_STATE: 'Project state', ARTIFACT_ONLY: 'Artifact only' },
  provider_type: { 'openai-compatible': 'OpenAI-compatible', local: 'Local service', custom: 'Custom' },
  provider_role: { extraction: 'Task extraction', judging: 'Result judging', planning: 'Task planning', routing: 'Routing', general: 'General intelligence' },
  agent_status: { linked: 'Linked', runnable: 'Runnable', unavailable: 'Not installed', detected: 'Detected', historical: 'Historical' },
  collab_mode: { template: 'Template workflow', handoff: 'Handoff', parallel: 'Parallel compare', review: 'Author + reviewer', 'parallel-compare': 'Parallel compare', 'author-reviewer': 'Author + reviewer' },
  collab_role: { author: 'Author', reviewer: 'Reviewer', integrator: 'Integrator', 'parallel-compare': 'Parallel compare', handoff: 'Handoff' },
  collab_status: { queued: 'Queued', running: 'Running', completed: 'Completed', failed: 'Failed', pending: 'Pending' },
  replay_status: { queued: 'Queued', running: 'Running', completed: 'Completed', failed: 'Failed' },
  replay_mode: { CLEAN: 'Clean context', PROJECT_STATE: 'Project state', SELECTED_HISTORY: 'Selected history' },
  activity_type: { task: 'Task', run: 'Run', replay: 'Replay', episode: 'Episode', rating: 'Rating', judgment: 'Judgment', collab: 'Collab', mark: 'Mark' },
  mark: { accept: 'Accept', edit: 'Edit', reject: 'Reject', redo: 'Redo' },
  judgment: { prefer_a: 'Prefer A', tie: 'Tie', prefer_b: 'Prefer B', inconclusive: 'Inconclusive' },
  policy: { balanced: 'Balanced', quality_first: 'Quality first', cost_first: 'Cost first', speed_first: 'Speed first', fast: 'Fast' },
  confidence: { high: 'High confidence', medium: 'Medium confidence', low: 'Low confidence', 'very_low': 'Very low confidence', 'insufficient-data': 'Insufficient data' },
  skill_mode: {
    'analyze-session': 'Session analysis',
    'analyze-task': 'Task profile',
    'plan-task': 'Task planning',
    'replan-task': 'Replan',
    'split-task': 'Split task',
    'template-customize': 'Template customize',
    'judge-result': 'Rate result',
    'judge-pair': 'Compare results',
    'summarize-reports': 'Summarize reports',
    'extract-tasks': 'Task extraction',
  },
  execution_provider: { direct_cli: 'Local CLI', provider_api: 'Provider API', openai: 'OpenAI-compatible API', kimi: 'Kimi CLI', none: 'Not configured' },
  agent_type: { kimi: 'Kimi Code', hermes: 'Hermes', claude: 'Claude Code', codex: 'Codex', cursor: 'Cursor', gemini: 'Gemini CLI', opencode: 'OpenCode', openclaw: 'OpenClaw', aider: 'Aider', pi: 'Pi', qwen: 'Qwen Code' },
  capability_axis: {
    reasoning: 'Reasoning', coding: 'Coding', agentic_coding: 'Agentic coding',
    mathematics: 'Mathematics', data_analysis: 'Data analysis', language: 'Language',
    instruction_following: 'Instruction following',
  },
}

const ENUM_LABELS: Record<string, Record<string, string>> = {
  primary_type: {
    CODING: '编码', RESEARCH: '调研', ANALYSIS: '分析', WRITING: '写作', PLANNING: '规划',
    DATA: '数据', SYSTEM_OPERATION: '系统运维', MULTIMODAL: '多模态', OTHER: '其他',
  },
  subtype: {
    implementation: '实现', debugging: '调试', refactor: '重构', review: '评审', testing: '测试',
    architecture: '架构', integration: '集成', search: '检索', literature: '文献', comparison: '对比',
    verification: '验证', synthesis: '综合', project: '项目', workflow: '流程', decision: '决策', decomposition: '拆分',
  },
  difficulty: { D1: 'D1 · 简单', D2: 'D2 · 中等', D3: 'D3 · 较难', D4: 'D4 · 困难', D5: 'D5 · 高难' },
  risk: { R0: 'R0 · 无风险', R1: 'R1 · 低风险', R2: 'R2 · 中风险', R3: 'R3 · 高风险' },
  context_requirement: { LOW: '低', MEDIUM: '中', HIGH: '高' },
  decomposition: { not_recommended: '不建议拆分', recommended: '建议拆分', required: '必须拆分' },
  review: { not_recommended: '不建议评审', recommended: '建议评审', required: '必须评审' },
  estimated_duration: { short: '短', medium: '中', long: '长', unknown: '未知' },
  tool_requirement: { filesystem: '文件系统', shell: '终端', git: 'Git', web: '网络', mcp: 'MCP', none: '无' },
  strategy: {
    DIRECT: '直接执行', PLAN_FIRST: '先规划后执行', DECOMPOSE: '拆分执行', AUTHOR_REVIEWER: '作者-评审',
    PARALLEL_COMPARE: '并行对比', HANDOFF: '接力交接',
  },
  step_type: { analysis: '分析', design: '设计', implementation: '实现', review: '评审', verification: '验证', research: '调研', documentation: '文档', other: '其他' },
  context_policy: { CLEAN: '干净上下文', PROJECT_STATE: '项目状态', ARTIFACT_ONLY: '仅交接制品' },
  provider_type: { 'openai-compatible': 'OpenAI 兼容', local: '本地服务', custom: '自定义' },
  execution_provider: { direct_cli: '本地 CLI', provider_api: 'Provider API', openai: 'OpenAI 兼容 API', kimi: 'Kimi CLI', none: '未配置' },
  provider_role: { extraction: '任务提取', judging: '结果评估', planning: '任务规划', routing: '路由推荐', general: '通用智能' },
  agent_status: { linked: '已链接', runnable: '可运行', unavailable: '未安装', detected: '已识别', historical: '历史记录' },
  agent_type: { kimi: 'Kimi Code', hermes: 'Hermes', claude: 'Claude Code', codex: 'Codex', cursor: 'Cursor', gemini: 'Gemini CLI', opencode: 'OpenCode', openclaw: 'OpenClaw', aider: 'Aider', pi: 'Pi', qwen: 'Qwen Code' },
  collab_mode: { template: '模板工作流', handoff: '接力交接', parallel: '并行对比', review: '作者-评审', 'parallel-compare': '并行对比', 'author-reviewer': '作者-评审' },
  collab_role: { author: '作者', reviewer: '评审者', integrator: '整合者', 'parallel-compare': '并行对比', handoff: '接力交接' },
  collab_status: { queued: '排队中', running: '执行中', completed: '已完成', failed: '失败', pending: '等待中' },
  replay_status: { queued: '排队中', running: '执行中', completed: '已完成', failed: '失败' },
  replay_mode: { CLEAN: '干净上下文', PROJECT_STATE: '项目状态', SELECTED_HISTORY: '选定历史' },
  activity_type: { task: '任务', run: '执行', replay: '重放', episode: '经验', rating: '评价', judgment: '裁决', collab: '协作', mark: '标记' },
  mark: { accept: '接受', edit: '编辑', reject: '拒绝', redo: '重做' },
  judgment: { prefer_a: 'A 更好', tie: '平局', prefer_b: 'B 更好', inconclusive: '无法判断' },
  policy: { balanced: '均衡策略', quality_first: '质量优先', cost_first: '成本优先', speed_first: '速度优先', fast: '速度优先' },
  confidence: { high: '高置信度', medium: '中置信度', low: '低置信度', 'very_low': '很低置信度', 'insufficient-data': '证据不足' },
  skill_mode: {
    'analyze-session': '会话分析',
    'analyze-task': '任务画像',
    'plan-task': '任务规划',
    'replan-task': '失败重规划',
    'split-task': '拆分子任务',
    'template-customize': '模板定制',
    'judge-result': '结果评分',
    'judge-pair': '成对比较',
    'summarize-reports': '评估表汇总',
    'extract-tasks': '任务提取',
  },
  task_status: {
    draft: '草稿', profiled: '已建档', planned: '已规划', revised: '已修订', approved: '已批准', running: '执行中',
    review: '待评审', accepted: '已完成', failed: '失败', cancelled: '已结束', needs_input: '待输入', queued: '排队中', completed: '已完成',
  },
  source: { manual: '手动', llm: 'LLM 建议', rule: '规则' },
  capability_axis: {
    reasoning: '推理', coding: '编码', agentic_coding: '智能体编码',
    mathematics: '数学', data_analysis: '数据分析', language: '语言',
    instruction_following: '指令遵循',
  },
}

export function displayEnum(field: string, value: string | null | undefined, lang: Lang): string {
  if (!value) return '—'
  return lang === 'zh'
    ? ENUM_LABELS[field]?.[value] ?? value
    : ENUM_LABELS_EN[field]?.[value] ?? value
}

export function displayStatus(value: string | null | undefined, lang: Lang): string {
  return displayEnum('task_status', value, lang)
}

export function templateName(templateId: string, fallback: string, lang: Lang): string {
  return lang === 'zh' ? TEMPLATE_NAMES[templateId] ?? fallback : fallback
}

export function templatePhase(templateId: string, key: string, fallbackTitle: string, fallbackDescription: string, lang: Lang): PhaseCopy {
  if (lang !== 'zh') return { title: fallbackTitle, description: fallbackDescription }
  return TEMPLATE_PHASES[templateId]?.[key] ?? { title: fallbackTitle, description: fallbackDescription }
}

export function templatePhaseTitle(templateId: string, key: string, fallback: string, lang: Lang): string {
  return templatePhase(templateId, key, fallback, '', lang).title
}

export function templatePhaseDescription(templateId: string, key: string, fallback: string, lang: Lang): string {
  return templatePhase(templateId, key, '', fallback, lang).description
}

const STRATEGY_HELP_ZH: Record<string, string> = {
  DIRECT: '任务 → Agent。适合 D1/D2：单步骤，不单独规划。',
  PLAN_FIRST: '任务 → 计划 → 用户批准 → Agent。适合 D3 及以上。',
  DECOMPOSE: '任务 → 步骤 1 → 步骤 2 → 步骤 3。每个步骤可以使用不同 Agent。',
  AUTHOR_REVIEWER: 'Agent A → 制品 → Agent B 评审 → A 修订。',
  PARALLEL_COMPARE: 'A 与 B 并行执行 → 比较结果。适合设计、决策和高不确定性任务。',
  HANDOFF: 'Agent A → 仅交接制品 → Agent B。不会转移私有记忆。',
}

export function strategyHelp(strategy: string, fallback: string, lang: Lang): string {
  return lang === 'zh' ? STRATEGY_HELP_ZH[strategy] ?? fallback : fallback
}

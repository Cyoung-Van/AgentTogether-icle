import { azure, cyan, ice, lilac, steel } from './palettes'
import type { GalaxyLayer, PlanetNode, SpaceField } from './types'

const today: PlanetNode = {
  id: 'today',
  path: '/space/today',
  titleKey: 'home.title',
  blurbKey: 'space.blurb.today',
  icon: 'today',
  palette: ice,
  children: [
    { id: 'today-overview', path: '/today', titleKey: 'space.section.overview', blurbKey: 'space.section.overviewHint', palette: ice, icon: 'today' },
    { id: 'today-needs', path: '/today#needs-attention', titleKey: 'overview.needsYou', blurbKey: 'space.section.needsHint', palette: cyan, icon: 'tasks' },
    { id: 'today-active', path: '/today#active-work', titleKey: 'home.continue', blurbKey: 'space.section.activeHint', palette: azure, icon: 'tasks' },
    { id: 'today-resources', path: '/today#resources', titleKey: 'space.section.resources', blurbKey: 'space.section.resourcesHint', palette: steel, icon: 'cost' },
  ],
}

const taskList: PlanetNode = {
  id: 'task-list',
  path: '/tasks',
  titleKey: 'home.allTasks',
  blurbKey: 'space.blurb.taskList',
  icon: 'tasks',
  palette: cyan,
}

const newTask: PlanetNode = {
  id: 'new-task',
  path: '/tasks/new',
  titleKey: 'nav.newTask',
  blurbKey: 'space.blurb.newTask',
  icon: 'newTask',
  palette: { c0: '#dfe6dc', c1: '#a8b3a4', c2: '#6f776c', core: '#eef1ea' },
}

const episodes: PlanetNode = {
  id: 'episodes',
  path: '/episodes',
  titleKey: 'nav.episodes',
  blurbKey: 'space.blurb.episodes',
  icon: 'episodes',
  palette: { c0: '#d8e0e4', c1: '#9aa7ae', c2: '#6a7378', core: '#eef1f2' },
}

const collabs: PlanetNode = {
  id: 'collabs',
  path: '/collabs',
  titleKey: 'nav.collabs',
  blurbKey: 'space.blurb.collabs',
  icon: 'collabs',
  palette: { c0: '#d7e0dc', c1: '#9aaba6', c2: '#6a756f', core: '#eef1ee' },
}

const agentDir: PlanetNode = {
  id: 'agent-dir',
  path: '/agents',
  titleKey: 'nav.agents',
  blurbKey: 'space.blurb.agentDir',
  icon: 'agents',
  palette: azure,
}

const localAgents: PlanetNode = {
  id: 'local-agents',
  path: '/agents/local',
  titleKey: 'nav.localAgents',
  blurbKey: 'space.blurb.localAgents',
  icon: 'local',
  palette: { c0: '#d8dde4', c1: '#9aa3ae', c2: '#6a7178', core: '#eef0f2' },
}

const recommend: PlanetNode = {
  id: 'recommend',
  path: '/recommend',
  titleKey: 'nav.recommend',
  blurbKey: 'space.blurb.recommend',
  icon: 'recommend',
  palette: { c0: '#dce0e4', c1: '#a2a8b0', c2: '#6e747a', core: '#f0f1f2' },
}

const taskStudio: PlanetNode = {
  id: 'task-studio',
  path: '/space/tasks',
  titleKey: 'nav.tasks',
  blurbKey: 'space.blurb.tasks',
  icon: 'tasks',
  palette: cyan,
  children: [taskList, newTask, episodes, collabs],
}

const agents: PlanetNode = {
  id: 'agents',
  path: '/space/agents',
  titleKey: 'nav.agents',
  blurbKey: 'space.blurb.agents',
  icon: 'agents',
  palette: azure,
  children: [agentDir, localAgents, recommend],
}

const sessions: PlanetNode = {
  id: 'sessions',
  path: '/space/sessions',
  titleKey: 'nav.sessions',
  blurbKey: 'space.blurb.sessions',
  icon: 'sessions',
  palette: lilac,
  children: [
    { id: 'sessions-browse', path: '/sessions#browse', titleKey: 'space.section.browse', blurbKey: 'space.section.browseHint', palette: lilac, icon: 'sessions' },
    { id: 'sessions-task', path: '/sessions#task-preparation', titleKey: 'space.section.sessionTask', blurbKey: 'space.section.sessionTaskHint', palette: cyan, icon: 'newTask' },
    { id: 'sessions-analysis', path: '/sessions#analysis-preparation', titleKey: 'space.section.sessionAnalysis', blurbKey: 'space.section.sessionAnalysisHint', palette: azure, icon: 'episodes' },
  ],
}

const settings: PlanetNode = {
  id: 'settings',
  path: '/space/settings',
  titleKey: 'nav.settings',
  blurbKey: 'space.blurb.settings',
  icon: 'settings',
  palette: steel,
  children: [
    { id: 'settings-general', path: '/settings#general', titleKey: 'space.section.general', blurbKey: 'space.section.generalHint', palette: steel, icon: 'settings' },
    { id: 'settings-providers', path: '/providers', titleKey: 'nav.providers', blurbKey: 'space.section.providersHint', palette: azure, icon: 'providers' },
    { id: 'settings-pricing', path: '/settings#pricing', titleKey: 'settings.pricingCatalog', blurbKey: 'space.section.pricingHint', palette: ice, icon: 'cost' },
    { id: 'settings-budgets', path: '/settings#budgets', titleKey: 'space.section.budgets', blurbKey: 'space.section.budgetsHint', palette: cyan, icon: 'cost' },
    { id: 'settings-data', path: '/settings#data', titleKey: 'space.section.dataSafety', blurbKey: 'space.section.dataHint', palette: lilac, icon: 'settings' },
  ],
}

export const HOME_CENTER: PlanetNode = {
  id: 'home',
  path: '/',
  titleKey: 'nav.home',
  blurbKey: 'space.blurb.home',
  icon: 'today',
  palette: ice,
  children: [today, taskStudio, agents, sessions, settings],
}

// One tree defines centers, their direct entrances, and semantic parents.
// Nodes without children always resolve to the existing DOM workspace routes.
const GALAXIES: GalaxyLayer[] = []
const ALL_NODES: PlanetNode[] = []
const PARENTS = new Map<string, string | null>()
function indexNode(node: PlanetNode, parent: PlanetNode | null) {
  ALL_NODES.push(node)
  PARENTS.set(node.path, parent?.path ?? null)
  if (!node.children?.length) return
  GALAXIES.push({ id: node.id, path: node.path, titleKey: node.titleKey, center: node, parent, children: node.children })
  node.children.forEach(child => indexNode(child, node))
}
indexNode(HOME_CENTER, null)
// Legacy whole-page URLs remain available and return to their owning galaxy.
PARENTS.set('/sessions', '/space/sessions')
PARENTS.set('/settings', '/space/settings')
export const HOME_GALAXY = GALAXIES.find(g => g.path === '/')!
export const TASK_GALAXY = GALAXIES.find(g => g.path === '/space/tasks')!
export const AGENT_GALAXY = GALAXIES.find(g => g.path === '/space/agents')!

export const ALL_GALAXIES = GALAXIES

// Only known section fragments participate in navigation; unrelated fragments
// and query parameters keep the original workspace behavior.
export function navigationPath(location: { pathname: string; hash?: string }): string {
  const section = location.pathname + (location.hash ?? '')
  return PARENTS.has(section) ? section : location.pathname
}

export function galaxyForPath(pathname: string): GalaxyLayer | null {
  return GALAXIES.find((layer) => layer.path === pathname) ?? null
}

export function fieldForPath(pathname: string): SpaceField | null {
  const galaxy = galaxyForPath(pathname)
  if (!galaxy) return null
  return {
    id: galaxy.id,
    path: galaxy.path,
    titleKey: galaxy.titleKey,
    planets: galaxy.children,
  }
}

export function isGalaxyPath(pathname: string): boolean {
  return galaxyForPath(pathname) !== null
}

export function nodeForPath(pathname: string): PlanetNode | undefined {
  return ALL_NODES.find((node) => node.path === pathname)
}

export function parentPath(pathname: string): string | null {
  if (PARENTS.has(pathname)) return PARENTS.get(pathname) ?? null
  if (pathname === '/activity') return '/'
  if (pathname === '/cost') return '/settings#budgets'
  if (pathname === '/agent-profile') return '/agents'
  const task = pathname.match(/^\/tasks\/([^/]+)$/)
  if (task) return '/tasks'
  const episode = pathname.match(/^\/episodes\/([^/]+)$/)
  if (episode) return '/episodes'
  const compare = pathname.match(/^\/episodes\/([^/]+)\/compare$/)
  if (compare) return `/episodes/${compare[1]}`
  const agent = pathname.match(/^\/agents\/([^/]+)$/)
  if (agent && agent[1] !== 'local') return '/agents'
  return '/'
}

export function trailForPath(pathname: string): Array<{ path: string; titleKey: string }> {
  const trail: Array<{ path: string; titleKey: string }> = []
  let current: string | null = pathname
  const guard = new Set<string>()
  while (current && !guard.has(current)) {
    guard.add(current)
    trail.push({ path: current, titleKey: titleKeyForPath(current) })
    current = parentPath(current)
  }
  return trail.reverse()
}

export function titleKeyForPath(pathname: string): string {
  if (pathname === '/') return 'nav.home'
  if (pathname === '/settings') return 'nav.settings'
  if (pathname === '/sessions') return 'nav.sessions'
  const node = nodeForPath(pathname)
  if (node) return node.titleKey
  if (pathname.startsWith('/tasks/')) return 'nav.tasks'
  if (pathname.includes('/compare')) return 'nav.episodes'
  if (pathname.startsWith('/episodes/')) return 'nav.episodes'
  if (pathname.startsWith('/agents/')) return 'nav.agents'
  if (pathname === '/providers') return 'nav.providers'
  if (pathname === '/cost') return 'nav.cost'
  if (pathname === '/activity') return 'nav.activity'
  if (pathname === '/agent-profile') return 'nav.agents'
  return 'nav.home'
}

export function isComposingPath(pathname: string): boolean {
  return pathname === '/tasks/new'
}

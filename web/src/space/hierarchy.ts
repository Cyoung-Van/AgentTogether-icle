import { azure, cyan, ice, lilac, steel } from './palettes'
import type { GalaxyLayer, PlanetNode, SpaceField } from './types'

const today: PlanetNode = {
  id: 'today',
  path: '/today',
  titleKey: 'home.title',
  blurbKey: 'space.blurb.today',
  icon: 'today',
  palette: ice,
  desktop: { x: 22, y: 30, size: 96, depth: 0.18 },
  mobile: { x: 28, y: 20, size: 84, depth: 0.12 },
}

const taskList: PlanetNode = {
  id: 'task-list',
  path: '/tasks',
  titleKey: 'home.allTasks',
  blurbKey: 'space.blurb.taskList',
  icon: 'tasks',
  palette: cyan,
  desktop: { x: 30, y: 36, size: 104, depth: 0.06 },
  mobile: { x: 30, y: 22, size: 90, depth: 0.04 },
}

const newTask: PlanetNode = {
  id: 'new-task',
  path: '/tasks/new',
  titleKey: 'nav.newTask',
  blurbKey: 'space.blurb.newTask',
  icon: 'newTask',
  palette: { c0: '#b4d8cc', c1: '#3d7f6c', c2: '#142820', core: '#e4f2ec' },
  desktop: { x: 62, y: 28, size: 88, depth: 0.22 },
  mobile: { x: 72, y: 28, size: 80, depth: 0.16 },
}

const episodes: PlanetNode = {
  id: 'episodes',
  path: '/episodes',
  titleKey: 'nav.episodes',
  blurbKey: 'space.blurb.episodes',
  icon: 'episodes',
  palette: { c0: '#b0cddd', c1: '#447088', c2: '#142430', core: '#e2eef4' },
  desktop: { x: 40, y: 68, size: 84, depth: 0.34 },
  mobile: { x: 32, y: 58, size: 78, depth: 0.28 },
}

const collabs: PlanetNode = {
  id: 'collabs',
  path: '/collabs',
  titleKey: 'nav.collabs',
  blurbKey: 'space.blurb.collabs',
  icon: 'collabs',
  palette: { c0: '#a8c8d4', c1: '#3a6c80', c2: '#142428', core: '#deecee' },
  desktop: { x: 72, y: 64, size: 78, depth: 0.4 },
  mobile: { x: 70, y: 64, size: 74, depth: 0.32 },
}

const agentDir: PlanetNode = {
  id: 'agent-dir',
  path: '/agents',
  titleKey: 'nav.agents',
  blurbKey: 'space.blurb.agentDir',
  icon: 'agents',
  palette: azure,
  desktop: { x: 30, y: 38, size: 104, depth: 0.08 },
  mobile: { x: 30, y: 26, size: 90, depth: 0.06 },
}

const localAgents: PlanetNode = {
  id: 'local-agents',
  path: '/agents/local',
  titleKey: 'nav.localAgents',
  blurbKey: 'space.blurb.localAgents',
  icon: 'local',
  palette: { c0: '#b0c4dc', c1: '#446088', c2: '#162030', core: '#e4ecf4' },
  desktop: { x: 66, y: 32, size: 90, depth: 0.24 },
  mobile: { x: 72, y: 30, size: 80, depth: 0.18 },
}

const recommend: PlanetNode = {
  id: 'recommend',
  path: '/recommend',
  titleKey: 'nav.recommend',
  blurbKey: 'space.blurb.recommend',
  icon: 'recommend',
  palette: { c0: '#b8c8dc', c1: '#4a6488', c2: '#182436', core: '#e6eef4' },
  desktop: { x: 50, y: 68, size: 82, depth: 0.36 },
  mobile: { x: 50, y: 64, size: 76, depth: 0.3 },
}

const taskStudio: PlanetNode = {
  id: 'task-studio',
  path: '/space/tasks',
  titleKey: 'nav.tasks',
  blurbKey: 'space.blurb.tasks',
  icon: 'tasks',
  palette: cyan,
  desktop: { x: 48, y: 40, size: 120, depth: 0 },
  mobile: { x: 70, y: 28, size: 100, depth: 0 },
  children: [taskList, newTask, episodes, collabs],
}

const agents: PlanetNode = {
  id: 'agents',
  path: '/space/agents',
  titleKey: 'nav.agents',
  blurbKey: 'space.blurb.agents',
  icon: 'agents',
  palette: azure,
  desktop: { x: 74, y: 26, size: 94, depth: 0.22 },
  mobile: { x: 32, y: 50, size: 86, depth: 0.2 },
  children: [agentDir, localAgents, recommend],
}

const sessions: PlanetNode = {
  id: 'sessions',
  path: '/sessions',
  titleKey: 'nav.sessions',
  blurbKey: 'space.blurb.sessions',
  icon: 'sessions',
  palette: lilac,
  desktop: { x: 36, y: 70, size: 84, depth: 0.38 },
  mobile: { x: 72, y: 56, size: 78, depth: 0.32 },
}

const settings: PlanetNode = {
  id: 'settings',
  path: '/settings',
  titleKey: 'nav.settings',
  blurbKey: 'space.blurb.settings',
  icon: 'settings',
  palette: steel,
  desktop: { x: 70, y: 72, size: 76, depth: 0.46 },
  mobile: { x: 50, y: 80, size: 74, depth: 0.4 },
}

export const HOME_CENTER: PlanetNode = {
  id: 'home',
  path: '/',
  titleKey: 'nav.home',
  blurbKey: 'space.blurb.home',
  icon: 'today',
  palette: ice,
  desktop: { x: 50, y: 50, size: 128, depth: 0 },
  mobile: { x: 50, y: 50, size: 110, depth: 0 },
}

export const HOME_FIELD: SpaceField = {
  id: 'home',
  path: '/',
  titleKey: 'nav.home',
  planets: [today, taskStudio, agents, sessions, settings],
}

export const HOME_GALAXY: GalaxyLayer = {
  id: 'home',
  path: '/',
  titleKey: 'nav.home',
  center: HOME_CENTER,
  parent: null,
  children: HOME_FIELD.planets,
}

export const TASK_GALAXY: GalaxyLayer = {
  id: 'tasks',
  path: '/space/tasks',
  titleKey: 'nav.tasks',
  center: taskStudio,
  parent: HOME_CENTER,
  children: taskStudio.children ?? [],
}

export const AGENT_GALAXY: GalaxyLayer = {
  id: 'agents',
  path: '/space/agents',
  titleKey: 'nav.agents',
  center: agents,
  parent: HOME_CENTER,
  children: agents.children ?? [],
}

const GALAXIES = [HOME_GALAXY, TASK_GALAXY, AGENT_GALAXY]

export const CLUSTER_FIELDS: SpaceField[] = [
  {
    id: 'tasks',
    path: '/space/tasks',
    titleKey: 'nav.tasks',
    planets: taskStudio.children ?? [],
  },
  {
    id: 'agents',
    path: '/space/agents',
    titleKey: 'nav.agents',
    planets: agents.children ?? [],
  },
]

const ALL_NODES = [
  HOME_CENTER,
  today,
  taskStudio,
  agents,
  sessions,
  settings,
  ...CLUSTER_FIELDS.flatMap((field) => field.planets),
]

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
  if (pathname === '/') return null
  if (pathname.startsWith('/space/')) return '/'
  if (
    pathname === '/today' ||
    pathname === '/sessions' ||
    pathname === '/settings' ||
    pathname === '/activity'
  ) {
    return '/'
  }
  if (
    pathname === '/tasks' ||
    pathname === '/tasks/new' ||
    pathname === '/episodes' ||
    pathname === '/collabs'
  ) {
    return '/space/tasks'
  }
  if (pathname === '/agents' || pathname === '/agents/local' || pathname === '/recommend') {
    return '/space/agents'
  }
  if (pathname === '/providers' || pathname === '/cost') return '/settings'
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
  if (pathname === '/today') return 'home.title'
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

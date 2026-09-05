export type IconName =
  | 'today'
  | 'tasks'
  | 'newTask'
  | 'episodes'
  | 'collabs'
  | 'agents'
  | 'local'
  | 'recommend'
  | 'sessions'
  | 'activity'
  | 'settings'
  | 'providers'
  | 'cost'

export type PlanetPalette = {
  c0: string
  c1: string
  c2: string
  core: string
}

export type PlanetLayout = {
  x: number
  y: number
  size: number
  depth: number
}

export type PlanetNode = {
  id: string
  path: string
  titleKey: string
  blurbKey: string
  icon: IconName
  palette: PlanetPalette
  desktop: PlanetLayout
  mobile: PlanetLayout
  children?: PlanetNode[]
}

export type SpaceField = {
  id: string
  path: string
  titleKey: string
  planets: PlanetNode[]
}

export type GalaxyLayer = {
  id: string
  path: string
  titleKey: string
  center: PlanetNode
  parent: PlanetNode | null
  children: PlanetNode[]
}

export type PlanetOrigin = {
  x: number
  y: number
  size: number
  palette: PlanetPalette
  nodeId: string
  icon?: IconName
}

export type SpaceTransition = {
  kind: 'enter' | 'exit'
  origin: PlanetOrigin
  fromPath: string
  toPath: string
}

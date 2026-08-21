import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useLang } from '../i18n'

interface ProjectPickerProps {
  projectId: string
  projectPath: string
  onChange: (projectId: string, projectPath: string) => void
  optional?: boolean
}

const CUSTOM_PROJECT = '__new_project__'

/** Select an existing project; free-form fields only appear for an explicit new project. */
export function ProjectPicker({ projectId, projectPath, onChange, optional = false }: ProjectPickerProps) {
  const { t } = useLang()
  const tasks = useQuery({ queryKey: ['tasks', 'projectOptions'], queryFn: api.tasks })
  const episodes = useQuery({ queryKey: ['episodes', 'projectOptions'], queryFn: () => api.episodes() })

  const projects = new Map<string, string>()
  projects.set('default', '')
  for (const task of tasks.data?.tasks ?? []) {
    if (task.project_id) projects.set(task.project_id, task.project_path ?? projects.get(task.project_id) ?? '')
  }
  for (const episode of episodes.data?.episodes ?? []) {
    if (episode.project_id && !projects.has(episode.project_id)) projects.set(episode.project_id, '')
  }

  const isExisting = projectId !== '' && projects.has(projectId)
  const selected = projectId === '' && optional ? '' : isExisting ? projectId : CUSTOM_PROJECT

  function selectProject(value: string) {
    if (!value && optional) {
      onChange('', '')
      return
    }
    if (value === CUSTOM_PROJECT) {
      onChange('', '')
      return
    }
    onChange(value, projects.get(value) ?? '')
  }

  return (
    <div className="project-picker">
      <label className="field">
        {t('project.label')}
        <select value={selected} onChange={(event) => selectProject(event.target.value)}>
          {optional && <option value="">{t('project.any')}</option>}
          {[...projects.entries()].map(([id, path]) => (
            <option key={id} value={id}>{id}{path ? ` · ${path}` : ''}</option>
          ))}
          <option value={CUSTOM_PROJECT}>{t('project.new')}</option>
        </select>
      </label>
      {selected === CUSTOM_PROJECT && (
        <div className="form-grid project-custom-fields">
          <label className="field">
            {t('project.id')}
            <input required value={projectId} onChange={(event) => onChange(event.target.value, projectPath)} />
          </label>
          <label className="field">
            {t('project.path')}
            <input value={projectPath} onChange={(event) => onChange(projectId, event.target.value)} />
          </label>
        </div>
      )}
    </div>
  )
}

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { useLang } from '../i18n'
import { ProjectPicker } from '../components/ProjectPicker'

export default function NewTask() {
  const { t } = useLang()
  const navigate = useNavigate()
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [projectId, setProjectId] = useState('default')
  const [projectPath, setProjectPath] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  async function create() {
    if (!title.trim()) {
      setError(t('newtask.needTitle'))
      return
    }
    if (!projectId.trim()) {
      setError(t('project.needId'))
      return
    }
    setSaving(true)
    setError('')
    try {
      const task = await api.createTask({
        title: title.trim(),
        description: description.trim(),
        project_id: projectId.trim() || 'default',
        project_path: projectPath.trim(),
      })
      navigate(`/tasks/${task.task_id}`)
    } catch (exc) {
      setError(String(exc))
      setSaving(false)
    }
  }

  return (
    <div className="compose">
      <header className="page-hero">
        <h1>{t('newtask.title')}</h1>
        <p className="page-guide">{t('newtask.footer')}</p>
      </header>
      <ol className="how-path compact">
        <li>
          <span className="how-n" aria-hidden="true">1</span>
          <strong>{t('how.step1')}</strong>
          <span>{t('newtask.after1')}</span>
        </li>
        <li>
          <span className="how-n" aria-hidden="true">2</span>
          <strong>{t('how.step2')}</strong>
          <span>{t('newtask.after2')}</span>
        </li>
        <li>
          <span className="how-n" aria-hidden="true">3</span>
          <strong>{t('how.step3')}</strong>
          <span>{t('newtask.after3')}</span>
        </li>
        <li>
          <span className="how-n" aria-hidden="true">4</span>
          <strong>{t('how.step4')}</strong>
          <span>{t('newtask.after4')}</span>
        </li>
      </ol>
      <input
        className="compose-title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder={t('newtask.titlePh')}
        autoFocus
      />
      <textarea
        className="compose-body"
        rows={8}
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder={t('newtask.descPh')}
      />
      <ProjectPicker
        projectId={projectId}
        projectPath={projectPath}
        onChange={(nextProjectId, nextProjectPath) => {
          setProjectId(nextProjectId)
          setProjectPath(nextProjectPath)
        }}
      />
      {error && <div className="error">{error}</div>}
      <div className="actions">
        <button onClick={create} disabled={saving}>
          {saving ? t('common.loading') : t('newtask.create')}
        </button>
      </div>
    </div>
  )
}

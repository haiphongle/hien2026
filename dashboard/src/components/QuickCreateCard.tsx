import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { fetchAPI } from '../api/client'
import { useTranslation } from '../i18n/useTranslation'
import type { Orientation, Project } from '../types'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from './ui/card'
import { Button } from './ui/button'
import FlowModelSummary from './FlowModelSummary'

interface Material { id: string; name: string }
interface QuickCreateResult { project_id: string; video_id: string; scene_count: number; request_count: number }

const controlStyle = {
  background: 'var(--surface)',
  color: 'var(--text)',
  border: '1px solid var(--border)',
}

export default function QuickCreateCard({ projects }: { projects: Project[] }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [materials, setMaterials] = useState<Material[]>([])
  const [projectId, setProjectId] = useState('')
  const [idea, setIdea] = useState('')
  const [orientation, setOrientation] = useState<Orientation>('VERTICAL')
  const [material, setMaterial] = useState('realistic')
  const [sceneCount, setSceneCount] = useState(1)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const projectMaterial = projects.find(project => project.id === projectId)?.material

  useEffect(() => {
    fetchAPI<Material[]>('/api/materials').then(setMaterials).catch(e => setError(String(e)))
  }, [])

  useEffect(() => {
    const requested = searchParams.get('project_id')
    setProjectId(previous => {
      if (previous && projects.some(p => p.id === previous)) return previous
      return projects.find(p => p.id === requested)?.id ?? projects[0]?.id ?? ''
    })
  }, [projects, searchParams])

  useEffect(() => {
    if (projectMaterial) setMaterial(projectMaterial)
  }, [projectId, projectMaterial])

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!projectId || idea.trim().length < 3) return
    setSubmitting(true)
    setError(null)
    try {
      const result = await fetchAPI<QuickCreateResult>('/api/videos/quick-create', {
        method: 'POST',
        body: JSON.stringify({ project_id: projectId, idea: idea.trim(), orientation, material, scene_count: sceneCount }),
      })
      navigate(`/projects/${result.project_id}?tab=pipeline&video=${result.video_id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Card className="py-5 gap-4" id="create-video">
      <CardHeader>
        <CardTitle className="text-base">{t('quickCreate.title')}</CardTitle>
        <CardDescription className="text-xs">{t('quickCreate.description')}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="mb-4">
          <FlowModelSummary />
        </div>
        <div className="mb-4">
          <Button type="button" variant="outline" disabled={!projectId} onClick={() => navigate(`/projects/${projectId}?tab=studio`)}>{t('studio.open')}</Button>
        </div>
        <form onSubmit={submit} className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <label htmlFor="quick-idea" className="text-xs">{t('quickCreate.idea')}</label>
            <textarea
              id="quick-idea"
              value={idea}
              onChange={e => setIdea(e.target.value)}
              placeholder={t('quickCreate.ideaPlaceholder')}
              minLength={3}
              maxLength={2000}
              required
              rows={3}
              className="w-full rounded-md px-3 py-2 text-sm outline-none focus:ring-1"
              style={controlStyle}
            />
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="flex flex-col gap-1.5">
              <label htmlFor="quick-project" className="text-xs">{t('quickCreate.project')}</label>
              <select id="quick-project" value={projectId} onChange={e => setProjectId(e.target.value)} className="rounded-md px-2.5 py-2 text-xs" style={controlStyle} required>
                {projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}
              </select>
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="quick-orientation" className="text-xs">{t('quickCreate.orientation')}</label>
              <select id="quick-orientation" value={orientation} onChange={e => setOrientation(e.target.value as Orientation)} className="rounded-md px-2.5 py-2 text-xs" style={controlStyle}>
                <option value="VERTICAL">{t('quickCreate.vertical')}</option>
                <option value="HORIZONTAL">{t('quickCreate.horizontal')}</option>
              </select>
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="quick-material" className="text-xs">{t('quickCreate.material')}</label>
              <select id="quick-material" value={material} onChange={e => setMaterial(e.target.value)} className="rounded-md px-2.5 py-2 text-xs" style={controlStyle} required>
                {materials.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="quick-count" className="text-xs">{t('quickCreate.sceneCount')}</label>
              <select id="quick-count" value={sceneCount} onChange={e => setSceneCount(Number(e.target.value))} className="rounded-md px-2.5 py-2 text-xs" style={controlStyle}>
                {[1, 2, 3, 4, 5, 6].map(n => <option key={n} value={n}>{n}</option>)}
              </select>
            </div>
          </div>

          {projects.length === 0 && <p className="text-xs" style={{ color: 'var(--yellow)' }}>{t('quickCreate.noProject')}</p>}
          {error && <p role="alert" className="text-xs break-words" style={{ color: 'var(--red)' }}>{error}</p>}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="text-[11px]" style={{ color: 'var(--muted)' }}>{t('quickCreate.creditNote')}</span>
            <Button type="submit" disabled={submitting || !projectId || materials.length === 0 || idea.trim().length < 3}>
              {submitting ? t('quickCreate.submitting') : t('quickCreate.submit')}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

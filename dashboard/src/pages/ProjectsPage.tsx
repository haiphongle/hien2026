import { useState, useEffect, type FormEvent } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { fetchAPI } from '../api/client'
import type { Project } from '../types'
import ProjectDetailPage from './ProjectDetailPage'
import { useTranslation } from '../i18n/useTranslation'
import type { TranslationKey } from '../i18n/translations'
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardAction, CardFooter } from '../components/ui/card'
import { Badge } from '../components/ui/badge'
import { Tabs, TabsList, TabsTrigger } from '../components/ui/tabs'
import { Button } from '../components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '../components/ui/dialog'

type FilterTab = 'ACTIVE' | 'ARCHIVED' | 'ALL'
interface Material { id: string; name: string }

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const controlStyle = { background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }

function flowProjectId(value: string): string | null {
  const input = value.trim()
  if (UUID.test(input)) return input.toLowerCase()
  try {
    const url = new URL(input)
    if (url.protocol !== 'https:' || url.hostname !== 'flow.google.com') return null
    const parts = url.pathname.split('/').filter(Boolean)
    const index = parts.indexOf('project')
    if (index < 0 || (index !== 0 && (index !== 2 || parts[0] !== 'u' || !/^\d+$/.test(parts[1])))) return null
    const id = parts[index + 1]
    return id && UUID.test(id) ? id.toLowerCase() : null
  } catch {
    return null
  }
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString()
}

function TierBadge({ tier, t }: { tier: string | null; t: (key: TranslationKey) => string }) {
  if (!tier) return null
  const isTwo = tier.includes('TWO')
  return <Badge variant={isTwo ? 'default' : 'secondary'}>{isTwo ? t('projects.tier2') : t('projects.tier1')}</Badge>
}

function ProjectCard({ project, onClick, t }: { project: Project; onClick: () => void; t: (key: TranslationKey, params?: Record<string, string | number>) => string }) {
  return (
    <Card className="py-4 gap-3 h-full cursor-pointer transition-opacity hover:opacity-90" onClick={onClick}>
      <CardHeader>
        <CardTitle className="text-sm">{project.name}</CardTitle>
        {project.description && (
          <CardDescription className="text-[11px] leading-relaxed line-clamp-2">{project.description}</CardDescription>
        )}
        <CardAction>
          <TierBadge tier={project.user_paygate_tier} t={t} />
        </CardAction>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap gap-1.5">
          {project.material && <Badge variant="outline">{project.material}</Badge>}
          <Badge variant="outline">{project.status}</Badge>
        </div>
      </CardContent>
      <CardFooter>
        <span className="text-[10px] tracking-wide" style={{ color: 'var(--muted)' }}>{t('projects.footer', { date: formatDate(project.created_at), id: project.id.slice(0, 8) })}</span>
      </CardFooter>
    </Card>
  )
}

export default function ProjectsPage() {
  const { t } = useTranslation()
  const { id } = useParams<{ id?: string }>()
  const navigate = useNavigate()
  const [tab, setTab] = useState<FilterTab>('ACTIVE')
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [addOpen, setAddOpen] = useState(false)
  const [materials, setMaterials] = useState<Material[]>([])
  const [name, setName] = useState('')
  const [flowUrl, setFlowUrl] = useState('')
  const [material, setMaterial] = useState('realistic')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchAPI<Project[]>('/api/projects')
      .then(setProjects)
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!addOpen) return
    fetchAPI<Material[]>('/api/materials').then(setMaterials).catch(e => setError(String(e)))
  }, [addOpen])

  async function addProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const projectId = flowProjectId(flowUrl)
    if (!projectId) {
      setError(t('projects.add.invalidUrl'))
      return
    }
    if (projects.some(project => project.id.toLowerCase() === projectId)) {
      setError(t('projects.add.duplicate'))
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const created = await fetchAPI<Project>('/api/projects', {
        method: 'POST',
        body: JSON.stringify({ name: name.trim(), flow_project_id: projectId, material }),
      })
      setProjects(previous => [created, ...previous])
      setAddOpen(false)
      setName('')
      setFlowUrl('')
      navigate(`/projects/${created.id}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  // If there's an :id param, show detail page
  if (id) {
    return <ProjectDetailPage projectId={id} onBack={() => navigate('/projects')} />
  }

  const filtered = projects.filter(p => {
    if (tab === 'ALL') return p.status !== 'DELETED'
    return p.status === tab
  })

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <Tabs value={tab} onValueChange={v => setTab(v as FilterTab)}>
          <TabsList>
            <TabsTrigger value="ACTIVE">{t('projects.tab.active')}</TabsTrigger>
            <TabsTrigger value="ARCHIVED">{t('projects.tab.archived')}</TabsTrigger>
            <TabsTrigger value="ALL">{t('projects.tab.all')}</TabsTrigger>
          </TabsList>
        </Tabs>
        <span className="ml-auto text-[11px]" style={{ color: 'var(--muted)' }}>{t('projects.count', { n: filtered.length })}</span>
        <Button size="sm" variant="outline" onClick={() => { setError(null); setAddOpen(true) }}>{t('projects.add.button')}</Button>
        <Button size="sm" onClick={() => navigate('/#create-video')}>{t('quickCreate.title')}</Button>
      </div>

      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t('projects.add.title')}</DialogTitle>
            <DialogDescription>
              {t('projects.add.instructions')}{' '}
              <a href="https://flow.google.com/" target="_blank" rel="noopener noreferrer" className="underline">Google Flow ↗</a>
            </DialogDescription>
          </DialogHeader>
          <form onSubmit={addProject} className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <label htmlFor="add-project-name" className="text-xs">{t('projects.add.name')}</label>
              <input id="add-project-name" value={name} onChange={e => setName(e.target.value)} required maxLength={200} placeholder={t('projects.add.namePlaceholder')} className="rounded-md px-3 py-2 text-sm" style={controlStyle} />
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="add-project-url" className="text-xs">{t('projects.add.url')}</label>
              <input id="add-project-url" type="text" value={flowUrl} onChange={e => setFlowUrl(e.target.value)} required placeholder="https://flow.google.com/u/5/project/…" className="rounded-md px-3 py-2 text-sm" style={controlStyle} />
              <p className="text-[11px] text-muted-foreground">{t('projects.add.urlHint')}</p>
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="add-project-material" className="text-xs">{t('quickCreate.material')}</label>
              <select id="add-project-material" value={material} onChange={e => setMaterial(e.target.value)} required className="rounded-md px-3 py-2 text-sm" style={controlStyle}>
                {materials.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
              </select>
            </div>
            {error && <p role="alert" className="text-xs break-words" style={{ color: 'var(--red)' }}>{error}</p>}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setAddOpen(false)}>{t('common.close')}</Button>
              <Button type="submit" disabled={submitting || materials.length === 0 || !name.trim() || !flowUrl.trim()}>{submitting ? t('projects.add.submitting') : t('projects.add.submit')}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {loading ? (
        <div className="text-xs" style={{ color: 'var(--muted)' }}>{t('projects.loading')}</div>
      ) : filtered.length === 0 ? (
        <div className="text-xs" style={{ color: 'var(--muted)' }}>{t(`projects.empty.${tab}` as TranslationKey)}</div>
      ) : (
        <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))' }}>
          {filtered.map(p => (
            <ProjectCard key={p.id} project={p} onClick={() => navigate(`/projects/${p.id}`)} t={t} />
          ))}
        </div>
      )}
    </div>
  )
}

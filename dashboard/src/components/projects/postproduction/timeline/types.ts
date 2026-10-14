import type { CaptionStyle, ColorSettings, EditRegion, EditingResult, FrameSettings } from '../editingTypes'
import type { RestorationResult } from '../propainter'
export interface Track { purpose?: 'standard' | 'overlay'; id: string; name: string; kind: 'video' | 'overlay' | 'audio' | 'caption' | 'text' | 'effect'; locked: boolean; muted: boolean; solo?: boolean; role?: 'original' | 'music' | 'voice' | 'effects' }
export type Caption = CaptionStyle & { x: number; y: number }
export interface Transition { kind: 'dissolve' | 'blur' | 'black' | 'slide' | 'light'; duration: number }
export interface VideoOverlay { x: number; y: number; width: number; height: number; fit: 'contain' | 'cover'; flip: boolean; hidden: boolean; layout: 'free' | 'split'; border: number; border_color: string; shape?: 'rectangle' | 'rounded' | 'circle'; radius?: number; entrance?: number; entrance_offset?: number }
export interface Clip { transition?: Transition | null; muted?: boolean; overlay?: VideoOverlay | null; id: string; asset_id: string | null; track_id: string; start: number; source_in: number; duration: number; gain: number; group: string | null; anchor?: string | null; speed?: number; fade_in?: number; fade_out?: number; envelope?: { time: number; value: number }[]; text?: string; caption?: Caption; effect?: EditRegion | null; colors?: ColorSettings | null; origin?: string }
export interface Ducking { enabled: boolean; amount: number; attack: number; release: number; original_amount?: number; detect_pauses?: boolean }
export interface Document { version: 1; fps: number; width: number; height: number; tracks: Track[]; clips: Clip[]; ducking?: Ducking; frame?: FrameSettings | null; legacy_imported?: boolean }
export interface Asset { management_id?: string | null; id: string; owner: string; ref: string; name: string; kind: 'video' | 'audio'; category: 'source' | 'export' | 'sfx' | 'music' | 'voice'; duration: number; width: number; height: number; fps: number; has_audio: boolean; url: string; proxy_url?: string | null; missing?: boolean; visuals_ready: boolean }
export interface Version extends EditingResult { export_id: string; name: string }
export interface TimelineState { revision: number; document: Document; assets: Asset[]; warnings: string[]; source_available: boolean; legacy_available?: boolean; fonts?: { id: string; name: string }[]; exports?: Version[] }
export interface Visuals { peaks: number[]; interval: number; duration: number; thumbnail_count: number; thumbnail_interval?: number; thumbnail_width?: number; thumbnail_height?: number; thumbnail_url?: string; peak_db?: number; silences?: { start: number; end: number }[] }
export interface Job { id: string; kind: string; status: string; progress: number; stage: string; error?: string; result?: { url?: string; duration?: number; asset_ids?: string[]; export_id?: string; range_start?: number; range_end?: number | null } }
export type AITool = 'speech' | 'narration' | 'music' | 'cover' | 'restore'
export interface ResultOptions { captions?: boolean; voices?: boolean; effects?: boolean; legacy?: boolean; source?: 'auto' | 'speech' | 'scenes' }
export interface ImportReport { voices: number; fitted: number; blocked: { scene_id: string; name: string; duration: number; frame: number }[] }
export interface TimelineController {
  save: () => Promise<number>
  refresh: () => Promise<unknown>
  importResults: (options?: ResultOptions) => Promise<ImportReport | undefined>
  addMusic: (filename: string) => Promise<void>
  addRestoration: (results: RestorationResult[], restore?: boolean) => Promise<void>
  getSelectedVideoIds: () => string[]
  getDocument: () => Document | null
  getAssets: () => Asset[]
  selectScene: (sceneId: string) => void
}

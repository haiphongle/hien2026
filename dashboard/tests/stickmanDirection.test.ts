import assert from 'node:assert/strict'
import test from 'node:test'
import { applyShotDirection, cameraDirection, cameraPresets, rememberDirection, normalizeAnalysis, type StickmanScene } from '../src/components/projects/stickmanOptions.ts'

const scene: StickmanScene = { narration: 'Hơn một câu nói, mất một tấm lòng.', action: 'A and B sit across a table.', speaker: 'A', cast: ['A', 'B'], emotion: 'thinking', gesture: 'explain', camera: 'Locked camera.', motion_beats: ['Both put cups down.', 'Both turn away.', 'The cups cool.'] }

test('applying AI direction changes only pictures and movement, preserving spoken words and cast', () => {
  const before = structuredClone(scene)
  const result = applyShotDirection(scene, { action: 'A and B hold hands.', camera: cameraDirection('push'), motion_beats: ['A reaches out.', 'B takes the hand.', 'Both relax.'], narration: 'Changed words', speaker: 'B', cast: [] } as StickmanScene)
  assert.equal(result.narration, before.narration)
  assert.deepEqual(result.cast, before.cast)
  assert.equal(result.speaker, before.speaker)
  assert.equal(result.emotion, before.emotion)
  assert.equal(result.action, 'A and B hold hands.')
  assert.deepEqual(scene, before)
  result.motion_beats![0] = 'Edited later'
  assert.deepEqual(scene, before)
})

test('camera presets fit the existing backend contract and intensity does not accumulate prompts', () => {
  for (const preset of cameraPresets) for (const strength of ['gentle', 'normal', 'strong'] as const) {
    const value = cameraDirection(preset.id, strength)
    assert.ok(value.length <= 180 && value.length >= 3)
    assert.ok(value.startsWith(preset.prompt))
  }
  assert.notEqual(cameraDirection('push', 'gentle'), cameraDirection('push', 'strong'))
  assert.equal(cameraDirection('unknown'), '')
})

test('timestamp prefixes from AI are stripped before applying direction to production', () => {
  const result = applyShotDirection(scene, { ...scene, motion_beats: ['0–3s: Sit down.', '3-6s: Set down a cup.', '6—8s: Rest.'] })
  assert.deepEqual(result.motion_beats, ['Sit down.', 'Set down a cup.', 'Rest.'])
})

test('prompt history is deduplicated, bounded, serializable and can restore previous visual direction', () => {
  let versions: ReturnType<typeof rememberDirection> = []
  for (let i = 0; i < 8; i++) versions = rememberDirection(versions, { ...scene, action: `Action ${i}` })
  assert.equal(versions.length, 5)
  versions = rememberDirection(versions, { ...scene, action: 'Action 7' })
  assert.equal(versions.length, 5)
  const loaded = JSON.parse(JSON.stringify(versions))
  const restored = applyShotDirection(scene, loaded[0])
  assert.equal(restored.action, 'Action 3')
  assert.equal(restored.narration, scene.narration)
  assert.deepEqual(restored.cast, scene.cast)
})

test('scene environment and continuity survive AI application, history and legacy suggestions', () => {
  const original = { ...scene, environment: 'Small home at dusk.', staging: 'A left, B right.', lighting: 'Window light.', continuity: 'Same cups.' }
  const proposed = { ...scene, environment: 'Same home at night.', staging: 'Both sit together.', lighting: 'Warm lamp.', continuity: 'Cups remain in place.' }
  const result = applyShotDirection(original, proposed)
  assert.equal(result.environment, proposed.environment)
  assert.equal(result.narration, original.narration)
  const saved = JSON.parse(JSON.stringify(rememberDirection([], original)))
  assert.deepEqual(applyShotDirection(result, saved[0]), original)
  assert.equal(applyShotDirection(original, scene).environment, original.environment)
})

test('old drafts and malformed analysis normalize without changing accepted story fields', () => {
  assert.deepEqual(normalizeAnalysis(null), { summary: '', environment: '', era: '', continuity: '' })
  assert.deepEqual(normalizeAnalysis({ summary: 'Family story.', era: 'a'.repeat(200), environment: 4 }),
    { summary: 'Family story.', environment: '', era: 'a'.repeat(120), continuity: '' })
})

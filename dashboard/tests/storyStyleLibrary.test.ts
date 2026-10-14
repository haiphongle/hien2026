import assert from 'node:assert/strict'
import test from 'node:test'
import { filterStyles, profileChoice, styleChange, styleKey, styleSettings, type StoryPreset, type StyleProfile } from '../src/components/projects/storyStyleLibrary.ts'

const preset: StoryPreset = { id: 'claymation', name: 'Hoạt hình đất sét', shape: 'illustrated', local_actor: false, category: '3d' }
const own: StyleProfile = { id: 'own-style', name: 'Đất sét ấm áp', image_url: '/image', style_instruction: 'Handcrafted warm clay illustration.', negative_prompt: 'No text.', character_design: 'A round sculpted person.', category: '3d', default_character_id: 'approved-character', status: 'PUBLISHED' }

test('search handles Vietnamese accents and combines category and saved favorites', () => {
  const choices = [{ preset }, { preset, profile: own }]
  const favorites = JSON.parse(JSON.stringify([styleKey(choices[1])]))
  assert.equal(filterStyles(choices, 'dat set', '3d', favorites, true).length, 1)
  assert.equal(filterStyles(choices, 'dat', '2d', favorites, false).length, 0)
  assert.equal(filterStyles(choices, 'khong co', '', [], false).length, 0)
})

test('style change explicitly keeps both character references or uses the approved default', () => {
  const current = { character: 'existing-A', secondCharacter: 'existing-B', designs: [{ role: 'A' as const, name: 'A', description: 'Appearance A' }], motion: 'talk' as const }
  const before = structuredClone(current)
  const kept = styleChange({ preset, profile: own }, 'keep', current)
  assert.equal(kept.character, 'existing-A'); assert.equal(kept.secondCharacter, 'existing-B')
  assert.deepEqual(kept.designs, current.designs); assert.equal(kept.motion, 'ai')
  const fresh = styleChange({ preset, profile: own }, 'new', current)
  assert.equal(fresh.character, 'approved-character'); assert.equal(fresh.secondCharacter, '')
  assert.deepEqual(fresh.designs, []); assert.deepEqual(current, before)
})

test('own styles resolve to a compatible production base and settings exclude server metadata', () => {
  assert.equal(profileChoice(own, [preset])?.preset.id, 'claymation')
  assert.equal(profileChoice(own, []), undefined)
  const settings = styleSettings(own)
  assert.ok(!('id' in settings) && !('image_url' in settings) && !('status' in settings))
  assert.equal(settings.default_character_id, 'approved-character')
  assert.equal(settings.orientation, 'VERTICAL')
})

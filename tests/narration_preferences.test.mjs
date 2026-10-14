import assert from 'node:assert/strict'
import { test } from 'node:test'
import { parseNarrationPreferences, narrationDefaults } from '../dashboard/src/lib/narrationPreferences.ts'

test('a chosen sample survives reload, excludes saved template and keeps subtitle preference', () => {
  const settings = parseNarrationPreferences(JSON.stringify({
    ...narrationDefaults, voiceSampleId: 'vieneu_binh_north_male', template: 'old',
    burnSubtitles: false, speed: 1.2, invalidatedSceneIds: ['scene1'],
  }))
  assert.equal(settings.voiceSampleId, 'vieneu_binh_north_male')
  assert.equal(settings.template, '')
  assert.equal(settings.speed, 1.2)
  assert.equal(settings.burnSubtitles, false)
  assert.deepEqual(settings.invalidatedSceneIds, ['scene1'])
})

test('malformed storage cannot break the narration form', () => {
  for (const raw of [null, 'bad json', '[]', 'null', 'false']) assert.equal(parseNarrationPreferences(raw), null)
  const settings = parseNarrationPreferences(JSON.stringify({speed: 999, sourceVolume: 'loud', voiceSampleId: '../path', jobId: 'bad'}))
  assert.equal(settings.speed, 1)
  assert.equal(settings.sourceVolume, 0.35)
  assert.equal(settings.voiceSampleId, '')
  assert.equal(settings.jobId, null)
})

test('native engine and preset survive reload while older preferences keep OmniVoice', () => {
  const saved = parseNarrationPreferences(JSON.stringify({ engine: 'vieneu', nativePresetId: 'v3_0123456789abcdef' }))
  assert.equal(saved.engine, 'vieneu')
  assert.equal(saved.nativePresetId, 'v3_0123456789abcdef')
  const old = parseNarrationPreferences(JSON.stringify({ template: 'my_voice' }))
  assert.equal(old.engine, 'omnivoice')
  const invalid = parseNarrationPreferences(JSON.stringify({ engine: 'unknown', nativePresetId: '../../path' }))
  assert.equal(invalid.engine, 'omnivoice')
  assert.equal(invalid.nativePresetId, '')
})

test('English Kokoro selection survives reload and rejects malformed preset paths', () => {
  const saved = parseNarrationPreferences(JSON.stringify({ engine: 'kokoro', nativePresetId: 'kokoro_af_heart', speed: 1.2 }))
  assert.equal(saved.engine, 'kokoro')
  assert.equal(saved.nativePresetId, 'kokoro_af_heart')
  assert.equal(saved.speed, 1.2)
  assert.equal(parseNarrationPreferences(JSON.stringify({ engine: 'kokoro', nativePresetId: 'kokoro_../../secret' })).nativePresetId, '')
})

test('Qwen3 language and preset survive reload without accepting unsupported voices', () => {
  const saved = parseNarrationPreferences(JSON.stringify({ engine: 'qwen3', qwenLanguage: 'ja', nativePresetId: 'qwen3_ja_ono_anna' }))
  assert.equal(saved.engine, 'qwen3')
  assert.equal(saved.qwenLanguage, 'ja')
  assert.equal(saved.nativePresetId, 'qwen3_ja_ono_anna')
  assert.equal(parseNarrationPreferences(JSON.stringify({ engine: 'qwen3', qwenLanguage: 'vi', nativePresetId: 'qwen3_vi_ryan' })).nativePresetId, '')
})

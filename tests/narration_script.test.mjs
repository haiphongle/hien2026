import assert from 'node:assert/strict'
import { test } from 'node:test'
import { assignNarrationBlocks, splitNarrationScript } from '../dashboard/src/lib/narrationScript.ts'

test('scene headings skip the title and preserve multi-line narration', () => {
  assert.deepEqual(splitNarrationScript('# Mùa phượng\n\n**Cảnh 1:**\nNgày xưa.\nSân trường.\n\n## Scene 2\nChiều nay.'), [
    'Ngày xưa. Sân trường.', 'Chiều nay.',
  ])
})

test('extra paragraphs are kept in the final scene', () => {
  assert.deepEqual(assignNarrationBlocks(['one', 'two'], ['A', 'B', 'C', 'D']), {
    one: 'A', two: 'B\n\nC\n\nD',
  })
  assert.deepEqual(assignNarrationBlocks(['one', 'two'], ['A']), { one: 'A' })
  assert.deepEqual(assignNarrationBlocks([], ['A']), {})
})

test('scripts without scene headings split by paragraph or sentence', () => {
  assert.deepEqual(splitNarrationScript('Một. Hai!'), ['Một.', 'Hai!'])
  assert.deepEqual(splitNarrationScript('Một\ndòng\n\nHai'), ['Một dòng', 'Hai'])
  assert.deepEqual(splitNarrationScript('   '), [])
})

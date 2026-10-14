/** Parse plain text or Markdown scene headings from a pasted narration script. */
export function splitNarrationScript(text: string): string[] {
  const lines = text.replace(/\r/g, '').split('\n')
  const cleanLine = (line: string) => line.trim().replace(/\*+/g, '').replace(/^#+\s*/, '')
  const heading = /^\s*(?:cảnh|phân\s*cảnh|phân\s*đoạn|scene)\s*(?:số\s*)?#?\d*\s*(?:[:.)\-–—]\s*|\s*$)/i
  const blocks: string[] = []

  if (lines.some(line => heading.test(cleanLine(line)))) {
    let current: string | null = null
    for (const original of lines) {
      const line = cleanLine(original)
      if (heading.test(line)) {
        if (current?.trim()) blocks.push(current.trim())
        current = line.replace(heading, '').trim()
      } else if (line && current !== null) {
        current = current ? `${current} ${line}` : line
      }
    }
    if (current?.trim()) blocks.push(current.trim())
  } else {
    blocks.push(...text.split(/\n\s*\n+/).map(block => block.replace(/\s*\n\s*/g, ' ').trim()).filter(Boolean))
    if (blocks.length === 1) {
      const sentences = blocks[0].match(/[^.!?]+[.!?]+(?:[”’"']+)?|[^.!?]+$/g)?.map(item => item.trim()).filter(Boolean) ?? []
      if (sentences.length > 1) return sentences
    }
  }
  return blocks
}

/** Keep surplus paragraphs in the final scene instead of silently dropping them. */
export function assignNarrationBlocks(sceneIds: string[], blocks: string[]): Record<string, string> {
  return Object.fromEntries(sceneIds.flatMap((id, index) => {
    if (index >= blocks.length) return []
    const text = index === sceneIds.length - 1 ? blocks.slice(index).join('\n\n') : blocks[index]
    return [[id, text]]
  }))
}

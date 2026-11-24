/** "just now", "5 min ago", "3 h ago", "2 days ago", then a date. */
export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return ''
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`
  if (seconds < 7 * 86400) return `${Math.floor(seconds / 86400)} days ago`
  return new Date(iso).toLocaleDateString()
}

export const PLATFORM_LABELS: Record<string, string> = { instagram: 'Instagram', linkedin: 'LinkedIn' }

/** Split text into plain and highlighted parts, for every place a quote appears (case-insensitive). */
export function splitByQuotes(text: string, quotes: string[]): { text: string; marked: boolean }[] {
  const lower = text.toLowerCase()
  const ranges: [number, number][] = []
  for (const q of quotes) {
    const needle = q.trim().toLowerCase()
    if (!needle) continue
    let at = lower.indexOf(needle)
    while (at !== -1) {
      ranges.push([at, at + needle.length])
      at = lower.indexOf(needle, at + needle.length)
    }
  }
  // Sort and merge overlapping ranges, then cut the text into pieces.
  ranges.sort((a, b) => a[0] - b[0])
  const merged: [number, number][] = []
  for (const r of ranges) {
    const last = merged[merged.length - 1]
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1])
    else merged.push([...r])
  }
  const parts: { text: string; marked: boolean }[] = []
  let pos = 0
  for (const [start, end] of merged) {
    if (start > pos) parts.push({ text: text.slice(pos, start), marked: false })
    parts.push({ text: text.slice(start, end), marked: true })
    pos = end
  }
  if (pos < text.length) parts.push({ text: text.slice(pos), marked: false })
  return parts
}

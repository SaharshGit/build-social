import { splitByQuotes } from '../lib/format'

/** The post text with the reviewer's flagged phrases marked. Line breaks are kept. */
export default function Highlighted({ text, quotes }: { text: string; quotes: string[] }) {
  return (
    <div className="post-text">
      {splitByQuotes(text, quotes).map((part, i) =>
        part.marked ? (
          <mark key={i} title="Flagged by the reviewer">{part.text}</mark>
        ) : (
          <span key={i}>{part.text}</span>
        ),
      )}
    </div>
  )
}

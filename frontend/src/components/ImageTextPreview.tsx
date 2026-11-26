// The words printed on a single-image post. A placeholder until the designed image is rendered (V1c):
// it shows what will be on the image, with review issues highlighted like the caption.
import type { Post } from '../api/client'
import Highlighted from './Highlighted'

export default function ImageTextPreview({ post, quotes }: { post: Post; quotes: string[] }) {
  const it = post.draft.image_text
  if (!it) return null
  return (
    <div className="image-text">
      <span className="image-text-label">On the image</span>
      <div className="image-text-headline"><Highlighted text={it.headline} quotes={quotes} /></div>
      {it.subline && <div className="image-text-subline"><Highlighted text={it.subline} quotes={quotes} /></div>}
    </div>
  )
}

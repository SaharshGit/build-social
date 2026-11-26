// Brand → Look & feel: the brand kit the design step uses for every image (the writer never sees it).
// Colours with roles (checked for readability as you type), Google Fonts, logo, art direction, dos/don'ts.
import { useEffect, useState } from 'react'
import { assetUrl, type Brand, type BrandColor, type BrandDesign } from '../../api/client'
import { useSaveBrand, useUploadAsset } from '../../api/hooks'
import { contrastRatio, isHex, MIN_TEXT_CONTRAST } from '../../lib/color'
import { ErrorBox } from '../common'

const ROLES: BrandColor['role'][] = ['background', 'text', 'accent', 'secondary', 'highlight']

// A neutral starting kit for brands that don't have one yet.
const STARTER: BrandDesign = {
  colors: [
    { name: 'Background', hex: '#FFFFFF', role: 'background' },
    { name: 'Text', hex: '#222222', role: 'text' },
  ],
  fonts: { heading: { family: '', weights: [600, 800] }, body: { family: '', weights: [400, 600] } },
  art_direction: '', visual_dos: [], visual_donts: [],
}

const lines = (text: string) => text.split('\n').map((x) => x.trim()).filter(Boolean)

/** Load a Google Font so its name can be previewed in itself. */
function useGoogleFont(family: string) {
  useEffect(() => {
    const name = family.trim()
    if (!name) return
    const id = `gf-${name.replace(/\s+/g, '-')}`
    if (document.getElementById(id)) return
    const link = document.createElement('link')
    link.id = id
    link.rel = 'stylesheet'
    link.href = `https://fonts.googleapis.com/css2?family=${encodeURIComponent(name)}:wght@400;700&display=swap`
    document.head.appendChild(link)
  }, [family])
}

export default function LookAndFeel({ brand }: { brand: Brand }) {
  const saved = brand.profile.design ?? null
  const [design, setDesign] = useState<BrandDesign>(saved ?? STARTER)
  const [dos, setDos] = useState((saved?.visual_dos ?? []).join('\n'))
  const [donts, setDonts] = useState((saved?.visual_donts ?? []).join('\n'))
  const save = useSaveBrand()
  const uploadLogo = useUploadAsset(brand.id)
  useGoogleFont(design.fonts.heading.family)
  useGoogleFont(design.fonts.body.family)

  const bg = design.colors.find((c) => c.role === 'background')
  const text = design.colors.find((c) => c.role === 'text')
  const readable = bg && text && isHex(bg.hex) && isHex(text.hex) ? contrastRatio(text.hex, bg.hex) : null

  const setColor = (i: number, change: Partial<BrandColor>) =>
    setDesign({ ...design, colors: design.colors.map((c, j) => (j === i ? { ...c, ...change } : c)) })
  const setFont = (which: 'heading' | 'body', family: string) =>
    setDesign({ ...design, fonts: { ...design.fonts, [which]: { ...design.fonts[which], family } } })

  const onSave = () => {
    const next: BrandDesign = { ...design, visual_dos: lines(dos), visual_donts: lines(donts) }
    save.mutate({ brandId: brand.id, profile: { ...brand.profile, design: next } })
  }

  const onLogo = (file: File | undefined) => {
    if (!file) return
    uploadLogo.mutate({ file, kind: 'logo' }, {
      onSuccess: (asset) => {
        const next = { ...design, logo_asset_id: asset.id }
        setDesign(next)
        save.mutate({ brandId: brand.id, profile: { ...brand.profile, design: { ...next, visual_dos: lines(dos), visual_donts: lines(donts) } } })
      },
    })
  }

  return (
    <div className="look">
      {!saved && <p className="muted small">No look & feel yet. Fill this in so images match the brand; nothing is saved until you press Save.</p>}
      <div className="look-grid">
        <div className="card look-card">
          <div className="look-head">
            <h2>Colours</h2>
            <button type="button" className="link-btn"
              onClick={() => setDesign({ ...design, colors: [...design.colors, { name: '', hex: '#888888', role: 'accent' }] })}>
              + Add colour
            </button>
          </div>
          {design.colors.map((c, i) => {
            const vsBg = bg && c.role !== 'background' && isHex(c.hex) && isHex(bg.hex) ? contrastRatio(c.hex, bg.hex) : null
            return (
              <div key={i} className="swatch-row">
                <input type="color" className="swatch" value={isHex(c.hex) ? c.hex : '#000000'}
                  onChange={(e) => setColor(i, { hex: e.target.value.toUpperCase() })} aria-label="Pick colour" />
                <div className="swatch-fields">
                  <input value={c.name} placeholder="Name, e.g. Espresso" onChange={(e) => setColor(i, { name: e.target.value })} />
                  <div className="swatch-sub">
                    <input className={`hex ${isHex(c.hex) ? '' : 'invalid'}`} value={c.hex}
                      onChange={(e) => setColor(i, { hex: e.target.value })} />
                    <select value={c.role} onChange={(e) => setColor(i, { role: e.target.value as BrandColor['role'] })}>
                      {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
                    </select>
                    {vsBg !== null && c.role !== 'text' && vsBg < MIN_TEXT_CONTRAST && (
                      <span className="warn small">{vsBg.toFixed(1)} : 1 on background, never for text</span>
                    )}
                  </div>
                </div>
                <button type="button" className="icon-btn" title="Remove colour"
                  onClick={() => setDesign({ ...design, colors: design.colors.filter((_, j) => j !== i) })}>✕</button>
              </div>
            )
          })}
          <div className={`readable ${readable !== null && readable < MIN_TEXT_CONTRAST ? 'bad' : ''}`}>
            {readable === null
              ? 'Needs one background and one text colour.'
              : readable >= MIN_TEXT_CONTRAST
                ? `✓ Readable: ${text!.name || 'text'} on ${bg!.name || 'background'} is ${readable.toFixed(1)} : 1 (needs at least ${MIN_TEXT_CONTRAST} : 1)`
                : `✗ Hard to read: ${readable.toFixed(1)} : 1. Text on the background needs at least ${MIN_TEXT_CONTRAST} : 1.`}
          </div>

          <h2>Fonts</h2>
          <label className="font-row">
            <span>Headlines</span>
            <input value={design.fonts.heading.family} placeholder="Google Fonts name, e.g. Fraunces"
              onChange={(e) => setFont('heading', e.target.value)} />
          </label>
          {design.fonts.heading.family && (
            <p className="font-sample" style={{ fontFamily: `'${design.fonts.heading.family}', serif`, fontWeight: 700 }}>
              Cold chai weather, anyone?
            </p>
          )}
          <label className="font-row">
            <span>Everything else</span>
            <input value={design.fonts.body.family} placeholder="e.g. DM Sans"
              onChange={(e) => setFont('body', e.target.value)} />
          </label>
          {design.fonts.body.family && (
            <p className="font-sample small" style={{ fontFamily: `'${design.fonts.body.family}', sans-serif` }}>
              New on the menu at our Indiranagar cafe.
            </p>
          )}
          <p className="muted small">From Google Fonts: free for commercial use.</p>
        </div>

        <div className="card look-card">
          <h2>Logo</h2>
          <div className="logo-row">
            <div className="logo-box" style={{ background: bg && isHex(bg.hex) ? bg.hex : undefined }}>
              {design.logo_asset_id
                ? <img src={assetUrl(design.logo_asset_id)} alt={`${brand.profile.name} logo`} />
                : <span className="muted small">No logo</span>}
            </div>
            <div>
              <label className="btn file-btn">
                {uploadLogo.isPending ? 'Uploading…' : design.logo_asset_id ? 'Replace' : 'Upload logo'}
                <input type="file" accept="image/png,image/jpeg,image/webp" hidden
                  onChange={(e) => onLogo(e.target.files?.[0])} disabled={uploadLogo.isPending} />
              </label>
              <p className="muted small">PNG, JPG or WebP. Placed small, in one corner of each image.</p>
            </div>
          </div>

          <h2>Art direction</h2>
          <textarea rows={4} value={design.art_direction}
            placeholder="How photos should look: light, textures, props, mood. E.g. natural warm daylight, wooden tables, no stock models."
            onChange={(e) => setDesign({ ...design, art_direction: e.target.value })} />

          <div className="form-row">
            <label>Do (one per line)
              <textarea rows={3} value={dos} onChange={(e) => setDos(e.target.value)} placeholder="Plenty of empty space" />
            </label>
            <label>Don't (one per line)
              <textarea rows={3} value={donts} onChange={(e) => setDonts(e.target.value)} placeholder="No ALL CAPS" />
            </label>
          </div>
          <p className="muted small"><em>The style board (Photos tab) adds what the brand's own pictures look like.</em></p>
        </div>
      </div>

      <div className="look-actions">
        <button className="btn btn-primary" onClick={onSave} disabled={save.isPending}>
          {save.isPending ? 'Saving…' : 'Save look & feel'}
        </button>
        {save.isSuccess && !save.isPending && <span className="muted small">Saved.</span>}
      </div>
      {save.error && <ErrorBox error={save.error} />}
      {uploadLogo.error && <ErrorBox error={uploadLogo.error} />}
    </div>
  )
}

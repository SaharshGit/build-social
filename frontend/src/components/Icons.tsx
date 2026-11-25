// Line icons matching the Canva design (thin strokes in the text colour). Plain SVG, no icon library.
type P = { size?: number }
const base = (size: number) => ({
  width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
  strokeWidth: 1.6, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
})

export const PlusIcon = ({ size = 22 }: P) => <svg {...base(size)}><path d="M12 4v16M4 12h16" /></svg>
export const SlidersIcon = ({ size = 22 }: P) => (
  <svg {...base(size)}><path d="M3 7h11M18 7h3M3 17h4M11 17h10" /><circle cx="16" cy="7" r="2" /><circle cx="9" cy="17" r="2" /></svg>
)
export const ClockIcon = ({ size = 22 }: P) => <svg {...base(size)}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>
export const InboxIcon = ({ size = 22 }: P) => (
  <svg {...base(size)}><path d="M3 13l2.5-7h13L21 13v6H3z" /><path d="M3 13h5l1.5 2.5h5L16 13h5" /></svg>
)
export const TagIcon = ({ size = 22 }: P) => (
  <svg {...base(size)}><path d="M3 4h8l10 10-7 7L4 11z" /><circle cx="8" cy="8" r="1.2" /></svg>
)
export const CollapseIcon = ({ size = 20 }: P) => (
  <svg {...base(size)}><path d="M15 6l-6 6 6 6" /><path d="M20 4v16" /></svg>
)
export const SendIcon = ({ size = 18 }: P) => <svg {...base(size)} strokeWidth={2}><path d="M12 19V5M5 12l7-7 7 7" /></svg>
export const PencilIcon = ({ size = 18 }: P) => <svg {...base(size)}><path d="M4 20h4L19 9l-4-4L4 16z" /></svg>
export const TrashIcon = ({ size = 18 }: P) => (
  <svg {...base(size)}><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" /></svg>
)

/** The split-circle mark from the design: black half on the left, grey half on the right. */
export function LogoMark({ size = 26 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 30 30" aria-hidden="true">
      <path d="M0 0 A15 15 0 0 1 0 30 Z" fill="#000" />
      <path d="M30 0 A15 15 0 0 0 30 30 Z" fill="#8e99a2" />
    </svg>
  )
}

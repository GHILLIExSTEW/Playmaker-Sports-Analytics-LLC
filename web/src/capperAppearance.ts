export const sectionLabels = { stats: 'Performance stats', picks: 'Current picks', charts: 'Analytics charts', results: 'Settled results' }
export type CapperSection = keyof typeof sectionLabels
export const headingFonts = { barlow: "'Barlow Condensed', sans-serif", ibm: "'IBM Plex Sans', sans-serif", georgia: 'Georgia, serif' }
export type CapperAppearance = {
  name_color: string | null
  link_color: string | null
  text_color: string | null
  heading_font: keyof typeof headingFonts
  banner_url: string | null
  section_order: CapperSection[]
}
export type CapperTheme = { background_color: string; accent_color: string; appearance: CapperAppearance }

export function parseAppearance(value: unknown): CapperAppearance {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Unexpected page appearance.')
  const color = (key: string) => {
    const entry: unknown = Reflect.get(value, key)
    if (entry !== null && (typeof entry !== 'string' || !/^#[0-9a-f]{6}$/i.test(entry))) throw new Error('Unexpected appearance color.')
    return entry
  }
  const font: unknown = Reflect.get(value, 'heading_font')
  if (font !== 'barlow' && font !== 'ibm' && font !== 'georgia') throw new Error('Unexpected heading font.')
  const banner: unknown = Reflect.get(value, 'banner_url')
  if (banner !== null && (typeof banner !== 'string' || !safeImageUrl(banner))) throw new Error('Unexpected banner URL.')
  const order: unknown = Reflect.get(value, 'section_order')
  if (!Array.isArray(order) || order.length !== 4 || new Set(order).size !== 4) throw new Error('Unexpected section order.')
  const sections = order.map((key: unknown): CapperSection => {
    if (key !== 'stats' && key !== 'picks' && key !== 'charts' && key !== 'results') throw new Error('Unexpected capper section.')
    return key
  })
  return { name_color: color('name_color'), link_color: color('link_color'), text_color: color('text_color'),
    heading_font: font, banner_url: banner, section_order: sections }
}

export function safeImageUrl(value: string) {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && !url.username && !url.password && !/\s/.test(value)
  } catch { return false }
}

import { TenantTheme } from './types'

// Injects CSS custom properties onto the document root based on tenant theme
export function injectTenantCSSVariables(theme: TenantTheme): void {
  const root = document.documentElement
  const { colors, fonts, borderRadius, darkMode } = theme

  // Primary colors
  Object.entries(colors.primary).forEach(([key, value]) => {
    const suffix = key === 'DEFAULT' ? '' : `-${key}`
    root.style.setProperty(`--color-primary${suffix}`, value)
  })

  // Secondary colors
  Object.entries(colors.secondary).forEach(([key, value]) => {
    const suffix = key === 'DEFAULT' ? '' : `-${key}`
    root.style.setProperty(`--color-secondary${suffix}`, value)
  })

  // Accent colors (optional)
  if (colors.accent) {
    Object.entries(colors.accent).forEach(([key, value]) => {
      if (value) {
        const suffix = key === 'DEFAULT' ? '' : `-${key}`
        root.style.setProperty(`--color-accent${suffix}`, value)
      }
    })
  }

  // Surface, background
  root.style.setProperty('--color-surface', colors.surface.DEFAULT)
  root.style.setProperty('--color-surface-variant', colors.surface.variant)
  root.style.setProperty('--color-background', colors.background.DEFAULT)
  root.style.setProperty('--color-background-dark', colors.background.dark)

  // Gradient
  root.style.setProperty('--color-gradient-from', colors.gradient.from)
  root.style.setProperty('--color-gradient-to', colors.gradient.to)

  // Fonts
  root.style.setProperty('--font-sans', fonts.sans.join(', '))
  if (fonts.display) root.style.setProperty('--font-display', fonts.display.join(', '))
  if (fonts.mono) root.style.setProperty('--font-mono', fonts.mono.join(', '))

  // Border radius
  root.style.setProperty('--radius-base', borderRadius.base)
  root.style.setProperty('--radius-button', borderRadius.button)
  root.style.setProperty('--radius-input', borderRadius.input)

  // Dark mode
  root.style.setProperty('--color-dark-background', darkMode.background)
  root.style.setProperty('--color-dark-surface', darkMode.surface)
  root.style.setProperty('--color-dark-text', darkMode.text)
}

// Infer MIME type from favicon URL extension
function getFaviconMimeType(url: string): string {
  const ext = url.split('.').pop()?.toLowerCase()
  switch (ext) {
    case 'svg': return 'image/svg+xml'
    case 'png': return 'image/png'
    case 'ico': return 'image/x-icon'
    case 'jpg':
    case 'jpeg': return 'image/jpeg'
    case 'webp': return 'image/webp'
    default: return 'image/svg+xml'
  }
}

// Update favicon dynamically
export function updateFavicon(faviconUrl: string): void {
  let link = document.querySelector<HTMLLinkElement>("link[rel*='icon']")
  if (!link) {
    link = document.createElement('link')
    link.rel = 'icon'
    document.head.appendChild(link)
  }
  link.type = getFaviconMimeType(faviconUrl)
  link.href = faviconUrl
}

// Update document title
export function updateDocumentTitle(appName: string): void {
  document.title = appName
}

// Load Google Fonts dynamically for a tenant
export function loadTenantFonts(fonts: TenantTheme['fonts']): void {
  const allFonts = [
    ...fonts.sans,
    ...(fonts.display || []),
    ...(fonts.mono || []),
  ].filter(f => !['system-ui', 'sans-serif', 'serif', 'monospace', '-apple-system'].includes(f))

  if (allFonts.length === 0) return

  const families = allFonts.map(f => f.replace(/\s+/g, '+')).join('&family=')
  const weights = '400;500;600;700'
  const url = `https://fonts.googleapis.com/css2?family=${families}:wght@${weights}&display=swap`

  // Check if already loaded
  const existing = document.querySelector(`link[href="${url}"]`)
  if (existing) return

  const link = document.createElement('link')
  link.rel = 'stylesheet'
  link.href = url
  link.onerror = () => {
    console.warn(`[Tenant] Failed to load Google Fonts: ${families.replace(/&family=/g, ', ')}. Falling back to system fonts.`)
    link.remove()
  }
  document.head.appendChild(link)
}

// Apply i18n overrides (to be called with i18next instance)
export function applyI18nOverrides(
  i18n: { addResourceBundle: (lng: string, ns: string, resources: Record<string, string>, deep?: boolean, overwrite?: boolean) => void },
  overrides: Record<string, Record<string, string>>
): void {
  Object.entries(overrides).forEach(([lang, translations]) => {
    // Convert flat dot-notation keys to nested object
    const nested = unflattenObject(translations)
    i18n.addResourceBundle(lang, 'translation', nested as Record<string, string>, true, true)
  })
}

// Convert flat dot-notation keys to nested object
// e.g., { "common.appName": "Quellex" } => { common: { appName: "Quellex" } }
function unflattenObject(obj: Record<string, string>): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(obj)) {
    const parts = key.split('.')
    let current: Record<string, unknown> = result
    for (let i = 0; i < parts.length - 1; i++) {
      if (!(parts[i] in current)) {
        current[parts[i]] = {}
      } else if (typeof current[parts[i]] !== 'object' || current[parts[i]] === null) {
        // Collision: an intermediate key was previously set as a leaf value.
        // Promote it to an object so nested keys can still be added.
        console.warn(`[Tenant i18n] Key collision at "${parts.slice(0, i + 1).join('.')}": overwriting leaf value with namespace.`)
        current[parts[i]] = {}
      }
      current = current[parts[i]] as Record<string, unknown>
    }
    current[parts[parts.length - 1]] = value
  }
  return result
}

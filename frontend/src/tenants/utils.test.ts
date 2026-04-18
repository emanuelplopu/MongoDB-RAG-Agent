import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { quellexConfig } from './configs/quellex'
import {
  applyI18nOverrides,
  injectTenantCSSVariables,
  loadTenantFonts,
  updateDocumentTitle,
  updateFavicon,
} from './utils'

describe('tenant utils', () => {
  beforeEach(() => {
    document.head.innerHTML = ''
    document.title = 'Original Title'
    document.documentElement.removeAttribute('style')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('injects tenant CSS variables for colors, fonts, radius, and dark mode', () => {
    injectTenantCSSVariables(quellexConfig.theme)

    const style = document.documentElement.style
    expect(style.getPropertyValue('--color-primary')).toBe('#C29629')
    expect(style.getPropertyValue('--color-primary-500')).toBe('#C49B1A')
    expect(style.getPropertyValue('--color-secondary-700')).toBe('#155E55')
    expect(style.getPropertyValue('--color-accent')).toBe('#248F8F')
    expect(style.getPropertyValue('--color-surface')).toBe('#F8F6F2')
    expect(style.getPropertyValue('--color-background-dark')).toBe('#0A1629')
    expect(style.getPropertyValue('--font-sans')).toContain('Inter')
    expect(style.getPropertyValue('--font-display')).toContain('Playfair Display')
    expect(style.getPropertyValue('--radius-button')).toBe('12px')
    expect(style.getPropertyValue('--color-dark-text')).toBe('#FFFFFF')
  })

  it('creates and updates favicon links with inferred mime types', () => {
    updateFavicon('/branding/icon.png')

    let favicon = document.querySelector<HTMLLinkElement>("link[rel='icon']")
    expect(favicon).not.toBeNull()
    expect(favicon?.type).toBe('image/png')
    expect(favicon?.href).toContain('/branding/icon.png')

    updateFavicon('/branding/icon.ico')

    favicon = document.querySelector<HTMLLinkElement>("link[rel='icon']")
    expect(document.querySelectorAll("link[rel='icon']")).toHaveLength(1)
    expect(favicon?.type).toBe('image/x-icon')
    expect(favicon?.href).toContain('/branding/icon.ico')
  })

  it('updates the document title directly', () => {
    updateDocumentTitle('Quellex Portal')
    expect(document.title).toBe('Quellex Portal')
  })

  it('loads Google fonts once and skips system fonts', () => {
    loadTenantFonts({
      sans: ['Inter', 'system-ui', 'sans-serif'],
      display: ['Playfair Display'],
      mono: ['JetBrains Mono', 'monospace'],
    })

    const links = document.head.querySelectorAll<HTMLLinkElement>("link[rel='stylesheet']")
    expect(links).toHaveLength(1)
    expect(links[0].href).toContain('family=Inter')
    expect(links[0].href).toContain('family=Playfair+Display')
    expect(links[0].href).toContain('family=JetBrains+Mono')

    loadTenantFonts({
      sans: ['Inter', 'system-ui', 'sans-serif'],
      display: ['Playfair Display'],
      mono: ['JetBrains Mono', 'monospace'],
    })
    expect(document.head.querySelectorAll("link[rel='stylesheet']")).toHaveLength(1)

    loadTenantFonts({
      sans: ['system-ui', '-apple-system', 'sans-serif'],
    })
    expect(document.head.querySelectorAll("link[rel='stylesheet']")).toHaveLength(1)
  })

  it('removes the font link and warns when font loading fails', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})

    loadTenantFonts({
      sans: ['Inter'],
      display: ['Playfair Display'],
    })

    const link = document.head.querySelector<HTMLLinkElement>("link[rel='stylesheet']")
    expect(link).not.toBeNull()

    link?.onerror?.(new Event('error'))

    expect(warn).toHaveBeenCalledWith(
      '[Tenant] Failed to load Google Fonts: Inter, Playfair+Display. Falling back to system fonts.'
    )
    expect(document.head.querySelector("link[rel='stylesheet']")).toBeNull()
  })

  it('applies i18n overrides and warns on nested key collisions', () => {
    const addResourceBundle = vi.fn()
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})

    applyI18nOverrides(
      { addResourceBundle },
      {
        en: {
          common: 'clobbered',
          'common.appName': 'Quellex',
          'landing.cta.title': 'Start now',
        },
        de: {
          'chat.assistant.title': 'Assistent',
        },
      }
    )

    expect(addResourceBundle).toHaveBeenNthCalledWith(
      1,
      'en',
      'translation',
      {
        common: {
          appName: 'Quellex',
        },
        landing: {
          cta: {
            title: 'Start now',
          },
        },
      },
      true,
      true
    )
    expect(addResourceBundle).toHaveBeenNthCalledWith(
      2,
      'de',
      'translation',
      {
        chat: {
          assistant: {
            title: 'Assistent',
          },
        },
      },
      true,
      true
    )
    expect(warn).toHaveBeenCalledWith(
      '[Tenant i18n] Key collision at "common": overwriting leaf value with namespace.'
    )
  })
})

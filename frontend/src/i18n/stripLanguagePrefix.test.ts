import { describe, expect, it } from 'vitest'
import { stripLanguagePrefix } from './index'

describe('stripLanguagePrefix', () => {
  it('removes a supported language segment', () => {
    expect(stripLanguagePrefix('/en/system/status')).toBe('/system/status')
    expect(stripLanguagePrefix('/de/search')).toBe('/search')
    expect(stripLanguagePrefix('/en')).toBe('/')
  })

  it('leaves paths without a language prefix untouched', () => {
    expect(stripLanguagePrefix('/system/status')).toBe('/system/status')
    expect(stripLanguagePrefix('/')).toBe('/')
    expect(stripLanguagePrefix('/english/docs')).toBe('/english/docs')
  })
})

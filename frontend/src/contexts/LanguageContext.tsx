import { createContext, useContext, useCallback } from 'react'
import { supportedLanguages, SupportedLanguage } from '../i18n'

export interface LanguageContextType {
  language: SupportedLanguage
  setLanguage: (lang: SupportedLanguage) => void
  /** Apply language from synced preferences (no DB write back) */
  applyLanguage: (lang: SupportedLanguage) => void
  supportedLanguages: readonly SupportedLanguage[]
}

/** Provided by `LanguageProvider` (see ./LanguageProvider.tsx). */
export const LanguageContext = createContext<LanguageContextType | undefined>(undefined)

export function useLanguage() {
  const context = useContext(LanguageContext)
  if (context === undefined) {
    throw new Error('useLanguage must be used within a LanguageProvider')
  }
  return context
}

// Hook to get localized path
export function useLocalizedPath() {
  const { language } = useLanguage()

  return useCallback((path: string): string => {
    // If path already has language prefix, replace it
    const pathParts = path.split('/')
    if (supportedLanguages.includes(pathParts[1] as SupportedLanguage)) {
      pathParts[1] = language
      return pathParts.join('/')
    }
    // Add language prefix
    return `/${language}${path.startsWith('/') ? path : `/${path}`}`
  }, [language])
}

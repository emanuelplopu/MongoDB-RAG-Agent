import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'

import en from './locales/en.json'
import de from './locales/de.json'

export const supportedLanguages = ['en', 'de'] as const
export type SupportedLanguage = typeof supportedLanguages[number]

export const languageNames: Record<SupportedLanguage, string> = {
  en: 'English',
  de: 'Deutsch',
}

// Get language from URL path
const getLanguageFromPath = (): SupportedLanguage | undefined => {
  if (typeof window === 'undefined') return undefined
  const pathParts = window.location.pathname.split('/')
  const langCode = pathParts[1]
  if (supportedLanguages.includes(langCode as SupportedLanguage)) {
    return langCode as SupportedLanguage
  }
  return undefined
}

// Custom language detector for URL-based language
const pathLanguageDetector = {
  name: 'pathLanguageDetector',
  lookup: (): string | undefined => {
    return getLanguageFromPath()
  },
}

const languageDetector = new LanguageDetector()
languageDetector.addDetector(pathLanguageDetector)

i18n
  .use(languageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      de: { translation: de },
    },
    fallbackLng: 'en',
    supportedLngs: supportedLanguages,
    detection: {
      order: ['pathLanguageDetector', 'localStorage', 'navigator'],
      lookupLocalStorage: 'i18nextLng',
      caches: ['localStorage'],
    },
    interpolation: {
      escapeValue: false,
    },
    react: {
      useSuspense: false,
    },
  })

export default i18n

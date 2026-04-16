import { createContext, useContext, useEffect, ReactNode, useCallback } from 'react'
import { useNavigate, useLocation, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { supportedLanguages, SupportedLanguage } from '../i18n'
import { useOptionalSettingsSync, SETTINGS_SYNCED_EVENT } from './SettingsSyncContext'
import { UserPreferencesSync } from '../api/client'

interface LanguageContextType {
  language: SupportedLanguage
  setLanguage: (lang: SupportedLanguage) => void
  /** Apply language from synced preferences (no DB write back) */
  applyLanguage: (lang: SupportedLanguage) => void
  supportedLanguages: readonly SupportedLanguage[]
}

const LanguageContext = createContext<LanguageContextType | undefined>(undefined)

export function LanguageProvider({ children }: { children: ReactNode }) {
  const { i18n } = useTranslation()
  const navigate = useNavigate()
  const location = useLocation()
  const { lang } = useParams<{ lang?: string }>()
  const settingsSync = useOptionalSettingsSync()

  // Get current language from URL or fallback to i18n language
  const getCurrentLanguage = useCallback((): SupportedLanguage => {
    if (lang && supportedLanguages.includes(lang as SupportedLanguage)) {
      return lang as SupportedLanguage
    }
    // Check path directly for non-parameterized routes
    const pathLang = location.pathname.split('/')[1]
    if (supportedLanguages.includes(pathLang as SupportedLanguage)) {
      return pathLang as SupportedLanguage
    }
    return (i18n.language as SupportedLanguage) || 'en'
  }, [lang, location.pathname, i18n.language])

  const language = getCurrentLanguage()

  // Sync i18n language with URL
  useEffect(() => {
    if (i18n.language !== language) {
      i18n.changeLanguage(language)
    }
  }, [language, i18n])

  // Navigate to path with new language prefix
  const navigateToLang = useCallback((newLang: SupportedLanguage) => {
    const currentPath = location.pathname
    const pathParts = currentPath.split('/')
    
    if (supportedLanguages.includes(pathParts[1] as SupportedLanguage)) {
      pathParts[1] = newLang
    } else {
      pathParts.splice(1, 0, newLang)
    }
    
    const newPath = pathParts.join('/') || `/${newLang}`
    navigate(newPath + location.search + location.hash, { replace: true })
  }, [location, navigate])

  const setLanguage = useCallback((newLang: SupportedLanguage) => {
    if (newLang === language) return

    // Change i18n language
    i18n.changeLanguage(newLang)
    localStorage.setItem('i18nextLng', newLang)

    // Update URL with new language
    navigateToLang(newLang)

    // Sync to DB
    settingsSync?.syncPreference({ language: newLang })
  }, [language, navigateToLang, i18n, settingsSync])

  // Apply language from DB sync (no DB write back to avoid loop)
  const applyLanguage = useCallback((newLang: SupportedLanguage) => {
    if (newLang === language) return
    if (!supportedLanguages.includes(newLang)) return

    i18n.changeLanguage(newLang)
    localStorage.setItem('i18nextLng', newLang)
    navigateToLang(newLang)
  }, [language, navigateToLang, i18n])

  // Listen for settings synced from DB
  useEffect(() => {
    const handler = (e: Event) => {
      const prefs = (e as CustomEvent<UserPreferencesSync>).detail
      if (prefs?.language && supportedLanguages.includes(prefs.language as SupportedLanguage)) {
        applyLanguage(prefs.language as SupportedLanguage)
      }
    }
    window.addEventListener(SETTINGS_SYNCED_EVENT, handler)
    return () => window.removeEventListener(SETTINGS_SYNCED_EVENT, handler)
  }, [applyLanguage])

  return (
    <LanguageContext.Provider value={{ language, setLanguage, applyLanguage, supportedLanguages }}>
      {children}
    </LanguageContext.Provider>
  )
}

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

import { useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useLanguage } from '../contexts/LanguageContext'
import { supportedLanguages, SupportedLanguage } from '../i18n'

// Hook to get the localized path
export function useLocalizedPath() {
  const { language } = useLanguage()

  return useCallback((path: string): string => {
    // Handle empty or just slash
    if (!path || path === '/') {
      return `/${language}`
    }

    // If path already has language prefix, replace it
    const pathParts = path.split('/')
    if (pathParts[1] && supportedLanguages.includes(pathParts[1] as SupportedLanguage)) {
      pathParts[1] = language
      return pathParts.join('/')
    }

    // Add language prefix
    return `/${language}${path.startsWith('/') ? path : `/${path}`}`
  }, [language])
}

// Hook for navigation with language prefix
export function useLocalizedNavigate() {
  const navigate = useNavigate()
  const getLocalizedPath = useLocalizedPath()

  return useCallback((to: string, options?: { replace?: boolean; state?: unknown }) => {
    navigate(getLocalizedPath(to), options)
  }, [navigate, getLocalizedPath])
}

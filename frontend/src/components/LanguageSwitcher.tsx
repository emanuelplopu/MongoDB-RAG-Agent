import { useState, useRef, useEffect } from 'react'
import { GlobeAltIcon } from '@heroicons/react/24/outline'
import { useLanguage } from '../contexts/LanguageContext'
import { languageNames, SupportedLanguage } from '../i18n'

interface LanguageSwitcherProps {
  compact?: boolean
}

export default function LanguageSwitcher({ compact = true }: LanguageSwitcherProps) {
  const { language, setLanguage, supportedLanguages } = useLanguage()
  const [isOpen, setIsOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  // Flag emojis for visual identification
  const languageFlags: Record<SupportedLanguage, string> = {
    en: '🇬🇧',
    de: '🇩🇪',
  }

  return (
    <div className="relative" ref={menuRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className={`flex items-center justify-center rounded-lg transition-all duration-200 ${
          compact
            ? 'p-2 hover:bg-surface-variant dark:hover:bg-gray-700'
            : 'gap-2 px-3 py-2 hover:bg-surface-variant dark:hover:bg-gray-700'
        }`}
        title={languageNames[language]}
        aria-label="Change language"
      >
        <GlobeAltIcon className="h-5 w-5 text-secondary dark:text-gray-400" />
        {!compact && (
          <span className="text-sm text-secondary dark:text-gray-400">
            {language.toUpperCase()}
          </span>
        )}
      </button>

      {isOpen && (
        <div className="absolute right-0 top-full mt-2 z-50 min-w-[160px] bg-white dark:bg-gray-800 rounded-xl shadow-xl border border-surface-variant dark:border-gray-600 py-1 overflow-hidden">
          {supportedLanguages.map((lang) => (
            <button
              key={lang}
              onClick={() => {
                setLanguage(lang)
                setIsOpen(false)
              }}
              className={`w-full flex items-center gap-3 px-4 py-2.5 text-sm transition-colors ${
                language === lang
                  ? 'bg-primary-100 dark:bg-primary-900/50 text-primary-700 dark:text-primary-300'
                  : 'text-primary-900 dark:text-gray-200 hover:bg-surface-variant dark:hover:bg-gray-700'
              }`}
            >
              <span className="text-lg">{languageFlags[lang]}</span>
              <span className="flex-1 text-left">{languageNames[lang]}</span>
              {language === lang && (
                <svg className="h-4 w-4 text-primary" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
                </svg>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

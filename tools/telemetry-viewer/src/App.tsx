import { useState, useRef, useEffect } from 'react'
import { Routes, Route, NavLink } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useDataSource } from './api'
import { useTheme } from './contexts/ThemeContext'
import BrowserPage from './pages/BrowserPage'
import ComparePage from './pages/ComparePage'
import ImportPage from './pages/ImportPage'
import StatsPage from './pages/StatsPage'
import StrategyRunsPage from './pages/StrategyRunsPage'

type Theme = 'light' | 'dark' | 'system'

const languages = [
  { code: 'en', label: '🇬🇧', name: 'EN' },
  { code: 'de', label: '🇩🇪', name: 'DE' },
  { code: 'ro', label: '🇷🇴', name: 'RO' },
]

export default function App() {
  const { state } = useDataSource()
  const { t, i18n } = useTranslation()
  const { theme, setTheme } = useTheme()
  const [langOpen, setLangOpen] = useState(false)
  const langRef = useRef<HTMLDivElement>(null)

  const cycleTheme = () => {
    const order: Theme[] = ['system', 'light', 'dark']
    const current = order.indexOf(theme)
    setTheme(order[(current + 1) % 3])
  }

  const themeIcon = theme === 'system' ? '🖥️' : theme === 'light' ? '☀️' : '🌙'

  const currentLangCode = (i18n.language || 'en').substring(0, 2)
  const currentLang = languages.find(l => l.code === currentLangCode) || languages[0]

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (langRef.current && !langRef.current.contains(e.target as Node)) {
        setLangOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const navItems = [
    { path: '/', labelKey: 'nav.browse', icon: '📋' },
    { path: '/compare', labelKey: 'nav.compare', icon: '⚖️' },
    { path: '/import', labelKey: 'nav.import', icon: '📥' },
    { path: '/stats', labelKey: 'nav.stats', icon: '📊' },
    { path: '/strategy', labelKey: 'nav.strategy', icon: '🧠' },
  ]

  return (
    <div className="flex h-screen bg-surface text-gray-100">
      {/* Sidebar */}
      <nav className="w-16 bg-surface-light flex flex-col items-center py-4 gap-2 border-r border-gray-700/50">
        {navItems.map(item => (
          <NavLink
            key={item.path}
            to={item.path}
            className={({ isActive }) =>
              `relative w-12 h-12 flex flex-col items-center justify-center rounded-xl text-xs transition-all ${
                isActive
                  ? 'bg-accent-blue/20 text-accent-blue'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-surface-lighter'
              }`
            }
          >
            <span className="text-lg">{item.icon}</span>
            <span className="mt-0.5">{t(item.labelKey)}</span>
            {item.path === '/' && state.records.length > 0 && (
              <span className="absolute -top-1 -right-1 min-w-[18px] h-[18px] flex items-center justify-center rounded-full bg-accent-blue text-[10px] font-bold text-white px-1">
                {state.records.length > 999 ? '999+' : state.records.length}
              </span>
            )}
          </NavLink>
        ))}

        {/* Spacer */}
        <div className="flex-1" />

        {/* Theme toggle */}
        <button
          onClick={cycleTheme}
          className="w-10 h-10 flex flex-col items-center justify-center rounded-lg text-xs transition-all text-gray-400 hover:text-gray-200 hover:bg-surface-lighter"
          title={`${t('theme.title')}: ${t(`theme.${theme}`)}`}
        >
          <span className="text-lg">{themeIcon}</span>
          <span className="mt-0.5 text-[9px]">{t(`theme.${theme}`)}</span>
        </button>

        {/* Language selector */}
        <div ref={langRef} className="relative">
          <button
            onClick={() => setLangOpen(!langOpen)}
            className="w-10 h-10 flex flex-col items-center justify-center rounded-lg text-gray-400 hover:text-gray-200 hover:bg-surface-lighter transition-colors"
            title={t('language.title')}
          >
            <span className="text-sm">{currentLang.label}</span>
            <span className="text-[9px] font-medium mt-0.5">{currentLang.name}</span>
          </button>
          {langOpen && (
            <div className="absolute bottom-0 left-14 bg-surface-light border border-gray-700/50 rounded-lg shadow-xl py-1 min-w-[120px] z-50">
              {languages.map(lang => (
                <button
                  key={lang.code}
                  onClick={() => {
                    i18n.changeLanguage(lang.code)
                    setLangOpen(false)
                  }}
                  className={`w-full flex items-center gap-2 px-3 py-1.5 text-xs transition-colors ${
                    lang.code === currentLangCode
                      ? 'text-accent-blue bg-accent-blue/10'
                      : 'text-gray-300 hover:bg-surface-lighter'
                  }`}
                >
                  <span>{lang.label}</span>
                  <span>{t(`language.${lang.code}`)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </nav>

      {/* Main content */}
      <main className="flex-1 overflow-hidden">
        <Routes>
          <Route path="/" element={<BrowserPage />} />
          <Route path="/compare" element={<ComparePage />} />
          <Route path="/import" element={<ImportPage />} />
          <Route path="/stats" element={<StatsPage />} />
          <Route path="/strategy" element={<StrategyRunsPage />} />
        </Routes>
      </main>
    </div>
  )
}

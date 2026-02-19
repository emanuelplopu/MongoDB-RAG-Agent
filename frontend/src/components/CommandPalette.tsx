/**
 * CommandPalette - Quick navigation and command palette (Ctrl+K)
 * Allows quick access to pages, actions, and recent items
 */

import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  MagnifyingGlassIcon,
  ChatBubbleLeftRightIcon,
  DocumentTextIcon,
  Cog6ToothIcon,
  ArrowRightIcon,
  ClockIcon,
  HomeIcon,
  CloudIcon,
  CommandLineIcon,
  UserCircleIcon,
  ChartBarIcon,
} from '@heroicons/react/24/outline'
import { useEscapeKey, useFocusTrap } from '../hooks/useKeyboardShortcuts'
import { useLocalStorage } from '../hooks/useLocalStorage'

interface CommandItem {
  id: string
  label: string
  description?: string
  icon: React.ComponentType<{ className?: string }>
  action: () => void
  category: 'navigation' | 'action' | 'recent'
  keywords?: string[]
}

interface CommandPaletteProps {
  isOpen: boolean
  onClose: () => void
}

// Navigation items
const getNavigationItems = (navigate: (path: string) => void, t: (key: string) => string): CommandItem[] => [
  {
    id: 'nav-home',
    label: t('nav.dashboard'),
    description: 'Go to dashboard',
    icon: HomeIcon,
    action: () => navigate('/dashboard'),
    category: 'navigation',
    keywords: ['home', 'dashboard', 'main'],
  },
  {
    id: 'nav-chat',
    label: t('nav.chat'),
    description: 'Start a new conversation',
    icon: ChatBubbleLeftRightIcon,
    action: () => navigate('/chat'),
    category: 'navigation',
    keywords: ['chat', 'conversation', 'talk', 'ai'],
  },
  {
    id: 'nav-search',
    label: t('nav.search'),
    description: 'Search documents',
    icon: MagnifyingGlassIcon,
    action: () => navigate('/search'),
    category: 'navigation',
    keywords: ['search', 'find', 'query'],
  },
  {
    id: 'nav-documents',
    label: t('nav.documents'),
    description: 'Browse uploaded documents',
    icon: DocumentTextIcon,
    action: () => navigate('/documents'),
    category: 'navigation',
    keywords: ['documents', 'files', 'uploads'],
  },
  {
    id: 'nav-cloud',
    label: t('nav.cloudSources'),
    description: 'Manage cloud integrations',
    icon: CloudIcon,
    action: () => navigate('/cloud-sources'),
    category: 'navigation',
    keywords: ['cloud', 'google drive', 'dropbox', 'integration'],
  },
  {
    id: 'nav-status',
    label: t('nav.status'),
    description: 'System status and health',
    icon: ChartBarIcon,
    action: () => navigate('/system/status'),
    category: 'navigation',
    keywords: ['status', 'health', 'system'],
  },
  {
    id: 'nav-prompts',
    label: t('nav.prompts'),
    description: 'Manage system prompts',
    icon: CommandLineIcon,
    action: () => navigate('/system/prompts'),
    category: 'navigation',
    keywords: ['prompts', 'system prompt', 'configuration'],
  },
  {
    id: 'nav-profiles',
    label: t('nav.profiles'),
    description: 'Manage AI profiles',
    icon: UserCircleIcon,
    action: () => navigate('/profiles'),
    category: 'navigation',
    keywords: ['profiles', 'ai profiles', 'settings'],
  },
  {
    id: 'nav-config',
    label: t('nav.configuration'),
    description: 'System configuration',
    icon: Cog6ToothIcon,
    action: () => navigate('/system/config'),
    category: 'navigation',
    keywords: ['config', 'configuration', 'settings'],
  },
]

export default function CommandPalette({ isOpen, onClose }: CommandPaletteProps) {
  const [query, setQuery] = useState('')
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [recentNavigation] = useLocalStorage<string[]>('recent_navigation', [])
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()
  const { t } = useTranslation()

  // Close on escape
  useEscapeKey(onClose, isOpen)
  useFocusTrap(containerRef, isOpen)

  // Focus input when opened
  useEffect(() => {
    if (isOpen) {
      setQuery('')
      setSelectedIndex(0)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [isOpen])

  // Navigation with tracking
  const handleNavigate = useCallback((path: string) => {
    navigate(path)
    onClose()
  }, [navigate, onClose])

  // All items
  const allItems = useMemo(() => {
    const navItems = getNavigationItems(handleNavigate, t)
    
    // Add recent items
    const recentItems: CommandItem[] = recentNavigation.slice(0, 3).map((path, idx) => ({
      id: `recent-${idx}`,
      label: `Recently visited: ${path}`,
      icon: ClockIcon,
      action: () => handleNavigate(path),
      category: 'recent' as const,
    }))

    return [...recentItems, ...navItems]
  }, [handleNavigate, t, recentNavigation])

  // Filter items based on query
  const filteredItems = useMemo(() => {
    if (!query.trim()) {
      return allItems
    }
    
    const lowerQuery = query.toLowerCase()
    return allItems.filter(item => {
      const matchLabel = item.label.toLowerCase().includes(lowerQuery)
      const matchDesc = item.description?.toLowerCase().includes(lowerQuery)
      const matchKeywords = item.keywords?.some(k => k.toLowerCase().includes(lowerQuery))
      return matchLabel || matchDesc || matchKeywords
    })
  }, [allItems, query])

  // Reset selection when filtered items change
  useEffect(() => {
    setSelectedIndex(0)
  }, [filteredItems.length])

  // Keyboard navigation
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setSelectedIndex(prev => (prev + 1) % filteredItems.length)
        break
      case 'ArrowUp':
        e.preventDefault()
        setSelectedIndex(prev => (prev - 1 + filteredItems.length) % filteredItems.length)
        break
      case 'Enter':
        e.preventDefault()
        if (filteredItems[selectedIndex]) {
          filteredItems[selectedIndex].action()
        }
        break
    }
  }, [filteredItems, selectedIndex])

  // Scroll selected item into view
  useEffect(() => {
    const list = listRef.current
    if (list) {
      const selectedElement = list.children[selectedIndex] as HTMLElement
      if (selectedElement) {
        selectedElement.scrollIntoView({ block: 'nearest' })
      }
    }
  }, [selectedIndex])

  if (!isOpen) return null

  // Group items by category
  const groupedItems = filteredItems.reduce((acc, item) => {
    if (!acc[item.category]) acc[item.category] = []
    acc[item.category].push(item)
    return acc
  }, {} as Record<string, CommandItem[]>)

  const categoryLabels: Record<string, string> = {
    recent: 'Recent',
    navigation: 'Navigation',
    action: 'Actions',
  }

  let globalIndex = -1

  return (
    <div className="fixed inset-0 z-50 overflow-y-auto">
      {/* Backdrop */}
      <div 
        className="fixed inset-0 bg-black/50 backdrop-blur-sm"
        onClick={onClose}
      />
      
      {/* Dialog */}
      <div className="flex min-h-full items-start justify-center p-4 pt-[15vh]">
        <div
          ref={containerRef}
          className="relative w-full max-w-lg bg-white dark:bg-gray-800 rounded-2xl shadow-2xl overflow-hidden animate-scale-in"
        >
          {/* Search input */}
          <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-200 dark:border-gray-700">
            <MagnifyingGlassIcon className="h-5 w-5 text-gray-400" />
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Search pages, actions..."
              className="flex-1 bg-transparent text-primary-900 dark:text-gray-100 placeholder:text-gray-400 focus:outline-none"
            />
            <kbd className="hidden sm:inline-flex items-center gap-1 px-2 py-1 text-xs text-gray-400 bg-gray-100 dark:bg-gray-700 rounded">
              ESC
            </kbd>
          </div>
          
          {/* Results */}
          <div ref={listRef} className="max-h-80 overflow-y-auto p-2">
            {filteredItems.length === 0 ? (
              <div className="px-4 py-8 text-center text-gray-500 dark:text-gray-400">
                No results found
              </div>
            ) : (
              Object.entries(groupedItems).map(([category, items]) => (
                <div key={category} className="mb-2">
                  <div className="px-3 py-1.5 text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                    {categoryLabels[category] || category}
                  </div>
                  {items.map((item) => {
                    globalIndex++
                    const isSelected = globalIndex === selectedIndex
                    const Icon = item.icon
                    
                    return (
                      <button
                        key={item.id}
                        onClick={() => item.action()}
                        className={`
                          w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition-colors
                          ${isSelected 
                            ? 'bg-primary-50 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300' 
                            : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'
                          }
                        `}
                      >
                        <Icon className="h-5 w-5 flex-shrink-0" />
                        <div className="flex-1 min-w-0">
                          <div className="font-medium truncate">{item.label}</div>
                          {item.description && (
                            <div className="text-xs text-gray-500 dark:text-gray-400 truncate">
                              {item.description}
                            </div>
                          )}
                        </div>
                        {isSelected && (
                          <ArrowRightIcon className="h-4 w-4 flex-shrink-0" />
                        )}
                      </button>
                    )
                  })}
                </div>
              ))
            )}
          </div>
          
          {/* Footer */}
          <div className="px-4 py-2 border-t border-gray-200 dark:border-gray-700 flex items-center gap-4 text-xs text-gray-500 dark:text-gray-400">
            <span className="flex items-center gap-1">
              <kbd className="px-1.5 py-0.5 bg-gray-100 dark:bg-gray-700 rounded">↑↓</kbd>
              Navigate
            </span>
            <span className="flex items-center gap-1">
              <kbd className="px-1.5 py-0.5 bg-gray-100 dark:bg-gray-700 rounded">↵</kbd>
              Select
            </span>
            <span className="flex items-center gap-1">
              <kbd className="px-1.5 py-0.5 bg-gray-100 dark:bg-gray-700 rounded">Esc</kbd>
              Close
            </span>
          </div>
        </div>
      </div>
    </div>
  )
}

/**
 * Hook to manage command palette state with Ctrl+K shortcut
 */
export function useCommandPalette() {
  const [isOpen, setIsOpen] = useState(false)

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault()
        setIsOpen(prev => !prev)
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [])

  return {
    isOpen,
    open: () => setIsOpen(true),
    close: () => setIsOpen(false),
    toggle: () => setIsOpen(prev => !prev),
  }
}

/**
 * SearchSuggestions - Autocomplete suggestions for search inputs
 * Shows recent searches, popular queries, and AI-powered suggestions
 */

import { useState, useEffect, useRef, useCallback } from 'react'
import {
  ClockIcon,
  MagnifyingGlassIcon,
  XMarkIcon,
  ArrowTrendingUpIcon,
  LightBulbIcon,
} from '@heroicons/react/24/outline'
import { useLocalStorage, STORAGE_KEYS } from '../hooks/useLocalStorage'

interface SearchSuggestion {
  id: string
  text: string
  type: 'recent' | 'popular' | 'suggestion'
  icon?: React.ComponentType<{ className?: string }>
}

interface SearchSuggestionsProps {
  /** Current query text */
  query: string
  /** Called when a suggestion is selected */
  onSelect: (text: string) => void
  /** Called when query changes */
  onQueryChange: (text: string) => void
  /** Whether the dropdown is visible */
  isOpen: boolean
  /** Called to close the dropdown */
  onClose: () => void
  /** Called to open the dropdown */
  onOpen: () => void
  /** Placeholder text */
  placeholder?: string
  /** Maximum recent searches to show */
  maxRecent?: number
  /** Show trending/popular suggestions */
  showTrending?: boolean
  /** Custom popular suggestions */
  popularSuggestions?: string[]
  /** Additional class name */
  className?: string
}

export default function SearchSuggestions({
  query,
  onSelect,
  onQueryChange,
  isOpen,
  onClose,
  onOpen,
  placeholder = 'Search...',
  maxRecent = 5,
  showTrending = true,
  popularSuggestions = [],
  className = '',
}: SearchSuggestionsProps) {
  const [recentSearches, setRecentSearches] = useLocalStorage<string[]>(
    STORAGE_KEYS.RECENT_SEARCHES,
    []
  )
  const [highlightedIndex, setHighlightedIndex] = useState(-1)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // Build suggestions list
  const suggestions: SearchSuggestion[] = []

  // Add recent searches (filtered by query)
  const filteredRecent = recentSearches
    .filter(s => !query || s.toLowerCase().includes(query.toLowerCase()))
    .slice(0, maxRecent)
  
  filteredRecent.forEach((text, i) => {
    suggestions.push({
      id: `recent-${i}`,
      text,
      type: 'recent',
      icon: ClockIcon,
    })
  })

  // Add popular suggestions (filtered by query)
  if (showTrending && popularSuggestions.length > 0) {
    const filteredPopular = popularSuggestions
      .filter(s => !query || s.toLowerCase().includes(query.toLowerCase()))
      .filter(s => !filteredRecent.includes(s))
      .slice(0, 3)
    
    filteredPopular.forEach((text, i) => {
      suggestions.push({
        id: `popular-${i}`,
        text,
        type: 'popular',
        icon: ArrowTrendingUpIcon,
      })
    })
  }

  // Add AI suggestions based on query (simple autocomplete)
  if (query.length >= 2) {
    const aiSuggestions = generateSuggestions(query)
      .filter(s => !suggestions.some(existing => existing.text === s))
      .slice(0, 3)
    
    aiSuggestions.forEach((text, i) => {
      suggestions.push({
        id: `suggestion-${i}`,
        text,
        type: 'suggestion',
        icon: LightBulbIcon,
      })
    })
  }

  // Reset highlight when suggestions change
  useEffect(() => {
    setHighlightedIndex(-1)
  }, [suggestions.length])

  // Handle keyboard navigation
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (!isOpen && e.key === 'ArrowDown') {
      onOpen()
      return
    }

    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setHighlightedIndex(prev => 
          prev < suggestions.length - 1 ? prev + 1 : 0
        )
        break
      case 'ArrowUp':
        e.preventDefault()
        setHighlightedIndex(prev => 
          prev > 0 ? prev - 1 : suggestions.length - 1
        )
        break
      case 'Enter':
        e.preventDefault()
        if (highlightedIndex >= 0 && suggestions[highlightedIndex]) {
          handleSelect(suggestions[highlightedIndex].text)
        } else if (query.trim()) {
          handleSelect(query)
        }
        break
      case 'Escape':
        e.preventDefault()
        onClose()
        break
    }
  }, [isOpen, highlightedIndex, suggestions, query, onOpen, onClose])

  // Handle selection
  const handleSelect = (text: string) => {
    // Add to recent searches
    const newRecent = [text, ...recentSearches.filter(s => s !== text)].slice(0, 10)
    setRecentSearches(newRecent)
    
    onSelect(text)
    onClose()
  }

  // Handle removing a recent search
  const handleRemoveRecent = (text: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setRecentSearches(recentSearches.filter(s => s !== text))
  }

  // Scroll highlighted item into view
  useEffect(() => {
    if (highlightedIndex >= 0 && listRef.current) {
      const item = listRef.current.children[highlightedIndex] as HTMLElement
      item?.scrollIntoView({ block: 'nearest' })
    }
  }, [highlightedIndex])

  return (
    <div className={`relative ${className}`}>
      {/* Search input */}
      <div className="relative">
        <MagnifyingGlassIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-5 w-5 text-gray-400" />
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={(e) => {
            onQueryChange(e.target.value)
            if (!isOpen) onOpen()
          }}
          onFocus={onOpen}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          className="w-full pl-10 pr-10 py-2.5 bg-surface-variant dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded-xl text-primary-900 dark:text-gray-100 placeholder:text-gray-400 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
        />
        {query && (
          <button
            onClick={() => {
              onQueryChange('')
              inputRef.current?.focus()
            }}
            className="absolute right-3 top-1/2 -translate-y-1/2 p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
          >
            <XMarkIcon className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Suggestions dropdown */}
      {isOpen && suggestions.length > 0 && (
        <div 
          ref={listRef}
          className="absolute top-full left-0 right-0 mt-1 bg-white dark:bg-gray-800 rounded-xl shadow-lg border border-gray-200 dark:border-gray-700 overflow-hidden z-50 max-h-80 overflow-y-auto"
        >
          {/* Group by type */}
          {['recent', 'popular', 'suggestion'].map(type => {
            const items = suggestions.filter(s => s.type === type)
            if (items.length === 0) return null
            
            const labels = {
              recent: 'Recent',
              popular: 'Trending',
              suggestion: 'Suggestions',
            }

            return (
              <div key={type}>
                <div className="px-3 py-1.5 text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider bg-gray-50 dark:bg-gray-700/50">
                  {labels[type as keyof typeof labels]}
                </div>
                {items.map((suggestion) => {
                  const globalIndex = suggestions.indexOf(suggestion)
                  const isHighlighted = globalIndex === highlightedIndex
                  const Icon = suggestion.icon
                  
                  return (
                    <button
                      key={suggestion.id}
                      onClick={() => handleSelect(suggestion.text)}
                      className={`
                        w-full flex items-center gap-3 px-3 py-2 text-left transition-colors
                        ${isHighlighted 
                          ? 'bg-primary-50 dark:bg-primary-900/30 text-primary-700 dark:text-primary-300' 
                          : 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700'
                        }
                      `}
                    >
                      {Icon && <Icon className="h-4 w-4 flex-shrink-0 text-gray-400" />}
                      <span className="flex-1 truncate">
                        {highlightQuery(suggestion.text, query)}
                      </span>
                      {suggestion.type === 'recent' && (
                        <button
                          onClick={(e) => handleRemoveRecent(suggestion.text, e)}
                          className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 opacity-0 group-hover:opacity-100"
                        >
                          <XMarkIcon className="h-3.5 w-3.5" />
                        </button>
                      )}
                    </button>
                  )
                })}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

/**
 * Generate simple autocomplete suggestions based on query
 */
function generateSuggestions(query: string): string[] {
  // Common search patterns to suggest
  const patterns = [
    `${query} documents`,
    `${query} in files`,
    `related to ${query}`,
    `how to ${query}`,
    `${query} examples`,
  ]
  
  return patterns
}

/**
 * Highlight matching parts of text
 */
function highlightQuery(text: string, query: string): React.ReactNode {
  if (!query) return text
  
  const parts = text.split(new RegExp(`(${escapeRegExp(query)})`, 'gi'))
  
  return parts.map((part, i) => 
    part.toLowerCase() === query.toLowerCase() ? (
      <span key={i} className="font-semibold text-primary-600 dark:text-primary-400">
        {part}
      </span>
    ) : (
      part
    )
  )
}

function escapeRegExp(string: string): string {
  return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * Hook to manage search suggestions state
 */
export function useSearchSuggestions() {
  const [isOpen, setIsOpen] = useState(false)
  const [query, setQuery] = useState('')

  return {
    isOpen,
    query,
    setQuery,
    open: () => setIsOpen(true),
    close: () => setIsOpen(false),
    toggle: () => setIsOpen(prev => !prev),
  }
}

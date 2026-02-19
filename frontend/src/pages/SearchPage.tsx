import { useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { MagnifyingGlassIcon, DocumentTextIcon, ClockIcon } from '@heroicons/react/24/outline'
import { searchApi, SearchResult, SearchResponse } from '../api/client'
import CopyButton from '../components/CopyButton'
import { useLocalStorage, STORAGE_KEYS } from '../hooks/useLocalStorage'
import { useKeyboardShortcuts } from '../hooks/useKeyboardShortcuts'

type SearchType = 'hybrid' | 'semantic' | 'text'

export default function SearchPage() {
  const { t } = useTranslation()
  const searchInputRef = useRef<HTMLInputElement>(null)
  
  // Persisted search settings
  const [query, setQuery] = useLocalStorage<string>(STORAGE_KEYS.SEARCH_QUERY, '')
  const [searchType, setSearchType] = useLocalStorage<SearchType>(STORAGE_KEYS.SEARCH_TYPE, 'hybrid')
  const [matchCount, setMatchCount] = useLocalStorage<number>(STORAGE_KEYS.SEARCH_MATCH_COUNT, 10)
  
  // Session-only state
  const [results, setResults] = useState<SearchResult[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [lastSearch, setLastSearch] = useState<SearchResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  
  // Track recent searches for quick access
  const [recentSearches, setRecentSearches] = useLocalStorage<string[]>(STORAGE_KEYS.RECENT_SEARCHES, [])
  const [showRecent, setShowRecent] = useState(false)

  // Keyboard shortcut to focus search
  useKeyboardShortcuts({
    shortcuts: [
      {
        key: '/',
        handler: () => searchInputRef.current?.focus(),
        ignoreInputs: true,
        description: 'Focus search input',
      },
    ],
  })

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!query.trim() || isLoading) return

    setIsLoading(true)
    setError(null)
    setShowRecent(false)

    try {
      const response = await searchApi.search(query.trim(), searchType, matchCount)
      setResults(response.results)
      setLastSearch(response)
      
      // Add to recent searches (keep last 10 unique)
      setRecentSearches(prev => {
        const filtered = prev.filter(s => s.toLowerCase() !== query.trim().toLowerCase())
        return [query.trim(), ...filtered].slice(0, 10)
      })
    } catch (err) {
      console.error('Search error:', err)
      setError('Failed to perform search. Please try again.')
      setResults([])
    } finally {
      setIsLoading(false)
    }
  }
  
  // Quick search from recent
  const handleQuickSearch = (searchQuery: string) => {
    setQuery(searchQuery)
    setShowRecent(false)
    // Trigger search after state update
    setTimeout(() => {
      const form = document.querySelector('form')
      form?.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }))
    }, 0)
  }

  return (
    <div className="space-y-6">
      {/* Search form */}
      <div className="rounded-3xl bg-surface dark:bg-gray-800 p-6 shadow-elevation-1">
        <form onSubmit={handleSearch} className="space-y-4">
          <div className="relative">
            <MagnifyingGlassIcon className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-secondary" />
            <input
              ref={searchInputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onFocus={() => recentSearches.length > 0 && setShowRecent(true)}
              onBlur={() => setTimeout(() => setShowRecent(false), 200)}
              placeholder={t('search.placeholder')}
              className="w-full rounded-2xl border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-700 py-3 pl-12 pr-4 text-primary-900 dark:text-gray-200 placeholder:text-secondary dark:placeholder:text-gray-500 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
            
            {/* Recent Searches Dropdown */}
            {showRecent && recentSearches.length > 0 && (
              <div className="absolute z-10 mt-1 w-full rounded-xl bg-white dark:bg-gray-700 shadow-lg border border-surface-variant dark:border-gray-600 overflow-hidden">
                <div className="px-3 py-2 text-xs font-medium text-secondary dark:text-gray-400 border-b border-surface-variant dark:border-gray-600">
                  Recent Searches
                </div>
                {recentSearches.slice(0, 5).map((search, idx) => (
                  <button
                    key={idx}
                    type="button"
                    onClick={() => handleQuickSearch(search)}
                    className="w-full px-4 py-2 text-left text-sm text-primary-900 dark:text-gray-200 hover:bg-surface-variant dark:hover:bg-gray-600 flex items-center gap-2"
                  >
                    <ClockIcon className="h-4 w-4 text-secondary" />
                    <span className="truncate">{search}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-4">
            {/* Search type selector */}
            <div className="flex rounded-xl bg-surface-variant dark:bg-gray-700 p-1">
              {(['hybrid', 'semantic', 'text'] as const).map((type) => (
                <button
                  key={type}
                  type="button"
                  onClick={() => setSearchType(type)}
                  className={`rounded-lg px-4 py-2 text-sm font-medium transition-all ${
                    searchType === type
                      ? 'bg-primary text-white shadow-sm'
                      : 'text-secondary dark:text-gray-400 hover:text-primary-700 dark:hover:text-primary-400'
                  }`}
                >
                  {type.charAt(0).toUpperCase() + type.slice(1)}
                </button>
              ))}
            </div>

            {/* Match count */}
            <div className="flex items-center gap-2">
              <label className="text-sm text-secondary dark:text-gray-400">Results:</label>
              <select
                value={matchCount}
                onChange={(e) => setMatchCount(Number(e.target.value))}
                className="rounded-lg border border-surface-variant dark:border-gray-600 bg-white dark:bg-gray-700 px-3 py-2 text-sm text-primary-900 dark:text-gray-200 focus:border-primary focus:outline-none"
              >
                {[5, 10, 20, 50].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </div>

            <button
              type="submit"
              disabled={!query.trim() || isLoading}
              className="ml-auto rounded-xl bg-primary px-6 py-2 font-medium text-white transition-all hover:bg-primary-700 disabled:bg-secondary disabled:cursor-not-allowed"
            >
              {isLoading ? t('common.loading') : t('search.searchButton')}
            </button>
          </div>
        </form>
      </div>

      {/* Search stats */}
      {lastSearch && (
        <div className="flex items-center gap-4 text-sm text-secondary">
          <span className="flex items-center gap-1">
            <DocumentTextIcon className="h-4 w-4" />
            {lastSearch.total_results} results
          </span>
          <span className="flex items-center gap-1">
            <ClockIcon className="h-4 w-4" />
            {lastSearch.processing_time_ms}ms
          </span>
          <span className="rounded-lg bg-surface-variant dark:bg-gray-700 px-2 py-1 text-primary-900 dark:text-gray-300">
            {lastSearch.search_type}
          </span>
        </div>
      )}

      {/* Error message */}
      {error && (
        <div className="rounded-2xl bg-red-50 dark:bg-red-900/30 p-4 text-red-700 dark:text-red-400">
          {error}
        </div>
      )}

      {/* Results */}
      <div className="space-y-4">
        {results.map((result, index) => (
          <div
            key={result.chunk_id || index}
            className="rounded-2xl bg-surface dark:bg-gray-800 p-5 shadow-elevation-1 transition-all hover:shadow-elevation-2"
          >
            <div className="mb-3 flex items-start justify-between">
              <div>
                <h3 className="font-medium text-primary-900 dark:text-gray-200">{result.document_title}</h3>
                <p className="text-sm text-secondary dark:text-gray-400">{result.document_source}</p>
              </div>
              <div className="flex items-center gap-2">
                <CopyButton 
                  text={result.content}
                  size="xs"
                  successMessage="Search result copied"
                  label="Copy result content"
                />
                <span className="rounded-lg bg-primary-100 dark:bg-primary-900/50 px-2 py-1 text-xs font-medium text-primary-700 dark:text-primary-300">
                  {Math.round(result.similarity * 100)}% match
                </span>
              </div>
            </div>
            <p className="text-sm text-primary-900/80 dark:text-gray-300 leading-relaxed">
              {result.content.length > 500
                ? result.content.slice(0, 500) + '...'
                : result.content}
            </p>
          </div>
        ))}

        {results.length === 0 && !isLoading && lastSearch && (
          <div className="rounded-2xl bg-surface-variant dark:bg-gray-800 p-8 text-center">
            <MagnifyingGlassIcon className="mx-auto h-12 w-12 text-secondary dark:text-gray-500 mb-3" />
            <p className="text-secondary dark:text-gray-400">{t('search.noResults')}</p>
          </div>
        )}
      </div>
    </div>
  )
}

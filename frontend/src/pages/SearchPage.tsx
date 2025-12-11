import { useState } from 'react'
import { MagnifyingGlassIcon, DocumentTextIcon, ClockIcon } from '@heroicons/react/24/outline'
import { searchApi, SearchResult, SearchResponse } from '../api/client'

type SearchType = 'hybrid' | 'semantic' | 'text'

export default function SearchPage() {
  const [query, setQuery] = useState('')
  const [searchType, setSearchType] = useState<SearchType>('hybrid')
  const [matchCount, setMatchCount] = useState(10)
  const [results, setResults] = useState<SearchResult[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const [lastSearch, setLastSearch] = useState<SearchResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!query.trim() || isLoading) return

    setIsLoading(true)
    setError(null)

    try {
      const response = await searchApi.search(query.trim(), searchType, matchCount)
      setResults(response.results)
      setLastSearch(response)
    } catch (err) {
      console.error('Search error:', err)
      setError('Failed to perform search. Please try again.')
      setResults([])
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Search form */}
      <div className="rounded-3xl bg-surface p-6 shadow-elevation-1">
        <form onSubmit={handleSearch} className="space-y-4">
          <div className="relative">
            <MagnifyingGlassIcon className="absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-secondary" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search your knowledge base..."
              className="w-full rounded-2xl border border-surface-variant bg-white py-3 pl-12 pr-4 text-primary-900 placeholder:text-secondary focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            />
          </div>

          <div className="flex flex-wrap items-center gap-4">
            {/* Search type selector */}
            <div className="flex rounded-xl bg-surface-variant p-1">
              {(['hybrid', 'semantic', 'text'] as const).map((type) => (
                <button
                  key={type}
                  type="button"
                  onClick={() => setSearchType(type)}
                  className={`rounded-lg px-4 py-2 text-sm font-medium transition-all ${
                    searchType === type
                      ? 'bg-primary text-white shadow-sm'
                      : 'text-secondary hover:text-primary-700'
                  }`}
                >
                  {type.charAt(0).toUpperCase() + type.slice(1)}
                </button>
              ))}
            </div>

            {/* Match count */}
            <div className="flex items-center gap-2">
              <label className="text-sm text-secondary">Results:</label>
              <select
                value={matchCount}
                onChange={(e) => setMatchCount(Number(e.target.value))}
                className="rounded-lg border border-surface-variant bg-white px-3 py-2 text-sm text-primary-900 focus:border-primary focus:outline-none"
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
              {isLoading ? 'Searching...' : 'Search'}
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
          <span className="rounded-lg bg-surface-variant px-2 py-1">
            {lastSearch.search_type}
          </span>
        </div>
      )}

      {/* Error message */}
      {error && (
        <div className="rounded-2xl bg-red-50 p-4 text-red-700">
          {error}
        </div>
      )}

      {/* Results */}
      <div className="space-y-4">
        {results.map((result, index) => (
          <div
            key={result.chunk_id || index}
            className="rounded-2xl bg-surface p-5 shadow-elevation-1 transition-all hover:shadow-elevation-2"
          >
            <div className="mb-3 flex items-start justify-between">
              <div>
                <h3 className="font-medium text-primary-900">{result.document_title}</h3>
                <p className="text-sm text-secondary">{result.document_source}</p>
              </div>
              <div className="flex items-center gap-2">
                <span className="rounded-lg bg-primary-100 px-2 py-1 text-xs font-medium text-primary-700">
                  {Math.round(result.similarity * 100)}% match
                </span>
              </div>
            </div>
            <p className="text-sm text-primary-900/80 leading-relaxed">
              {result.content.length > 500
                ? result.content.slice(0, 500) + '...'
                : result.content}
            </p>
          </div>
        ))}

        {results.length === 0 && !isLoading && lastSearch && (
          <div className="rounded-2xl bg-surface-variant p-8 text-center">
            <MagnifyingGlassIcon className="mx-auto h-12 w-12 text-secondary mb-3" />
            <p className="text-secondary">No results found for your query.</p>
          </div>
        )}
      </div>
    </div>
  )
}

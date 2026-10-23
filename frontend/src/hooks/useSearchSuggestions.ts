import { useState } from 'react'

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

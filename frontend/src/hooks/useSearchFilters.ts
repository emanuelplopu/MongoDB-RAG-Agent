import { useState, useCallback } from 'react'
import type { FilterValue } from '../components/SearchFilters'

/**
 * Hook to manage filter state
 */
export function useSearchFilters(initialValues: FilterValue = {}) {
  const [values, setValues] = useState<FilterValue>(initialValues)

  const handleChange = useCallback((newValues: FilterValue) => {
    setValues(newValues)
  }, [])

  const handleClear = useCallback(() => {
    setValues({})
  }, [])

  const handleRemove = useCallback((groupId: string, optionId: string) => {
    setValues(prev => {
      const current = (prev[groupId] as string[]) || []
      return {
        ...prev,
        [groupId]: current.filter(id => id !== optionId),
      }
    })
  }, [])

  return {
    values,
    setValues: handleChange,
    clear: handleClear,
    remove: handleRemove,
  }
}

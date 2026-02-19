/**
 * useLocalStorage - Custom hook for persisting state to localStorage
 * Provides automatic serialization/deserialization and handles storage errors gracefully
 */

import { useState, useEffect, useCallback, useRef } from 'react'

interface UseLocalStorageOptions<T> {
  /** Storage key prefix (defaults to 'recallhub_') */
  prefix?: string
  /** Serializer function (defaults to JSON.stringify) */
  serialize?: (value: T) => string
  /** Deserializer function (defaults to JSON.parse) */
  deserialize?: (value: string) => T
  /** Debounce delay in ms for saving (defaults to 300) */
  debounceMs?: number
}

/**
 * Hook for persisting state to localStorage with debouncing
 */
export function useLocalStorage<T>(
  key: string,
  initialValue: T,
  options: UseLocalStorageOptions<T> = {}
): [T, (value: T | ((prev: T) => T)) => void, () => void] {
  const {
    prefix = 'recallhub_',
    serialize = JSON.stringify,
    deserialize = JSON.parse,
    debounceMs = 300,
  } = options

  const fullKey = prefix + key
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout>>()

  // Initialize state from localStorage or use initial value
  const [storedValue, setStoredValue] = useState<T>(() => {
    try {
      const item = localStorage.getItem(fullKey)
      return item ? deserialize(item) : initialValue
    } catch (error) {
      console.warn(`Error reading localStorage key "${fullKey}":`, error)
      return initialValue
    }
  })

  // Debounced save to localStorage
  const saveToStorage = useCallback((value: T) => {
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current)
    }
    
    debounceTimerRef.current = setTimeout(() => {
      try {
        if (value === null || value === undefined || value === '') {
          localStorage.removeItem(fullKey)
        } else {
          localStorage.setItem(fullKey, serialize(value))
        }
      } catch (error) {
        console.warn(`Error saving to localStorage key "${fullKey}":`, error)
      }
    }, debounceMs)
  }, [fullKey, serialize, debounceMs])

  // Wrapper setValue that also persists to localStorage
  const setValue = useCallback((value: T | ((prev: T) => T)) => {
    setStoredValue(prev => {
      const newValue = value instanceof Function ? value(prev) : value
      saveToStorage(newValue)
      return newValue
    })
  }, [saveToStorage])

  // Clear stored value
  const clearValue = useCallback(() => {
    try {
      localStorage.removeItem(fullKey)
      setStoredValue(initialValue)
    } catch (error) {
      console.warn(`Error clearing localStorage key "${fullKey}":`, error)
    }
  }, [fullKey, initialValue])

  // Cleanup timer on unmount
  useEffect(() => {
    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current)
      }
    }
  }, [])

  return [storedValue, setValue, clearValue]
}

/**
 * Hook for managing form drafts with auto-save
 */
export function useFormDraft<T extends Record<string, unknown>>(
  formId: string,
  initialValues: T
): {
  values: T
  setValue: <K extends keyof T>(key: K, value: T[K]) => void
  setValues: (values: Partial<T>) => void
  resetForm: () => void
  isDirty: boolean
} {
  const [values, setStoredValues, clearValues] = useLocalStorage<T>(
    `form_draft_${formId}`,
    initialValues
  )
  
  const [isDirty, setIsDirty] = useState(false)

  const setValue = useCallback(<K extends keyof T>(key: K, value: T[K]) => {
    setStoredValues(prev => ({ ...prev, [key]: value }))
    setIsDirty(true)
  }, [setStoredValues])

  const setValues = useCallback((newValues: Partial<T>) => {
    setStoredValues(prev => ({ ...prev, ...newValues }))
    setIsDirty(true)
  }, [setStoredValues])

  const resetForm = useCallback(() => {
    clearValues()
    setIsDirty(false)
  }, [clearValues])

  return { values, setValue, setValues, resetForm, isDirty }
}

/**
 * Hook for warning users about unsaved changes before leaving
 */
export function useUnsavedChangesWarning(
  hasUnsavedChanges: boolean,
  message: string = 'You have unsaved changes. Are you sure you want to leave?'
): void {
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (hasUnsavedChanges) {
        e.preventDefault()
        e.returnValue = message
        return message
      }
    }

    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [hasUnsavedChanges, message])
}

// Storage keys registry for easy management
export const STORAGE_KEYS = {
  // Chat
  CHAT_DRAFT: 'chat_draft_',
  CHAT_AGENT_MODE: 'chat_agent_mode',
  
  // Search
  SEARCH_QUERY: 'search_query',
  SEARCH_TYPE: 'search_type',
  SEARCH_MATCH_COUNT: 'search_match_count',
  
  // Ingestion
  INGESTION_FORM: 'ingestion_form',
  
  // User preferences
  SIDEBAR_COLLAPSED: 'sidebar_collapsed',
  VIEW_MODE: 'view_mode',
  THEME: 'theme',
  
  // Recently used
  RECENT_SEARCHES: 'recent_searches',
  RECENT_DOCUMENTS: 'recent_documents',
} as const

export default useLocalStorage

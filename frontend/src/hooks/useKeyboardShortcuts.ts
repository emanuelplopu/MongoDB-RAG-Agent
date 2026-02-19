/**
 * useKeyboardShortcuts - Hook for managing keyboard shortcuts
 * Provides global keyboard shortcuts with proper focus handling
 */

import { useEffect, useCallback, useRef } from 'react'

interface ShortcutConfig {
  key: string
  ctrl?: boolean
  alt?: boolean
  shift?: boolean
  meta?: boolean
  handler: () => void
  description?: string
  /** If true, prevent default browser behavior */
  preventDefault?: boolean
  /** If true, only trigger when no input/textarea is focused */
  ignoreInputs?: boolean
}

interface UseKeyboardShortcutsOptions {
  enabled?: boolean
  shortcuts: ShortcutConfig[]
}

/**
 * Hook for registering keyboard shortcuts
 */
export function useKeyboardShortcuts({
  enabled = true,
  shortcuts,
}: UseKeyboardShortcutsOptions): void {
  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if (!enabled) return

      // Check if target is an input element
      const target = event.target as HTMLElement
      const isInputFocused =
        target.tagName === 'INPUT' ||
        target.tagName === 'TEXTAREA' ||
        target.isContentEditable

      for (const shortcut of shortcuts) {
        // Skip if shortcut ignores inputs and an input is focused
        if (shortcut.ignoreInputs && isInputFocused) continue

        // Check modifier keys
        const ctrlMatch = !!shortcut.ctrl === (event.ctrlKey || event.metaKey)
        const altMatch = !!shortcut.alt === event.altKey
        const shiftMatch = !!shortcut.shift === event.shiftKey

        // Check the key (case-insensitive)
        const keyMatch = event.key.toLowerCase() === shortcut.key.toLowerCase()

        if (keyMatch && ctrlMatch && altMatch && shiftMatch) {
          if (shortcut.preventDefault !== false) {
            event.preventDefault()
          }
          shortcut.handler()
          return
        }
      }
    },
    [enabled, shortcuts]
  )

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [handleKeyDown])
}

/**
 * Hook for handling Escape key to close modals/menus
 */
export function useEscapeKey(handler: () => void, enabled: boolean = true): void {
  useEffect(() => {
    if (!enabled) return

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        handler()
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [handler, enabled])
}

/**
 * Hook for trapping focus within a container (useful for modals)
 */
export function useFocusTrap(containerRef: React.RefObject<HTMLElement>, enabled: boolean = true): void {
  useEffect(() => {
    if (!enabled || !containerRef.current) return

    const container = containerRef.current
    const focusableElements = container.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    )
    
    if (focusableElements.length === 0) return

    const firstElement = focusableElements[0]
    const lastElement = focusableElements[focusableElements.length - 1]

    // Focus first element on mount
    firstElement.focus()

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Tab') return

      if (event.shiftKey) {
        if (document.activeElement === firstElement) {
          event.preventDefault()
          lastElement.focus()
        }
      } else {
        if (document.activeElement === lastElement) {
          event.preventDefault()
          firstElement.focus()
        }
      }
    }

    container.addEventListener('keydown', handleKeyDown)
    return () => container.removeEventListener('keydown', handleKeyDown)
  }, [containerRef, enabled])
}

/**
 * Hook for focus management - returns to previous element on unmount
 */
export function useRestoreFocus(): void {
  const previousElementRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    previousElementRef.current = document.activeElement as HTMLElement

    return () => {
      if (previousElementRef.current && typeof previousElementRef.current.focus === 'function') {
        previousElementRef.current.focus()
      }
    }
  }, [])
}

/**
 * Common keyboard shortcuts configuration
 */
export const COMMON_SHORTCUTS = {
  // Chat shortcuts
  newChat: { key: 'n', ctrl: true, description: 'New chat' },
  focusInput: { key: '/', description: 'Focus input' },
  sendMessage: { key: 'Enter', description: 'Send message' },
  
  // Navigation shortcuts
  search: { key: 'k', ctrl: true, description: 'Open search' },
  goBack: { key: 'Escape', description: 'Go back / Close' },
  
  // Action shortcuts
  copy: { key: 'c', ctrl: true, description: 'Copy' },
  paste: { key: 'v', ctrl: true, description: 'Paste' },
  save: { key: 's', ctrl: true, description: 'Save' },
  
  // Selection shortcuts
  selectAll: { key: 'a', ctrl: true, description: 'Select all' },
} as const

export type ShortcutName = keyof typeof COMMON_SHORTCUTS

export default useKeyboardShortcuts

/**
 * useClipboard - Custom hook for clipboard operations
 * Provides a simple interface to copy text to clipboard with success/error states
 */

import { useState, useCallback } from 'react'

interface UseClipboardOptions {
  /** Time in ms before resetting the copied state */
  timeout?: number
  /** Callback on successful copy */
  onSuccess?: () => void
  /** Callback on copy error */
  onError?: (error: Error) => void
}

interface UseClipboardReturn {
  /** Whether the last copy operation was successful */
  copied: boolean
  /** Error from the last copy operation, if any */
  error: Error | null
  /** Function to copy text to clipboard */
  copy: (text: string) => Promise<boolean>
  /** Reset the copied state */
  reset: () => void
}

export function useClipboard(options: UseClipboardOptions = {}): UseClipboardReturn {
  const { timeout = 2000, onSuccess, onError } = options
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const reset = useCallback(() => {
    setCopied(false)
    setError(null)
  }, [])

  const copy = useCallback(async (text: string): Promise<boolean> => {
    // Reset previous state
    setError(null)

    try {
      // Modern clipboard API
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text)
      } else {
        // Fallback for older browsers
        const textArea = document.createElement('textarea')
        textArea.value = text
        textArea.style.position = 'fixed'
        textArea.style.left = '-9999px'
        textArea.style.top = '-9999px'
        document.body.appendChild(textArea)
        textArea.focus()
        textArea.select()
        
        const success = document.execCommand('copy')
        document.body.removeChild(textArea)
        
        if (!success) {
          throw new Error('Failed to copy text using execCommand')
        }
      }

      setCopied(true)
      onSuccess?.()

      // Auto-reset after timeout
      if (timeout > 0) {
        setTimeout(() => {
          setCopied(false)
        }, timeout)
      }

      return true
    } catch (err) {
      const error = err instanceof Error ? err : new Error('Failed to copy to clipboard')
      setError(error)
      onError?.(error)
      return false
    }
  }, [timeout, onSuccess, onError])

  return { copied, error, copy, reset }
}

/**
 * Copy text to clipboard (non-hook version for one-off use)
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    } else {
      const textArea = document.createElement('textarea')
      textArea.value = text
      textArea.style.position = 'fixed'
      textArea.style.left = '-9999px'
      document.body.appendChild(textArea)
      textArea.focus()
      textArea.select()
      const success = document.execCommand('copy')
      document.body.removeChild(textArea)
      return success
    }
  } catch {
    return false
  }
}

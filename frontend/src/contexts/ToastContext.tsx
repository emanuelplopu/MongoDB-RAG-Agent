/**
 * ToastContext - Global toast notification system
 * Provides a simple interface to show success, error, warning, and info toasts.
 *
 * The provider and the toast UI live in ./ToastProvider.tsx.
 */

import { createContext, useContext } from 'react'

// Toast types
export type ToastType = 'success' | 'error' | 'warning' | 'info'

export interface Toast {
  id: string
  type: ToastType
  message: string
  description?: string
  duration?: number
  action?: {
    label: string
    onClick: () => void
  }
}

export interface ToastContextType {
  toasts: Toast[]
  addToast: (toast: Omit<Toast, 'id'>) => string
  removeToast: (id: string) => void
  clearToasts: () => void
  // Convenience methods
  success: (message: string, options?: Partial<Omit<Toast, 'id' | 'type' | 'message'>>) => string
  error: (message: string, options?: Partial<Omit<Toast, 'id' | 'type' | 'message'>>) => string
  warning: (message: string, options?: Partial<Omit<Toast, 'id' | 'type' | 'message'>>) => string
  info: (message: string, options?: Partial<Omit<Toast, 'id' | 'type' | 'message'>>) => string
}

export const ToastContext = createContext<ToastContextType | null>(null)

export function useToast() {
  const context = useContext(ToastContext)
  if (!context) {
    throw new Error('useToast must be used within ToastProvider')
  }
  return context
}

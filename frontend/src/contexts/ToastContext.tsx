/**
 * ToastContext - Global toast notification system
 * Provides a simple interface to show success, error, warning, and info toasts
 */

import { createContext, useContext, useState, useCallback, ReactNode } from 'react'
import { CheckCircleIcon, XCircleIcon, ExclamationTriangleIcon, InformationCircleIcon, XMarkIcon } from '@heroicons/react/24/outline'

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

interface ToastContextType {
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

const ToastContext = createContext<ToastContextType | null>(null)

export function useToast() {
  const context = useContext(ToastContext)
  if (!context) {
    throw new Error('useToast must be used within ToastProvider')
  }
  return context
}

// Generate unique ID
let toastIdCounter = 0
const generateId = () => `toast-${++toastIdCounter}-${Date.now()}`

// Default durations by type
const DEFAULT_DURATIONS: Record<ToastType, number> = {
  success: 3000,
  error: 5000,
  warning: 4000,
  info: 3000,
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const addToast = useCallback((toast: Omit<Toast, 'id'>): string => {
    const id = generateId()
    const duration = toast.duration ?? DEFAULT_DURATIONS[toast.type]
    
    setToasts(prev => [...prev, { ...toast, id }])

    // Auto-remove after duration (unless duration is 0)
    if (duration > 0) {
      setTimeout(() => {
        setToasts(prev => prev.filter(t => t.id !== id))
      }, duration)
    }

    return id
  }, [])

  const removeToast = useCallback((id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  const clearToasts = useCallback(() => {
    setToasts([])
  }, [])

  // Convenience methods
  const success = useCallback((message: string, options?: Partial<Omit<Toast, 'id' | 'type' | 'message'>>) => {
    return addToast({ type: 'success', message, ...options })
  }, [addToast])

  const error = useCallback((message: string, options?: Partial<Omit<Toast, 'id' | 'type' | 'message'>>) => {
    return addToast({ type: 'error', message, ...options })
  }, [addToast])

  const warning = useCallback((message: string, options?: Partial<Omit<Toast, 'id' | 'type' | 'message'>>) => {
    return addToast({ type: 'warning', message, ...options })
  }, [addToast])

  const info = useCallback((message: string, options?: Partial<Omit<Toast, 'id' | 'type' | 'message'>>) => {
    return addToast({ type: 'info', message, ...options })
  }, [addToast])

  const value: ToastContextType = {
    toasts,
    addToast,
    removeToast,
    clearToasts,
    success,
    error,
    warning,
    info,
  }

  return (
    <ToastContext.Provider value={value}>
      {children}
      <ToastContainer toasts={toasts} onRemove={removeToast} />
    </ToastContext.Provider>
  )
}

// Toast Container - renders all active toasts
function ToastContainer({ toasts, onRemove }: { toasts: Toast[]; onRemove: (id: string) => void }) {
  if (toasts.length === 0) return null

  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2 max-w-sm w-full pointer-events-none">
      {toasts.map(toast => (
        <ToastItem key={toast.id} toast={toast} onRemove={onRemove} />
      ))}
    </div>
  )
}

// Individual Toast Item
function ToastItem({ toast, onRemove }: { toast: Toast; onRemove: (id: string) => void }) {
  const config = {
    success: {
      icon: CheckCircleIcon,
      bgClass: 'bg-green-50 dark:bg-green-900/90',
      borderClass: 'border-green-200 dark:border-green-700',
      iconClass: 'text-green-500 dark:text-green-400',
      textClass: 'text-green-800 dark:text-green-100',
      descClass: 'text-green-600 dark:text-green-300',
    },
    error: {
      icon: XCircleIcon,
      bgClass: 'bg-red-50 dark:bg-red-900/90',
      borderClass: 'border-red-200 dark:border-red-700',
      iconClass: 'text-red-500 dark:text-red-400',
      textClass: 'text-red-800 dark:text-red-100',
      descClass: 'text-red-600 dark:text-red-300',
    },
    warning: {
      icon: ExclamationTriangleIcon,
      bgClass: 'bg-yellow-50 dark:bg-yellow-900/90',
      borderClass: 'border-yellow-200 dark:border-yellow-700',
      iconClass: 'text-yellow-500 dark:text-yellow-400',
      textClass: 'text-yellow-800 dark:text-yellow-100',
      descClass: 'text-yellow-600 dark:text-yellow-300',
    },
    info: {
      icon: InformationCircleIcon,
      bgClass: 'bg-blue-50 dark:bg-blue-900/90',
      borderClass: 'border-blue-200 dark:border-blue-700',
      iconClass: 'text-blue-500 dark:text-blue-400',
      textClass: 'text-blue-800 dark:text-blue-100',
      descClass: 'text-blue-600 dark:text-blue-300',
    },
  }

  const { icon: Icon, bgClass, borderClass, iconClass, textClass, descClass } = config[toast.type]

  return (
    <div
      className={`pointer-events-auto flex items-start gap-3 p-4 rounded-xl border shadow-lg backdrop-blur-sm animate-slide-in-right ${bgClass} ${borderClass}`}
      role="alert"
      aria-live="polite"
    >
      <Icon className={`h-5 w-5 flex-shrink-0 ${iconClass}`} />
      <div className="flex-1 min-w-0">
        <p className={`text-sm font-medium ${textClass}`}>{toast.message}</p>
        {toast.description && (
          <p className={`mt-1 text-xs ${descClass}`}>{toast.description}</p>
        )}
        {toast.action && (
          <button
            onClick={() => {
              toast.action?.onClick()
              onRemove(toast.id)
            }}
            className={`mt-2 text-xs font-medium underline hover:no-underline ${textClass}`}
          >
            {toast.action.label}
          </button>
        )}
      </div>
      <button
        onClick={() => onRemove(toast.id)}
        className={`flex-shrink-0 p-1 rounded-lg hover:bg-black/5 dark:hover:bg-white/10 transition-colors ${iconClass}`}
        aria-label="Dismiss"
      >
        <XMarkIcon className="h-4 w-4" />
      </button>
    </div>
  )
}

// CSS animation (add to index.css)
// @keyframes slide-in-right {
//   from { transform: translateX(100%); opacity: 0; }
//   to { transform: translateX(0); opacity: 1; }
// }
// .animate-slide-in-right { animation: slide-in-right 0.3s ease-out; }

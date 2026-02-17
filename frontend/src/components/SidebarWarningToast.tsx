import { XMarkIcon, ExclamationTriangleIcon, ExclamationCircleIcon } from '@heroicons/react/24/outline'
import { useLocalizedNavigate } from './LocalizedLink'

export type WarningLevel = 'warning' | 'critical'

export interface SidebarWarning {
  id: string
  level: WarningLevel
  message: string
  /** Path to navigate to when clicking the toast */
  path?: string
  /** Action to perform when clicking the toast (alternative to path) */
  action?: () => void
  /** Label for the action button (optional) */
  actionLabel?: string
}

interface SidebarWarningToastProps {
  warnings: SidebarWarning[]
  onDismiss: (id: string) => void
}

export default function SidebarWarningToast({ warnings, onDismiss }: SidebarWarningToastProps) {
  const navigate = useLocalizedNavigate()

  if (warnings.length === 0) return null

  const handleClick = (warning: SidebarWarning) => {
    if (warning.action) {
      warning.action()
    } else if (warning.path) {
      navigate(warning.path)
    }
  }

  const getStyles = (level: WarningLevel) => {
    if (level === 'critical') {
      return {
        container: 'bg-red-50 dark:bg-red-900/30 border-red-200 dark:border-red-800',
        icon: 'text-red-500 dark:text-red-400',
        text: 'text-red-700 dark:text-red-300',
        dismissBtn: 'text-red-400 hover:text-red-600 dark:text-red-500 dark:hover:text-red-300',
        actionText: 'text-red-600 dark:text-red-400 underline',
      }
    }
    // warning level
    return {
      container: 'bg-amber-50 dark:bg-amber-900/30 border-amber-200 dark:border-amber-800',
      icon: 'text-amber-500 dark:text-amber-400',
      text: 'text-amber-700 dark:text-amber-300',
      dismissBtn: 'text-amber-400 hover:text-amber-600 dark:text-amber-500 dark:hover:text-amber-300',
      actionText: 'text-amber-600 dark:text-amber-400 underline',
    }
  }

  return (
    <div className="px-3 mb-2 space-y-2">
      {warnings.map((warning) => {
        const styles = getStyles(warning.level)
        const isClickable = warning.path || warning.action
        const Icon = warning.level === 'critical' ? ExclamationCircleIcon : ExclamationTriangleIcon

        return (
          <div
            key={warning.id}
            className={`relative rounded-lg border p-2.5 ${styles.container} ${
              isClickable ? 'cursor-pointer hover:opacity-90 transition-opacity' : ''
            }`}
            onClick={() => isClickable && handleClick(warning)}
            role={isClickable ? 'button' : undefined}
            tabIndex={isClickable ? 0 : undefined}
            onKeyDown={(e) => {
              if (isClickable && (e.key === 'Enter' || e.key === ' ')) {
                e.preventDefault()
                handleClick(warning)
              }
            }}
          >
            {/* Dismiss button */}
            <button
              onClick={(e) => {
                e.stopPropagation()
                onDismiss(warning.id)
              }}
              className={`absolute top-1.5 right-1.5 p-0.5 rounded-md ${styles.dismissBtn} hover:bg-black/5 dark:hover:bg-white/5`}
              aria-label="Dismiss warning"
            >
              <XMarkIcon className="h-3.5 w-3.5" />
            </button>

            <div className="flex items-start gap-2 pr-5">
              <Icon className={`h-4 w-4 flex-shrink-0 mt-0.5 ${styles.icon}`} />
              <div className="flex-1 min-w-0">
                <p className={`text-xs leading-snug ${styles.text}`}>
                  {warning.message}
                </p>
                {warning.actionLabel && isClickable && (
                  <span className={`text-xs font-medium ${styles.actionText} mt-1 inline-block`}>
                    {warning.actionLabel}
                  </span>
                )}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

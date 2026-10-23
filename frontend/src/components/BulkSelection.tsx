/**
 * Bulk-selection UI that pairs with the useSelection hook:
 * a floating action bar, its buttons and a select-all checkbox.
 */

/**
 * BulkActionBar - Floating action bar for bulk operations
 */
interface BulkActionBarProps {
  selectionCount: number
  onClear: () => void
  children: React.ReactNode
  className?: string
}

export function BulkActionBar({
  selectionCount,
  onClear,
  children,
  className = '',
}: BulkActionBarProps) {
  if (selectionCount === 0) return null

  return (
    <div className={`
      fixed bottom-6 left-1/2 -translate-x-1/2 z-40
      bg-white dark:bg-gray-800 rounded-2xl shadow-xl border border-gray-200 dark:border-gray-700
      px-4 py-3 flex items-center gap-4
      animate-fade-in-up
      ${className}
    `}>
      <div className="flex items-center gap-2">
        <span className="w-6 h-6 rounded-full bg-primary text-white text-xs font-medium flex items-center justify-center">
          {selectionCount}
        </span>
        <span className="text-sm text-gray-600 dark:text-gray-300">
          item{selectionCount !== 1 ? 's' : ''} selected
        </span>
      </div>

      <div className="h-6 w-px bg-gray-200 dark:bg-gray-700" />

      <div className="flex items-center gap-2">
        {children}
      </div>

      <div className="h-6 w-px bg-gray-200 dark:bg-gray-700" />

      <button
        onClick={onClear}
        className="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
      >
        Clear
      </button>
    </div>
  )
}

/**
 * BulkActionButton - Individual action button for bulk operations
 */
interface BulkActionButtonProps {
  icon: React.ReactNode
  label: string
  onClick: () => void
  variant?: 'default' | 'danger'
  disabled?: boolean
}

export function BulkActionButton({
  icon,
  label,
  onClick,
  variant = 'default',
  disabled = false,
}: BulkActionButtonProps) {
  const variantClasses = {
    default: 'text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700',
    danger: 'text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20',
  }

  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`
        flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm font-medium
        transition-colors disabled:opacity-50 disabled:cursor-not-allowed
        ${variantClasses[variant]}
      `}
    >
      {icon}
      <span>{label}</span>
    </button>
  )
}

/**
 * SelectAllCheckbox - Checkbox with indeterminate state support
 */
interface SelectAllCheckboxProps {
  checked: boolean
  indeterminate: boolean
  onChange: () => void
  label?: string
  className?: string
}

export function SelectAllCheckbox({
  checked,
  indeterminate,
  onChange,
  label,
  className = '',
}: SelectAllCheckboxProps) {
  return (
    <label className={`flex items-center gap-2 cursor-pointer ${className}`}>
      <div className="relative">
        <input
          type="checkbox"
          checked={checked}
          onChange={onChange}
          className="sr-only peer"
        />
        <div className={`
          w-5 h-5 rounded border-2 transition-colors
          ${checked || indeterminate
            ? 'bg-primary border-primary'
            : 'border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800'
          }
          peer-focus-visible:ring-2 peer-focus-visible:ring-primary peer-focus-visible:ring-offset-2
        `}>
          {checked && !indeterminate && (
            <svg className="w-full h-full text-white" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
            </svg>
          )}
          {indeterminate && (
            <svg className="w-full h-full text-white" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M3 10a1 1 0 011-1h12a1 1 0 110 2H4a1 1 0 01-1-1z" clipRule="evenodd" />
            </svg>
          )}
        </div>
      </div>
      {label && <span className="text-sm text-gray-700 dark:text-gray-300">{label}</span>}
    </label>
  )
}

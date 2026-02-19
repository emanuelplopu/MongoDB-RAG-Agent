/**
 * useSelection - Hook for managing multi-selection with range select support
 * Supports Shift+click for range selection and Ctrl+click for toggle
 */

import { useState, useCallback, useRef } from 'react'

interface UseSelectionOptions<T> {
  /** Get unique ID from item */
  getItemId: (item: T) => string
  /** Callback when selection changes */
  onSelectionChange?: (selectedIds: Set<string>) => void
  /** Maximum items that can be selected (0 = unlimited) */
  maxSelections?: number
}

interface UseSelectionReturn<T> {
  /** Currently selected item IDs */
  selectedIds: Set<string>
  /** Check if an item is selected */
  isSelected: (item: T) => boolean
  /** Toggle selection of a single item */
  toggleSelection: (item: T, event?: React.MouseEvent) => void
  /** Select a single item (replacing current selection) */
  selectItem: (item: T) => void
  /** Select multiple items */
  selectItems: (items: T[]) => void
  /** Select all items */
  selectAll: (items: T[]) => void
  /** Clear all selections */
  clearSelection: () => void
  /** Select a range of items (for shift+click) */
  selectRange: (items: T[], toItem: T) => void
  /** Number of selected items */
  selectionCount: number
  /** Whether any items are selected */
  hasSelection: boolean
  /** Whether all items in a list are selected */
  isAllSelected: (items: T[]) => boolean
  /** Whether some (but not all) items are selected */
  isIndeterminate: (items: T[]) => boolean
  /** Handle click with modifier key support */
  handleItemClick: (item: T, items: T[], event: React.MouseEvent) => void
}

export function useSelection<T>({
  getItemId,
  onSelectionChange,
  maxSelections = 0,
}: UseSelectionOptions<T>): UseSelectionReturn<T> {
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const lastSelectedRef = useRef<string | null>(null)

  const updateSelection = useCallback((newSelection: Set<string>) => {
    setSelectedIds(newSelection)
    onSelectionChange?.(newSelection)
  }, [onSelectionChange])

  const isSelected = useCallback((item: T): boolean => {
    return selectedIds.has(getItemId(item))
  }, [selectedIds, getItemId])

  const toggleSelection = useCallback((item: T, _event?: React.MouseEvent) => {
    const id = getItemId(item)
    const newSelection = new Set(selectedIds)
    
    if (newSelection.has(id)) {
      newSelection.delete(id)
    } else {
      if (maxSelections > 0 && newSelection.size >= maxSelections) {
        return // Don't exceed max
      }
      newSelection.add(id)
    }
    
    lastSelectedRef.current = id
    updateSelection(newSelection)
  }, [selectedIds, getItemId, maxSelections, updateSelection])

  const selectItem = useCallback((item: T) => {
    const id = getItemId(item)
    lastSelectedRef.current = id
    updateSelection(new Set([id]))
  }, [getItemId, updateSelection])

  const selectItems = useCallback((items: T[]) => {
    const ids = items.map(getItemId)
    if (maxSelections > 0) {
      updateSelection(new Set(ids.slice(0, maxSelections)))
    } else {
      updateSelection(new Set(ids))
    }
  }, [getItemId, maxSelections, updateSelection])

  const selectAll = useCallback((items: T[]) => {
    selectItems(items)
  }, [selectItems])

  const clearSelection = useCallback(() => {
    lastSelectedRef.current = null
    updateSelection(new Set())
  }, [updateSelection])

  const selectRange = useCallback((items: T[], toItem: T) => {
    if (!lastSelectedRef.current) {
      toggleSelection(toItem)
      return
    }

    const itemIds = items.map(getItemId)
    const fromIndex = itemIds.indexOf(lastSelectedRef.current)
    const toIndex = itemIds.indexOf(getItemId(toItem))

    if (fromIndex === -1 || toIndex === -1) {
      toggleSelection(toItem)
      return
    }

    const start = Math.min(fromIndex, toIndex)
    const end = Math.max(fromIndex, toIndex)
    const rangeIds = itemIds.slice(start, end + 1)

    const newSelection = new Set(selectedIds)
    rangeIds.forEach(id => {
      if (maxSelections === 0 || newSelection.size < maxSelections) {
        newSelection.add(id)
      }
    })
    
    updateSelection(newSelection)
  }, [selectedIds, getItemId, toggleSelection, maxSelections, updateSelection])

  const handleItemClick = useCallback((item: T, items: T[], event: React.MouseEvent) => {
    if (event.shiftKey && lastSelectedRef.current) {
      // Shift+click: range select
      event.preventDefault()
      selectRange(items, item)
    } else if (event.ctrlKey || event.metaKey) {
      // Ctrl/Cmd+click: toggle
      event.preventDefault()
      toggleSelection(item)
    } else {
      // Normal click: single select
      selectItem(item)
    }
  }, [selectRange, toggleSelection, selectItem])

  const isAllSelected = useCallback((items: T[]): boolean => {
    if (items.length === 0) return false
    return items.every(item => selectedIds.has(getItemId(item)))
  }, [selectedIds, getItemId])

  const isIndeterminate = useCallback((items: T[]): boolean => {
    if (items.length === 0) return false
    const selectedCount = items.filter(item => selectedIds.has(getItemId(item))).length
    return selectedCount > 0 && selectedCount < items.length
  }, [selectedIds, getItemId])

  return {
    selectedIds,
    isSelected,
    toggleSelection,
    selectItem,
    selectItems,
    selectAll,
    clearSelection,
    selectRange,
    selectionCount: selectedIds.size,
    hasSelection: selectedIds.size > 0,
    isAllSelected,
    isIndeterminate,
    handleItemClick,
  }
}

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

export default useSelection

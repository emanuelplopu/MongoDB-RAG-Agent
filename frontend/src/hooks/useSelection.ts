/**
 * useSelection - Hook for managing multi-selection with range select support
 * Supports Shift+click for range selection and Ctrl+click for toggle.
 *
 * The matching bulk-action UI lives in ../components/BulkSelection.tsx.
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

export default useSelection

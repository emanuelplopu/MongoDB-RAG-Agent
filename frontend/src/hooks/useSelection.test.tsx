import { fireEvent, render, renderHook, screen } from '@testing-library/react'
import { act } from 'react'
import { describe, expect, it, vi } from 'vitest'

import useSelection, {
  BulkActionBar,
  BulkActionButton,
  SelectAllCheckbox,
} from './useSelection'

interface Item {
  id: string
  label: string
}

const items: Item[] = [
  { id: 'a', label: 'Alpha' },
  { id: 'b', label: 'Beta' },
  { id: 'c', label: 'Gamma' },
  { id: 'd', label: 'Delta' },
]

describe('useSelection', () => {
  it('selects, toggles, clears, and tracks aggregate state', () => {
    const onSelectionChange = vi.fn()
    const { result } = renderHook(() =>
      useSelection<Item>({
        getItemId: (item) => item.id,
        onSelectionChange,
      })
    )

    act(() => {
      result.current.selectItem(items[0])
    })
    expect(result.current.isSelected(items[0])).toBe(true)
    expect(result.current.selectionCount).toBe(1)
    expect(result.current.hasSelection).toBe(true)

    act(() => {
      result.current.toggleSelection(items[1])
    })
    expect(Array.from(result.current.selectedIds)).toEqual(['a', 'b'])
    expect(result.current.isIndeterminate(items)).toBe(true)

    act(() => {
      result.current.clearSelection()
    })
    expect(result.current.selectionCount).toBe(0)
    expect(result.current.hasSelection).toBe(false)
    expect(onSelectionChange).toHaveBeenCalled()
  })

  it('supports select-all, ranged selection, and click modifiers', () => {
    const { result } = renderHook(() =>
      useSelection<Item>({
        getItemId: (item) => item.id,
      })
    )

    act(() => {
      result.current.selectAll(items)
    })
    expect(result.current.isAllSelected(items)).toBe(true)

    act(() => {
      result.current.clearSelection()
    })
    act(() => {
      result.current.selectItem(items[0])
    })
    act(() => {
      result.current.selectRange(items, items[2])
    })
    expect(Array.from(result.current.selectedIds)).toEqual(['a', 'b', 'c'])

    const preventDefault = vi.fn()
    act(() => {
      result.current.handleItemClick(items[3], items, {
        ctrlKey: true,
        metaKey: false,
        shiftKey: false,
        preventDefault,
      } as unknown as React.MouseEvent)
    })
    expect(preventDefault).toHaveBeenCalledTimes(1)
    expect(result.current.isSelected(items[3])).toBe(true)

    act(() => {
      result.current.handleItemClick(items[1], items, {
        ctrlKey: false,
        metaKey: false,
        shiftKey: true,
        preventDefault,
      } as unknown as React.MouseEvent)
    })
    expect(result.current.isSelected(items[1])).toBe(true)
  })

  it('enforces maxSelections across multi-select paths', () => {
    const { result } = renderHook(() =>
      useSelection<Item>({
        getItemId: (item) => item.id,
        maxSelections: 2,
      })
    )

    act(() => {
      result.current.selectItems(items)
    })
    expect(Array.from(result.current.selectedIds)).toEqual(['a', 'b'])

    act(() => {
      result.current.toggleSelection(items[2])
    })
    expect(Array.from(result.current.selectedIds)).toEqual(['a', 'b'])

    act(() => {
      result.current.clearSelection()
    })
    act(() => {
      result.current.selectRange(items, items[3])
    })
    expect(Array.from(result.current.selectedIds)).toEqual(['d'])
  })
})

describe('useSelection UI helpers', () => {
  it('renders the bulk action bar only when something is selected', () => {
    const onClear = vi.fn()
    const { container, rerender } = render(
      <BulkActionBar selectionCount={0} onClear={onClear}>
        <button>Archive</button>
      </BulkActionBar>
    )

    expect(screen.queryByText('Archive')).not.toBeInTheDocument()

    rerender(
      <BulkActionBar selectionCount={2} onClear={onClear} className="bulk-shell">
        <button>Archive</button>
      </BulkActionBar>
    )

    expect(screen.getByText('items selected')).toBeInTheDocument()
    expect(screen.getByText('Archive')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Clear' }))
    expect(onClear).toHaveBeenCalledTimes(1)
    expect(container.firstChild).toHaveClass('bulk-shell')
  })

  it('renders bulk action buttons with variants and disabled state', () => {
    const onClick = vi.fn()
    const { rerender } = render(
      <BulkActionButton icon={<span>!</span>} label="Delete" onClick={onClick} variant="danger" />
    )

    const dangerButton = screen.getByRole('button', { name: /Delete/ })
    expect(dangerButton.className).toContain('text-red-600')
    fireEvent.click(dangerButton)
    expect(onClick).toHaveBeenCalledTimes(1)

    rerender(
      <BulkActionButton icon={<span>+</span>} label="Merge" onClick={onClick} disabled />
    )
    expect(screen.getByRole('button', { name: /Merge/ })).toBeDisabled()
  })

  it('renders select-all checkbox states and calls onChange', () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <SelectAllCheckbox checked={false} indeterminate={false} onChange={onChange} label="Select all" />
    )

    const checkbox = screen.getByRole('checkbox')
    fireEvent.click(checkbox)
    expect(onChange).toHaveBeenCalledTimes(1)
    expect(screen.getByText('Select all')).toBeInTheDocument()

    rerender(<SelectAllCheckbox checked indeterminate={false} onChange={onChange} />)
    expect(document.querySelector('svg')).not.toBeNull()

    rerender(<SelectAllCheckbox checked={false} indeterminate onChange={onChange} className="check-shell" />)
    expect(document.querySelectorAll('svg').length).toBeGreaterThan(0)
    expect(checkbox.parentElement?.parentElement).toHaveClass('check-shell')
  })
})

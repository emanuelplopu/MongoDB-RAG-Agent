import { act, renderHook } from '@testing-library/react'

import {
  COMMON_SHORTCUTS,
  useEscapeKey,
  useFocusTrap,
  useKeyboardShortcuts,
  useRestoreFocus,
} from './useKeyboardShortcuts'

describe('useKeyboardShortcuts', () => {
  it('fires matching shortcuts and respects preventDefault', () => {
    const handler = vi.fn()

    renderHook(() =>
      useKeyboardShortcuts({
        shortcuts: [{ key: 'k', ctrl: true, handler }],
      })
    )

    const event = new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true, cancelable: true })
    window.dispatchEvent(event)

    expect(handler).toHaveBeenCalledTimes(1)
    expect(event.defaultPrevented).toBe(true)
  })

  it('ignores shortcuts when disabled or when inputs are focused', () => {
    const handler = vi.fn()
    const input = document.createElement('input')
    document.body.appendChild(input)
    input.focus()

    renderHook(() =>
      useKeyboardShortcuts({
        enabled: false,
        shortcuts: [{ key: 's', ctrl: true, handler }],
      })
    )
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 's', ctrlKey: true, bubbles: true }))

    renderHook(() =>
      useKeyboardShortcuts({
        shortcuts: [{ key: 's', ctrl: true, handler, ignoreInputs: true }],
      })
    )
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 's', ctrlKey: true, bubbles: true }))

    expect(handler).not.toHaveBeenCalled()
    input.remove()
  })
})

describe('useEscapeKey', () => {
  it('calls the handler only when enabled', () => {
    const handler = vi.fn()
    const { rerender } = renderHook(
      ({ enabled }) => useEscapeKey(handler, enabled),
      { initialProps: { enabled: true } }
    )

    const firstEvent = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })
    window.dispatchEvent(firstEvent)
    expect(handler).toHaveBeenCalledTimes(1)
    expect(firstEvent.defaultPrevented).toBe(true)

    rerender({ enabled: false })
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    expect(handler).toHaveBeenCalledTimes(1)
  })
})

describe('useFocusTrap', () => {
  it('focuses the first element and traps tab navigation', () => {
    const container = document.createElement('div')
    const firstButton = document.createElement('button')
    const secondButton = document.createElement('button')
    container.appendChild(firstButton)
    container.appendChild(secondButton)
    document.body.appendChild(container)

    renderHook(() => useFocusTrap({ current: container }, true))

    expect(document.activeElement).toBe(firstButton)

    secondButton.focus()
    const forwardTab = new KeyboardEvent('keydown', { key: 'Tab', bubbles: true, cancelable: true })
    secondButton.dispatchEvent(forwardTab)
    expect(document.activeElement).toBe(firstButton)

    firstButton.focus()
    const backwardTab = new KeyboardEvent('keydown', { key: 'Tab', shiftKey: true, bubbles: true, cancelable: true })
    firstButton.dispatchEvent(backwardTab)
    expect(document.activeElement).toBe(secondButton)

    container.remove()
  })
})

describe('useRestoreFocus', () => {
  it('restores focus to the previously active element on unmount', () => {
    const button = document.createElement('button')
    document.body.appendChild(button)
    button.focus()

    const { unmount } = renderHook(() => useRestoreFocus())

    act(() => {
      unmount()
    })

    expect(document.activeElement).toBe(button)
    button.remove()
  })
})

describe('COMMON_SHORTCUTS', () => {
  it('exposes shared shortcut definitions', () => {
    expect(COMMON_SHORTCUTS.newChat).toEqual({ key: 'n', ctrl: true, description: 'New chat' })
    expect(COMMON_SHORTCUTS.goBack.key).toBe('Escape')
  })
})

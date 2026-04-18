import { act, renderHook } from '@testing-library/react'
import { vi } from 'vitest'

import {
  STORAGE_KEYS,
  useFormDraft,
  useLocalStorage,
  useUnsavedChangesWarning,
} from './useLocalStorage'

describe('useLocalStorage', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    window.localStorage.clear()
  })

  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
  })

  it('reads the initial value from localStorage and saves updates', () => {
    window.localStorage.setItem('recallhub_theme', JSON.stringify('dark'))

    const { result } = renderHook(() => useLocalStorage('theme', 'light'))

    expect(result.current[0]).toBe('dark')

    act(() => {
      result.current[1]('system')
      vi.advanceTimersByTime(300)
    })

    expect(window.localStorage.getItem('recallhub_theme')).toBe(JSON.stringify('system'))
  })

  it('supports updater functions, custom prefixes, and clearing', () => {
    const { result } = renderHook(() =>
      useLocalStorage('count', 1, { prefix: 'custom_', debounceMs: 50 })
    )

    act(() => {
      result.current[1]((previous) => previous + 1)
      vi.advanceTimersByTime(50)
    })

    expect(result.current[0]).toBe(2)
    expect(window.localStorage.getItem('custom_count')).toBe('2')

    act(() => {
      result.current[2]()
    })

    expect(result.current[0]).toBe(1)
    expect(window.localStorage.getItem('custom_count')).toBeNull()
  })

  it('removes empty values and falls back gracefully on read/write errors', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const getItemSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('read failed')
    })

    const { result } = renderHook(() => useLocalStorage('query', 'default'))

    expect(result.current[0]).toBe('default')

    getItemSpy.mockRestore()
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('write failed')
    })

    act(() => {
      result.current[1]('')
      vi.advanceTimersByTime(300)
    })

    expect(warnSpy).toHaveBeenCalled()

    setItemSpy.mockRestore()
    warnSpy.mockRestore()
  })
})

describe('useFormDraft', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    window.localStorage.clear()
  })

  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
  })

  it('tracks draft values and dirty state', () => {
    const { result } = renderHook(() =>
      useFormDraft('profile', { name: '', email: '' })
    )

    expect(result.current.isDirty).toBe(false)

    act(() => {
      result.current.setValue('name', 'Ada')
    })
    act(() => {
      result.current.setValues({ email: 'ada@example.com' })
    })
    act(() => {
      vi.advanceTimersByTime(300)
    })

    expect(result.current.values).toEqual({ name: 'Ada', email: 'ada@example.com' })
    expect(result.current.isDirty).toBe(true)
    expect(window.localStorage.getItem('recallhub_form_draft_profile')).toContain('ada@example.com')

    act(() => {
      result.current.resetForm()
    })

    expect(result.current.values).toEqual({ name: '', email: '' })
    expect(result.current.isDirty).toBe(false)
  })
})

describe('useUnsavedChangesWarning', () => {
  it('attaches a beforeunload warning only when changes exist', () => {
    const addSpy = vi.spyOn(window, 'addEventListener')
    const removeSpy = vi.spyOn(window, 'removeEventListener')

    const { rerender, unmount } = renderHook(
      ({ dirty }) => useUnsavedChangesWarning(dirty, 'Custom warning'),
      { initialProps: { dirty: true } }
    )

    expect(addSpy).toHaveBeenCalledWith('beforeunload', expect.any(Function))

    const handler = addSpy.mock.calls.find(([eventName]) => eventName === 'beforeunload')?.[1] as EventListener
    const event = new Event('beforeunload') as BeforeUnloadEvent
    Object.defineProperty(event, 'returnValue', { writable: true, value: '' })
    handler(event)
    expect(event.returnValue).toBe('Custom warning')

    rerender({ dirty: false })
    unmount()

    expect(removeSpy).toHaveBeenCalledWith('beforeunload', expect.any(Function))
  })
})

describe('STORAGE_KEYS', () => {
  it('exposes stable storage key constants', () => {
    expect(STORAGE_KEYS.CHAT_AGENT_MODE).toBe('chat_agent_mode')
    expect(STORAGE_KEYS.RECENT_DOCUMENTS).toBe('recent_documents')
  })
})

import { act, renderHook } from '@testing-library/react'
import { vi } from 'vitest'

import { copyToClipboard, useClipboard } from './useClipboard'

describe('useClipboard', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    Object.defineProperty(document, 'execCommand', {
      value: vi.fn(),
      configurable: true,
      writable: true,
    })
  })

  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
  })

  it('copies text with the modern clipboard API and auto-resets', async () => {
    const onSuccess = vi.fn()
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })

    const { result } = renderHook(() => useClipboard({ timeout: 100, onSuccess }))

    await act(async () => {
      expect(await result.current.copy('hello')).toBe(true)
    })

    expect(writeText).toHaveBeenCalledWith('hello')
    expect(onSuccess).toHaveBeenCalledTimes(1)
    expect(result.current.copied).toBe(true)

    act(() => {
      vi.advanceTimersByTime(100)
    })
    expect(result.current.copied).toBe(false)
  })

  it('falls back to execCommand and captures errors', async () => {
    const onError = vi.fn()
    Object.assign(navigator, { clipboard: undefined })
    const execCommandMock = vi.mocked(document.execCommand).mockReturnValue(false)

    const { result } = renderHook(() => useClipboard({ timeout: 0, onError }))

    await act(async () => {
      expect(await result.current.copy('fallback')).toBe(false)
    })

    expect(execCommandMock).toHaveBeenCalledWith('copy')
    expect(onError).toHaveBeenCalledTimes(1)
    expect(result.current.error).toBeInstanceOf(Error)

    act(() => {
      result.current.reset()
    })
    expect(result.current.copied).toBe(false)
    expect(result.current.error).toBeNull()
  })
})

describe('copyToClipboard', () => {
  it('returns true for modern clipboard success and false on failure', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })

    await expect(copyToClipboard('copy me')).resolves.toBe(true)

    writeText.mockRejectedValueOnce(new Error('denied'))
    await expect(copyToClipboard('copy me')).resolves.toBe(false)
  })

  it('uses execCommand when the clipboard API is unavailable', async () => {
    Object.assign(navigator, { clipboard: undefined })
    const execCommandMock = vi.mocked(document.execCommand).mockReturnValue(true)

    await expect(copyToClipboard('legacy')).resolves.toBe(true)
    expect(execCommandMock).toHaveBeenCalledWith('copy')
  })
})

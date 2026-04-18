import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { getPreferencesMock, updatePreferencesMock } = vi.hoisted(() => ({
  getPreferencesMock: vi.fn(),
  updatePreferencesMock: vi.fn(),
}))

vi.mock('../api/client', () => ({
  authApi: {
    getPreferences: getPreferencesMock,
    updatePreferences: updatePreferencesMock,
  },
}))

import {
  clearSyncedPreferences,
  getSyncedPreferences,
  useSettingsSync,
} from './useSettingsSync'

describe('useSettingsSync', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    window.localStorage.clear()
    getPreferencesMock.mockReset()
    updatePreferencesMock.mockReset()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('reads and clears synced preferences from localStorage safely', () => {
    window.localStorage.setItem(
      'recallhub_synced_preferences',
      JSON.stringify({ language: 'de', ui_density: 'compact' })
    )

    expect(getSyncedPreferences()).toEqual({ language: 'de', ui_density: 'compact' })

    window.localStorage.setItem('recallhub_synced_preferences', '{bad json')
    expect(getSyncedPreferences()).toBeNull()

    clearSyncedPreferences()
    expect(window.localStorage.getItem('recallhub_synced_preferences')).toBeNull()
    expect(window.localStorage.getItem('recallhub_preferences_version')).toBeNull()
  })

  it('pulls preferences from the backend, merges local cache, and falls back on errors', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    window.localStorage.setItem(
      'recallhub_synced_preferences',
      JSON.stringify({ language: 'en', ui_density: 'compact', default_model: 'cached' })
    )

    getPreferencesMock.mockResolvedValueOnce({
      language: 'de',
      ui_density: null,
      default_model: 'db-model',
      notifications_enabled: true,
    })

    const { result } = renderHook(() => useSettingsSync())

    await expect(result.current.pullFromDb()).resolves.toEqual({
      language: 'de',
      ui_density: 'compact',
      default_model: 'db-model',
      notifications_enabled: true,
    })

    getPreferencesMock.mockRejectedValueOnce(new Error('offline'))
    await expect(result.current.pullFromDb()).resolves.toEqual({
      language: 'de',
      ui_density: 'compact',
      default_model: 'db-model',
      notifications_enabled: true,
    })
    expect(warn).toHaveBeenCalledWith('Failed to pull preferences from DB', expect.any(Error))
  })

  it('syncs preferences immediately to cache and debounces backend writes', async () => {
    const { result } = renderHook(() => useSettingsSync())
    updatePreferencesMock.mockResolvedValue({
      preferences: { language: 'de', default_model: 'gpt-4.1' },
    })

    act(() => {
      result.current.syncPreference({ language: 'de' })
      result.current.syncPreference({ default_model: 'gpt-4.1' })
    })

    expect(getSyncedPreferences()).toEqual({
      language: 'de',
      default_model: 'gpt-4.1',
    })
    expect(window.localStorage.getItem('recallhub_preferences_version')).not.toBeNull()
    expect(updatePreferencesMock).not.toHaveBeenCalled()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })

    expect(updatePreferencesMock).toHaveBeenCalledWith({
      language: 'de',
      default_model: 'gpt-4.1',
    })
    expect(getSyncedPreferences()).toEqual({
      language: 'de',
      default_model: 'gpt-4.1',
    })
  })

  it('flushes pending updates and re-queues failed writes', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const { result } = renderHook(() => useSettingsSync())

    act(() => {
      result.current.syncPreference({ language: 'en' })
      result.current.syncPreference({ ui_density: 'spacious' })
    })

    updatePreferencesMock.mockRejectedValueOnce(new Error('db down'))
    await act(async () => {
      await result.current.flushToDb()
    })
    expect(warn).toHaveBeenCalledWith('Failed to sync preferences to DB', expect.any(Error))

    updatePreferencesMock.mockResolvedValueOnce({
      preferences: { language: 'en', ui_density: 'spacious' },
    })
    await act(async () => {
      await result.current.flushToDb()
    })

    expect(updatePreferencesMock).toHaveBeenLastCalledWith({
      language: 'en',
      ui_density: 'spacious',
    })
  })

  it('flushes pending updates on unmount', async () => {
    updatePreferencesMock.mockResolvedValue({ preferences: { language: 'de' } })
    const { result, unmount } = renderHook(() => useSettingsSync())

    act(() => {
      result.current.syncPreference({ language: 'de' })
    })

    await act(async () => {
      unmount()
    })

    expect(updatePreferencesMock).toHaveBeenCalledWith({ language: 'de' })
  })
})

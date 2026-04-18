import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const {
  pullFromDbMock,
  syncPreferenceMock,
  flushToDbMock,
  clearSyncedPreferencesMock,
} = vi.hoisted(() => ({
  pullFromDbMock: vi.fn(),
  syncPreferenceMock: vi.fn(),
  flushToDbMock: vi.fn(),
  clearSyncedPreferencesMock: vi.fn(),
}))

vi.mock('../hooks/useSettingsSync', () => ({
  useSettingsSync: () => ({
    pullFromDb: pullFromDbMock,
    syncPreference: syncPreferenceMock,
    flushToDb: flushToDbMock,
  }),
  clearSyncedPreferences: clearSyncedPreferencesMock,
}))

import {
  SETTINGS_SYNCED_EVENT,
  SettingsSyncProvider,
  useOptionalSettingsSync,
  useSettingsSyncContext,
} from './SettingsSyncContext'

function Consumer() {
  const context = useSettingsSyncContext()
  const optional = useOptionalSettingsSync()

  return (
    <div>
      <div data-testid="optional">{optional ? 'present' : 'missing'}</div>
      <button onClick={() => context.syncPreference({ language: 'de' })}>sync</button>
      <button onClick={() => void context.pullAndApply()}>pull</button>
      <button onClick={() => context.clearSync()}>clear</button>
    </div>
  )
}

describe('SettingsSyncContext', () => {
  beforeEach(() => {
    pullFromDbMock.mockReset()
    syncPreferenceMock.mockReset()
    flushToDbMock.mockReset()
    clearSyncedPreferencesMock.mockReset()
  })

  it('throws when the required hook is used outside the provider', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    expect(() => render(<Consumer />)).toThrow(
      'useSettingsSyncContext must be used within SettingsSyncProvider'
    )

    consoleError.mockRestore()
  })

  it('returns null from the optional hook outside the provider', () => {
    function OptionalConsumer() {
      return <div>{useOptionalSettingsSync() ? 'present' : 'missing'}</div>
    }

    render(<OptionalConsumer />)
    expect(screen.getByText('missing')).toBeInTheDocument()
  })

  it('pulls settings on login, dispatches sync events, and clears on logout', async () => {
    pullFromDbMock.mockResolvedValue({ language: 'de', ui_density: 'compact' })
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')

    const { rerender } = render(
      <SettingsSyncProvider isAuthenticated={false}>
        <Consumer />
      </SettingsSyncProvider>
    )

    expect(screen.getByTestId('optional')).toHaveTextContent('present')

    rerender(
      <SettingsSyncProvider isAuthenticated={true}>
        <Consumer />
      </SettingsSyncProvider>
    )

    await waitFor(() => {
      expect(pullFromDbMock).toHaveBeenCalledTimes(1)
    })
    expect(dispatchSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        type: SETTINGS_SYNCED_EVENT,
        detail: { language: 'de', ui_density: 'compact' },
      })
    )

    rerender(
      <SettingsSyncProvider isAuthenticated={false}>
        <Consumer />
      </SettingsSyncProvider>
    )

    expect(clearSyncedPreferencesMock).toHaveBeenCalledTimes(1)
  })

  it('exposes sync, pull, clear, and beforeunload flushing behavior', async () => {
    pullFromDbMock.mockResolvedValue({ language: 'en' })

    render(
      <SettingsSyncProvider isAuthenticated={true}>
        <Consumer />
      </SettingsSyncProvider>
    )

    fireEvent.click(screen.getByRole('button', { name: 'sync' }))
    expect(syncPreferenceMock).toHaveBeenCalledWith({ language: 'de' })

    fireEvent(window, new Event('beforeunload'))
    expect(flushToDbMock).toHaveBeenCalledTimes(1)

    const dispatchSpy = vi.spyOn(window, 'dispatchEvent')
    fireEvent.click(screen.getByRole('button', { name: 'pull' }))

    await waitFor(() => {
      expect(dispatchSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          type: SETTINGS_SYNCED_EVENT,
          detail: { language: 'en' },
        })
      )
    })

    fireEvent.click(screen.getByRole('button', { name: 'clear' }))
    expect(clearSyncedPreferencesMock).toHaveBeenCalled()
  })
})

import type { ReactNode } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const syncPreferenceMock = vi.fn()

vi.mock('../hooks/useLocalStorage', async () => {
  const React = await vi.importActual<typeof import('react')>('react')
  return {
    useLocalStorage: <T,>(_key: string, initialValue: T) => {
      const [value, setValue] = React.useState<T>(initialValue)
      const clearValue = () => setValue(initialValue)
      return [value, setValue, clearValue] as const
    },
  }
})

vi.mock('./SettingsSyncContext', () => ({
  SETTINGS_SYNCED_EVENT: 'settings:synced',
  useOptionalSettingsSync: () => ({
    syncPreference: syncPreferenceMock,
  }),
}))

import {
  SettingsNumber,
  SettingsSection,
  SettingsSelect,
  SettingsSlider,
  SettingsToggle,
  useUserPreferences,
  UserPreferencesProvider,
} from './UserPreferencesContext'

function Harness() {
  const {
    preferences,
    setPreference,
    setPreferences,
    resetPreferences,
    resetPreference,
    isDefault,
    applyFromSync,
  } = useUserPreferences()

  return (
    <div>
      <div data-testid="search-type">{preferences.defaultSearchType}</div>
      <div data-testid="items-per-page">{preferences.itemsPerPage}</div>
      <div data-testid="streaming">{String(preferences.streamingEnabled)}</div>
      <div data-testid="is-default">{String(isDefault('defaultSearchType'))}</div>
      <button onClick={() => setPreference('defaultSearchType', 'semantic')}>set one</button>
      <button
        onClick={() =>
          setPreferences({
            itemsPerPage: 50,
            streamingEnabled: false,
          })
        }
      >
        set many
      </button>
      <button onClick={() => resetPreference('itemsPerPage')}>reset one</button>
      <button onClick={() => resetPreferences()}>reset all</button>
      <button
        onClick={() =>
          applyFromSync({
            default_search_type: 'text',
            items_per_page: 15,
            streaming_enabled: false,
          })
        }
      >
        apply sync
      </button>
    </div>
  )
}

function ProviderWrapper({ children }: { children: ReactNode }) {
  return <UserPreferencesProvider>{children}</UserPreferencesProvider>
}

describe('UserPreferencesContext', () => {
  beforeEach(() => {
    syncPreferenceMock.mockReset()
  })

  it('throws when the hook is used outside the provider', () => {
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    expect(() => render(<Harness />)).toThrow(
      'useUserPreferences must be used within a UserPreferencesProvider'
    )

    consoleError.mockRestore()
  })

  it('updates, resets, and syncs preferences', () => {
    render(
      <ProviderWrapper>
        <Harness />
      </ProviderWrapper>
    )

    expect(screen.getByTestId('search-type')).toHaveTextContent('hybrid')
    expect(screen.getByTestId('is-default')).toHaveTextContent('true')

    fireEvent.click(screen.getByRole('button', { name: 'set one' }))
    expect(screen.getByTestId('search-type')).toHaveTextContent('semantic')
    expect(syncPreferenceMock).toHaveBeenCalledWith({ default_search_type: 'semantic' })
    expect(screen.getByTestId('is-default')).toHaveTextContent('false')

    fireEvent.click(screen.getByRole('button', { name: 'set many' }))
    expect(screen.getByTestId('items-per-page')).toHaveTextContent('50')
    expect(screen.getByTestId('streaming')).toHaveTextContent('false')
    expect(syncPreferenceMock).toHaveBeenCalledWith({
      items_per_page: 50,
      streaming_enabled: false,
    })

    fireEvent.click(screen.getByRole('button', { name: 'reset one' }))
    expect(screen.getByTestId('items-per-page')).toHaveTextContent('20')

    fireEvent.click(screen.getByRole('button', { name: 'reset all' }))
    expect(screen.getByTestId('search-type')).toHaveTextContent('hybrid')
    expect(screen.getByTestId('streaming')).toHaveTextContent('true')
  })

  it('applies synced preferences directly and listens for sync events', () => {
    render(
      <ProviderWrapper>
        <Harness />
      </ProviderWrapper>
    )

    fireEvent.click(screen.getByRole('button', { name: 'apply sync' }))
    expect(screen.getByTestId('search-type')).toHaveTextContent('text')
    expect(screen.getByTestId('items-per-page')).toHaveTextContent('15')

    act(() => {
      window.dispatchEvent(
        new CustomEvent('settings:synced', {
          detail: {
            default_search_type: 'semantic',
            items_per_page: 99,
            streaming_enabled: true,
          },
        })
      )
    })

    expect(screen.getByTestId('search-type')).toHaveTextContent('semantic')
    expect(screen.getByTestId('items-per-page')).toHaveTextContent('99')
    expect(syncPreferenceMock).not.toHaveBeenCalledWith(
      expect.objectContaining({ items_per_page: 99 })
    )
  })
})

describe('UserPreferences helper components', () => {
  it('renders settings section content', () => {
    render(
      <SettingsSection title="Preferences" description="Tune the app">
        <div>Inner content</div>
      </SettingsSection>
    )

    expect(screen.getByText('Preferences')).toBeInTheDocument()
    expect(screen.getByText('Tune the app')).toBeInTheDocument()
    expect(screen.getByText('Inner content')).toBeInTheDocument()
  })

  it('renders and toggles settings controls', () => {
    const onToggle = vi.fn()
    const onSelect = vi.fn()
    const onNumber = vi.fn()
    const onSlider = vi.fn()

    const { rerender } = render(
      <SettingsToggle
        label="Streaming"
        description="Stream responses"
        checked={false}
        onChange={onToggle}
      />
    )

    fireEvent.click(screen.getByRole('switch'))
    expect(onToggle).toHaveBeenCalledWith(true)

    rerender(
      <SettingsSelect
        label="Density"
        value="comfortable"
        options={[
          { value: 'compact', label: 'Compact' },
          { value: 'comfortable', label: 'Comfortable' },
        ]}
        onChange={onSelect}
      />
    )
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'compact' } })
    expect(onSelect).toHaveBeenCalledWith('compact')

    rerender(
      <SettingsNumber
        label="Items"
        value={20}
        min={10}
        max={100}
        suffix="rows"
        onChange={onNumber}
      />
    )
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '25' } })
    expect(onNumber).toHaveBeenCalledWith(25)

    rerender(
      <SettingsSlider
        label="Autosave"
        description="Seconds"
        value={5}
        min={1}
        max={10}
        valueFormatter={(value) => `${value}s`}
        onChange={onSlider}
      />
    )
    fireEvent.change(screen.getByRole('slider'), { target: { value: '7' } })
    expect(onSlider).toHaveBeenCalledWith(7)

    rerender(
      <SettingsSlider
        label="Autosave"
        description="Seconds"
        value={7}
        min={1}
        max={10}
        valueFormatter={(value) => `${value}s`}
        onChange={onSlider}
      />
    )
    expect(screen.getByText('7s')).toBeInTheDocument()
  })

  it('respects disabled control states', () => {
    render(
      <>
        <SettingsToggle label="Disabled toggle" checked onChange={vi.fn()} disabled />
        <SettingsSelect
          label="Disabled select"
          value="a"
          options={[{ value: 'a', label: 'A' }]}
          onChange={vi.fn()}
          disabled
        />
        <SettingsNumber label="Disabled number" value={1} onChange={vi.fn()} disabled />
      </>
    )

    expect(screen.getByRole('switch')).toBeDisabled()
    expect(screen.getByRole('combobox')).toBeDisabled()
    expect(screen.getByRole('spinbutton')).toBeDisabled()
  })
})

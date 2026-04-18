import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { vi } from 'vitest'

import { LanguageProvider, useLanguage, useLocalizedPath } from './LanguageContext'

const changeLanguageMock = vi.fn()
const syncPreferenceMock = vi.fn()

vi.mock('react-i18next', async () => {
  const actual = await vi.importActual<typeof import('react-i18next')>('react-i18next')
  return {
    ...actual,
    useTranslation: () => ({
      i18n: {
        language: 'en',
        changeLanguage: changeLanguageMock,
      },
    }),
  }
})

vi.mock('./SettingsSyncContext', () => ({
  SETTINGS_SYNCED_EVENT: 'settings:synced',
  useOptionalSettingsSync: () => ({
    syncPreference: syncPreferenceMock,
  }),
}))

function LocationDisplay() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname}</div>
}

function LanguageHarness() {
  const { language, setLanguage, applyLanguage } = useLanguage()
  const localizedPath = useLocalizedPath()

  return (
    <div>
      <div>{language}</div>
      <div>{localizedPath('/search')}</div>
      <button onClick={() => setLanguage('de')}>set de</button>
      <button onClick={() => applyLanguage('en')}>apply en</button>
    </div>
  )
}

describe('LanguageContext', () => {
  beforeEach(() => {
    changeLanguageMock.mockReset()
    syncPreferenceMock.mockReset()
    window.localStorage.clear()
  })

  it('reads the language from the route and localizes paths', () => {
    render(
      <MemoryRouter initialEntries={['/de/chat']}>
        <Routes>
          <Route
            path="/:lang/*"
            element={
              <LanguageProvider>
                <LanguageHarness />
                <LocationDisplay />
              </LanguageProvider>
            }
          />
        </Routes>
      </MemoryRouter>
    )

    expect(screen.getByText('de')).toBeInTheDocument()
    expect(screen.getByText('/de/search')).toBeInTheDocument()
  })

  it('updates i18n, local storage, navigation, and synced preferences when setting the language', async () => {
    render(
      <MemoryRouter initialEntries={['/en/search?tab=all#results']}>
        <Routes>
          <Route
            path="/:lang/*"
            element={
              <LanguageProvider>
                <LanguageHarness />
                <LocationDisplay />
              </LanguageProvider>
            }
          />
        </Routes>
      </MemoryRouter>
    )

    fireEvent.click(screen.getByRole('button', { name: 'set de' }))

    await waitFor(() => {
      expect(changeLanguageMock).toHaveBeenCalledWith('de')
    })
    expect(window.localStorage.getItem('i18nextLng')).toBe('de')
    expect(syncPreferenceMock).toHaveBeenCalledWith({ language: 'de' })
    expect(screen.getByTestId('location')).toHaveTextContent('/de/search')
  })

  it('applies synced preferences from the settings event without writing back to sync', async () => {
    render(
      <MemoryRouter initialEntries={['/en']}>
        <Routes>
          <Route
            path="/:lang"
            element={
              <LanguageProvider>
                <LanguageHarness />
                <LocationDisplay />
              </LanguageProvider>
            }
          />
        </Routes>
      </MemoryRouter>
    )

    act(() => {
      window.dispatchEvent(new CustomEvent('settings:synced', { detail: { language: 'de' } }))
    })

    await waitFor(() => {
      expect(changeLanguageMock).toHaveBeenCalledWith('de')
    })
    expect(syncPreferenceMock).not.toHaveBeenCalled()
    await waitFor(() => {
      expect(screen.getByTestId('location')).toHaveTextContent('/de')
    })
  })
})

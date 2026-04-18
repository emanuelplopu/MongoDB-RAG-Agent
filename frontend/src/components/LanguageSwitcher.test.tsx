import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const languageState = {
  language: 'en' as 'en' | 'de',
  setLanguage: vi.fn(),
  supportedLanguages: ['en', 'de'] as Array<'en' | 'de'>,
}

vi.mock('@heroicons/react/24/outline', () => ({
  GlobeAltIcon: (props: { className?: string }) => <svg data-testid="GlobeAltIcon" {...props} />,
}))

vi.mock('../contexts/LanguageContext', () => ({
  useLanguage: () => languageState,
}))

vi.mock('../i18n', () => ({
  languageNames: {
    en: 'English',
    de: 'Deutsch',
  },
}))

import LanguageSwitcher from './LanguageSwitcher'

describe('LanguageSwitcher', () => {
  beforeEach(() => {
    languageState.language = 'en'
    languageState.setLanguage.mockReset()
    languageState.supportedLanguages = ['en', 'de']
  })

  it('renders the active language and opens the dropdown', () => {
    render(<LanguageSwitcher compact={false} />)

    expect(screen.getByLabelText('Change language')).toHaveAttribute('title', 'English')
    expect(screen.getByText('EN')).toBeInTheDocument()
    expect(screen.getByTestId('GlobeAltIcon')).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('Change language'))

    expect(screen.getByText('English')).toBeInTheDocument()
    expect(screen.getByText('Deutsch')).toBeInTheDocument()
  })

  it('changes language and closes for outside clicks', () => {
    render(<LanguageSwitcher />)

    fireEvent.click(screen.getByLabelText('Change language'))
    fireEvent.click(screen.getByText('Deutsch'))
    expect(languageState.setLanguage).toHaveBeenCalledWith('de')

    fireEvent.click(screen.getByLabelText('Change language'))
    expect(screen.getByText('English')).toBeInTheDocument()

    fireEvent.mouseDown(document.body)
    expect(screen.queryByText('English')).not.toBeInTheDocument()
  })
})

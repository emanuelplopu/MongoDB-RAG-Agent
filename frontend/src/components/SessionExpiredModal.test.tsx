import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const navigate = vi.fn()

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}))

vi.mock('./LocalizedLink', () => ({
  useLocalizedNavigate: () => navigate,
}))

import SessionExpiredModal from './SessionExpiredModal'

describe('SessionExpiredModal', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    navigate.mockReset()
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      callback(0)
      return 1
    })
  })

  it('stays hidden when closed', () => {
    render(<SessionExpiredModal isOpen={false} onClose={vi.fn()} onContinueAsGuest={vi.fn()} />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('animates out after closing', () => {
    const { rerender } = render(
      <SessionExpiredModal isOpen={true} onClose={vi.fn()} onContinueAsGuest={vi.fn()} />
    )

    expect(screen.getByRole('dialog')).toBeInTheDocument()

    rerender(<SessionExpiredModal isOpen={false} onClose={vi.fn()} onContinueAsGuest={vi.fn()} />)
    expect(screen.getByRole('dialog')).toBeInTheDocument()

    act(() => {
      vi.advanceTimersByTime(250)
    })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('logs in again, shows loading state, and ignores backdrop clicks', () => {
    const onClose = vi.fn()
    const onContinueAsGuest = vi.fn()
    const { container } = render(
      <SessionExpiredModal
        isOpen={true}
        onClose={onClose}
        onContinueAsGuest={onContinueAsGuest}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Log In Again' }))

    expect(onClose).toHaveBeenCalledTimes(1)
    expect(navigate).toHaveBeenCalledWith('/login')
    expect(screen.getByRole('button', { name: 'Redirecting...' })).toBeDisabled()

    const backdrop = container.querySelector('div.absolute.inset-0')
    expect(backdrop).not.toBeNull()
    fireEvent.click(backdrop!)
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(onContinueAsGuest).not.toHaveBeenCalled()
  })

  it('continues as guest and closes the modal', () => {
    const onClose = vi.fn()
    const onContinueAsGuest = vi.fn()

    render(
      <SessionExpiredModal
        isOpen={true}
        onClose={onClose}
        onContinueAsGuest={onContinueAsGuest}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Continue as Guest' }))

    expect(onContinueAsGuest).toHaveBeenCalledTimes(1)
    expect(onClose).toHaveBeenCalledTimes(1)
    expect(navigate).not.toHaveBeenCalled()
    expect(screen.getByText('errors.sessionExpired.message')).toBeInTheDocument()
  })
})

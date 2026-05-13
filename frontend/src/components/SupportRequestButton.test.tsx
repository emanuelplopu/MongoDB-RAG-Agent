import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor, waitForElementToBeRemoved } from '@testing-library/react'
import SupportRequestButton from './SupportRequestButton'

const tMock = (key: string) => key

const successToastMock = vi.fn()
const errorToastMock = vi.fn()
const getSessionDiagnosticMock = vi.fn()
const submitSupportRequestMock = vi.fn()

let authState: {
  user: { is_admin: boolean } | null
}

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: tMock,
  }),
}))

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => authState,
}))

vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({
    success: successToastMock,
    error: errorToastMock,
  }),
}))

vi.mock('../api/client', () => ({
  supportApi: {
    getSessionDiagnostic: (...args: unknown[]) => getSessionDiagnosticMock(...args),
    submitSupportRequest: (...args: unknown[]) => submitSupportRequestMock(...args),
  },
}))

describe('SupportRequestButton', () => {
  beforeEach(() => {
    authState = {
      user: { is_admin: false },
    }
    successToastMock.mockReset()
    errorToastMock.mockReset()
    getSessionDiagnosticMock.mockReset()
    submitSupportRequestMock.mockReset()

    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      callback(0)
      return 1
    })
  })

  it('does not render for guests or admin users', () => {
    authState = { user: null }
    const { rerender } = render(<SupportRequestButton />)
    expect(screen.queryByRole('button', { name: 'support.requestSupport' })).not.toBeInTheDocument()

    authState = { user: { is_admin: true } }
    rerender(<SupportRequestButton />)
    expect(screen.queryByRole('button', { name: 'support.requestSupport' })).not.toBeInTheDocument()
  })

  it('opens a general support request and submits it successfully', async () => {
    submitSupportRequestMock.mockResolvedValue({ success: true })

    render(<SupportRequestButton />)

    fireEvent.click(screen.getByRole('button', { name: 'support.requestSupport' }))

    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(screen.getByDisplayValue('No active session. General support request.')).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('support.descriptionPlaceholder'), {
      target: { value: 'Need help with a missing answer.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'support.submit' }))

    await waitFor(() =>
      expect(submitSupportRequestMock).toHaveBeenCalledWith({
        diagnostic_text: 'No active session. General support request.',
        session_id: undefined,
        user_description: 'Need help with a missing answer.',
      })
    )
    expect(successToastMock).toHaveBeenCalledWith('support.success')
    await waitForElementToBeRemoved(() => screen.queryByRole('dialog'))
  })

  it('loads diagnostics for a session and shows an error toast when submission fails', async () => {
    getSessionDiagnosticMock.mockResolvedValue({ diagnostic: 'session diagnostic payload' })
    submitSupportRequestMock.mockRejectedValue(new Error('submit failed'))

    render(<SupportRequestButton sessionId="session-42" />)

    fireEvent.click(screen.getByRole('button', { name: 'support.requestSupport' }))

    expect(await screen.findByDisplayValue('session diagnostic payload')).toBeInTheDocument()
    fireEvent.change(screen.getByPlaceholderText('support.descriptionPlaceholder'), {
      target: { value: 'Please investigate the last response.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'support.submit' }))

    await waitFor(() =>
      expect(submitSupportRequestMock).toHaveBeenCalledWith({
        diagnostic_text: 'session diagnostic payload',
        session_id: 'session-42',
        user_description: 'Please investigate the last response.',
      })
    )
    expect(errorToastMock).toHaveBeenCalledWith('support.error')
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'

import CopyButton, { CopyCodeButton, CopyIconButton } from './CopyButton'
import { ToastProvider } from '../contexts/ToastContext'

describe('CopyButton', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    Object.defineProperty(document, 'execCommand', {
      value: vi.fn(),
      configurable: true,
      writable: true,
    })
  })

  afterEach(() => {
    cleanup()
    try {
      vi.runOnlyPendingTimers()
    } catch {
      // Some tests temporarily switch back to real timers for async UI updates.
    }
    vi.useRealTimers()
  })

  it('copies text and shows a success toast', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })

    render(
      <ToastProvider>
        <CopyButton
          text="copied text"
          label="copy text"
          iconOnly={false}
          variant="outline"
          size="md"
          successMessage="Copied successfully"
        />
      </ToastProvider>
    )

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'copy text' }))
    })

    expect(writeText).toHaveBeenCalledWith('copied text')
    expect(screen.getByText('Copied!')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Copied successfully')
  })

  it('shows an error toast when copy fails', async () => {
    vi.useRealTimers()
    const writeText = vi.fn().mockRejectedValue(new Error('blocked'))
    Object.assign(navigator, { clipboard: { writeText } })

    render(
      <ToastProvider>
        <CopyButton text="copied text" label="copy text" />
      </ToastProvider>
    )

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'copy text' }))
    })

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent('Failed to copy')
    })
  })
})

describe('CopyIconButton', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    cleanup()
    try {
      vi.runOnlyPendingTimers()
    } catch {
      // Some tests temporarily switch back to real timers for async UI updates.
    }
    vi.useRealTimers()
  })

  it('uses navigator.clipboard when available', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })

    const { container } = render(<CopyIconButton text="icon text" size="sm" />)
    const button = container.querySelector('button')

    expect(button).not.toBeNull()

    await act(async () => {
      fireEvent.click(button!)
    })

    expect(writeText).toHaveBeenCalledWith('icon text')
    expect(button).toHaveAttribute('title', 'Copied!')
  })

  it('falls back to execCommand when clipboard access fails', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('denied'))
    Object.assign(navigator, { clipboard: { writeText } })
    const execCommandMock = vi.mocked(document.execCommand).mockReturnValue(true)

    const { container } = render(<CopyIconButton text="icon text" />)
    const button = container.querySelector('button')

    expect(button).not.toBeNull()

    await act(async () => {
      fireEvent.click(button!)
    })

    expect(execCommandMock).toHaveBeenCalledWith('copy')
  })
})

describe('CopyCodeButton', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    cleanup()
    try {
      vi.runOnlyPendingTimers()
    } catch {
      // Some tests temporarily switch back to real timers for async UI updates.
    }
    vi.useRealTimers()
  })

  it('copies code and updates the button label', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })

    render(<CopyCodeButton code={'const answer = 42;'} />)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Copy' }))
    })

    expect(writeText).toHaveBeenCalledWith('const answer = 42;')
    expect(screen.getByRole('button')).toHaveTextContent('Copied')
  })

  it('uses the fallback path when the clipboard API rejects', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('denied'))
    Object.assign(navigator, { clipboard: { writeText } })
    const execCommandMock = vi.mocked(document.execCommand).mockReturnValue(true)

    render(<CopyCodeButton code={'const answer = 42;'} />)

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Copy' }))
    })

    expect(execCommandMock).toHaveBeenCalledWith('copy')
  })
})

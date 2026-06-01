import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import MarkdownRenderer from './MarkdownRenderer'

function LocationProbe() {
  const location = useLocation()
  return <div data-testid="location">{location.pathname}</div>
}

describe('MarkdownRenderer', () => {
  beforeEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockResolvedValue(undefined) },
      configurable: true,
    })
    Object.defineProperty(document, 'execCommand', {
      value: vi.fn(),
      configurable: true,
      writable: true,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders rich markdown and supports copy and internal navigation', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/start']}>
        <LocationProbe />
        <MarkdownRenderer
          content={[
            'Paragraph with `inlineCode` and [internal](/documents/doc-1) plus [external](https://example.com).',
            '',
            '```ts',
            'const value = 1',
            '```',
            '',
            '- one',
            '- two',
            '',
            '1. first',
            '2. second',
            '',
            '> quoted',
            '',
            '| A | B |',
            '| - | - |',
            '| 1 | 2 |',
          ].join('\n')}
        />
      </MemoryRouter>
    )

    await user.click(screen.getByText('inlineCode'))
    expect(screen.getByTitle('Copied!')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Copy/ }))
    expect(screen.getByRole('button', { name: /Copied/ })).toBeInTheDocument()

    await user.click(screen.getByRole('link', { name: 'internal' }))
    expect(screen.getByTestId('location')).toHaveTextContent('/documents/doc-1')

    expect(screen.getByRole('link', { name: /external/ })).toHaveAttribute('target', '_blank')
    expect(screen.getByText('quoted')).toBeInTheDocument()
  })

  it('falls back when clipboard writes fail', async () => {
    const user = userEvent.setup()
    Object.defineProperty(navigator, 'clipboard', {
      value: undefined,
      configurable: true,
    })

    render(
      <MemoryRouter>
        <MarkdownRenderer content={'Use `fallback`.\n\n```text\nplain block\n```'} />
      </MemoryRouter>
    )

    await user.click(screen.getByText('fallback'))
    await waitFor(() => expect(document.execCommand).toHaveBeenCalledWith('copy'))

    await user.click(screen.getByRole('button', { name: /Copy/ }))
    await waitFor(() => expect(document.execCommand).toHaveBeenCalledWith('copy'))
  })
})

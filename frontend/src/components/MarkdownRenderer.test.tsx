import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@heroicons/react/24/outline', () => {
  const makeIcon = (name: string) => (props: { className?: string }) => <svg data-testid={name} {...props} />
  return {
    ClipboardIcon: makeIcon('ClipboardIcon'),
    ClipboardDocumentCheckIcon: makeIcon('ClipboardDocumentCheckIcon'),
  }
})

import MarkdownRenderer from './MarkdownRenderer'

describe('MarkdownRenderer', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    })
    Object.defineProperty(document, 'execCommand', {
      value: vi.fn().mockReturnValue(true),
      configurable: true,
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('renders block code, links, and rich markdown wrappers', async () => {
    render(
      <MemoryRouter>
      <MarkdownRenderer
        className="custom-markdown"
        content={[
          '```ts',
          'const answer = 42;',
          '```',
          '',
          'Visit [Docs](https://example.com).',
          '',
          '> quote',
          '',
          '- item',
        ].join('\n')}
      />
      </MemoryRouter>
    )

    expect(document.querySelector('.custom-markdown')).toBeInTheDocument()
    expect(screen.getByText('ts')).toBeInTheDocument()
    expect(screen.getByText('const answer = 42;')).toBeInTheDocument()

    const copyButton = screen.getByRole('button', { name: /copy/i })
    await act(async () => {
      fireEvent.click(copyButton)
    })
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('const answer = 42;')
    expect(screen.getByRole('button', { name: /copied/i })).toBeInTheDocument()

    await act(async () => {
      vi.advanceTimersByTime(2000)
    })
    expect(screen.getByRole('button', { name: /copy/i })).toBeInTheDocument()

    const link = screen.getByRole('link', { name: /docs/i })
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(screen.getByText('quote').closest('blockquote')).toHaveClass('border-l-4')
    expect(screen.getByText('item').closest('ul')).toHaveClass('list-disc')
  })

  it('copies inline code and falls back to execCommand when clipboard write fails', async () => {
    const execCommand = vi.mocked(document.execCommand)
    vi.mocked(navigator.clipboard.writeText).mockRejectedValueOnce(new Error('denied'))

    render(<MemoryRouter><MarkdownRenderer content="Use `npm test` now." /></MemoryRouter>)

    const inlineCode = screen.getByText('npm test')
    await act(async () => {
      fireEvent.click(inlineCode)
    })

    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(screen.getByText(/npm test/)).toHaveAttribute('title', 'Copied!')

    await act(async () => {
      vi.advanceTimersByTime(1500)
    })
    expect(screen.getByText(/npm test/)).toHaveAttribute('title', 'Click to copy')
  })
})

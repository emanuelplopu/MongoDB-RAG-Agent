import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import StreamingIndicator, {
  CompletionIndicator,
  ProcessingPhase,
  StreamingDot,
  TypingIndicator,
} from './StreamingIndicator'

describe('StreamingIndicator', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.spyOn(Date, 'now').mockReturnValue(1000)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders nothing when streaming is inactive', () => {
    const { container } = render(<StreamingIndicator isStreaming={false} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the phase, pulse, and elapsed time while streaming', () => {
    const nowSpy = vi.spyOn(Date, 'now')
    nowSpy.mockReturnValue(1000)

    render(<StreamingIndicator isStreaming phase="Analyzing" size="lg" className="stream-shell" />)

    expect(screen.getByText('Analyzing')).toBeInTheDocument()
    expect(screen.getByText('0s')).toBeInTheDocument()

    act(() => {
      nowSpy.mockReturnValue(4000)
      vi.advanceTimersByTime(3000)
    })

    expect(screen.getByText('3s')).toBeInTheDocument()
    expect(screen.getByText('Analyzing').parentElement).toHaveClass('stream-shell')
  })

  it('supports pulse-less and timer-less display variants', () => {
    const { container } = render(
      <StreamingIndicator
        isStreaming
        phase="Planning"
        showElapsedTime={false}
        showPulse={false}
        size="sm"
      />
    )

    expect(screen.getByText('Planning')).toBeInTheDocument()
    expect(screen.queryByText('0s')).not.toBeInTheDocument()
    expect(container.querySelector('.animate-ping')).toBeNull()
  })
})

describe('StreamingIndicator helpers', () => {
  it('renders streaming dot only when active', () => {
    const { rerender } = render(<StreamingDot isStreaming={false} label="Live" />)
    expect(screen.queryByText('Live')).not.toBeInTheDocument()

    rerender(<StreamingDot isStreaming label="Live" className="dot-shell" />)
    expect(screen.getByText('Live')).toBeInTheDocument()
    expect(screen.getByText('Live').parentElement).toHaveClass('dot-shell')
  })

  it('renders typing, processing, and completion indicators', () => {
    const { container, rerender } = render(<TypingIndicator className="typing-shell" />)
    expect(container.querySelectorAll('.typing-dot')).toHaveLength(3)

    rerender(<ProcessingPhase phase="Searching" className="phase-shell" />)
    expect(screen.getByText('Searching')).toBeInTheDocument()
    expect(screen.getByText('Searching').parentElement).toHaveClass('phase-shell')

    rerender(<ProcessingPhase phase="Custom" icon={<span>Icon</span>} />)
    expect(screen.getByText('Icon')).toBeInTheDocument()

    rerender(<CompletionIndicator message="Finished" className="complete-shell" />)
    expect(screen.getByText('Finished')).toBeInTheDocument()
    expect(screen.getByText('Finished').parentElement).toHaveClass('complete-shell')
  })
})

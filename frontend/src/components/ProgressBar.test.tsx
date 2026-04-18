import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import ProgressBar, {
  CircularProgress,
  OperationStatus,
  StepProgress,
} from './ProgressBar'

describe('ProgressBar', () => {
  it('renders determinate progress with labels and clamped width', () => {
    const { container, rerender } = render(
      <ProgressBar
        value={120}
        size="lg"
        variant="success"
        showLabel
        label="Upload"
        className="progress-shell"
      />
    )

    expect(screen.getByText('Upload')).toBeInTheDocument()
    expect(screen.getByText('120%')).toBeInTheDocument()
    expect(container.firstChild).toHaveClass('progress-shell')
    expect(container.querySelector('.bg-green-500')).toHaveStyle({ width: '100%' })

    rerender(<ProgressBar value={-10} animated={false} variant="error" />)
    expect(container.querySelector('.bg-red-500')).toHaveStyle({ width: '0%' })
  })

  it('renders indeterminate progress', () => {
    const { container } = render(<ProgressBar variant="warning" size="xs" />)
    expect(container.querySelector('.animate-progress-indeterminate')).not.toBeNull()
  })
})

describe('CircularProgress', () => {
  it('renders determinate and indeterminate circular progress', () => {
    const { container, rerender } = render(
      <CircularProgress value={75} size={60} strokeWidth={6} showLabel variant="primary" />
    )

    expect(screen.getByText('75%')).toBeInTheDocument()
    expect(container.querySelector('svg')).not.toHaveClass('animate-spin')

    rerender(<CircularProgress variant="warning" />)
    expect(container.querySelector('svg')).toHaveClass('animate-spin')
  })
})

describe('StepProgress', () => {
  it('renders step states and completed indicators', () => {
    const { container } = render(
      <StepProgress steps={['One', 'Two', 'Three']} currentStep={1} className="steps-shell" />
    )

    expect(screen.getByText('One')).toBeInTheDocument()
    expect(screen.getByText('Two')).toBeInTheDocument()
    expect(screen.getByText('Three')).toBeInTheDocument()
    expect(container.querySelector('.steps-shell')).not.toBeNull()
    expect(container.querySelectorAll('svg').length).toBeGreaterThan(0)
  })
})

describe('OperationStatus', () => {
  it('renders loading, success, error, and idle states', () => {
    const { container, rerender } = render(
      <OperationStatus status="loading" message="Working" className="status-shell" />
    )

    expect(screen.getByText('Working')).toBeInTheDocument()
    expect(container.querySelector('.status-shell')).not.toBeNull()

    rerender(<OperationStatus status="success" message="Done" />)
    expect(screen.getByText('Done')).toBeInTheDocument()

    rerender(<OperationStatus status="error" message="Failed" />)
    expect(screen.getByText('Failed')).toBeInTheDocument()

    rerender(<OperationStatus status="idle" />)
    expect(container).toBeEmptyDOMElement()
  })
})

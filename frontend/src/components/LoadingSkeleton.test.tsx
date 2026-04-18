import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import Skeleton, {
  SkeletonAvatar,
  SkeletonCard,
  SkeletonDocumentList,
  SkeletonMessage,
  SkeletonSearchResult,
  SkeletonSidebar,
  SkeletonTableRow,
  SkeletonText,
} from './LoadingSkeleton'

describe('LoadingSkeleton primitives', () => {
  it('renders the base skeleton with style and variant options', () => {
    const { container, rerender } = render(
      <Skeleton width={120} height="2rem" variant="circular" className="base-skeleton" />
    )

    let skeleton = container.firstChild as HTMLElement
    expect(skeleton.style.width).toBe('120px')
    expect(skeleton.style.height).toBe('2rem')
    expect(skeleton.className).toContain('rounded-full')
    expect(skeleton.className).toContain('animate-shimmer')

    rerender(<Skeleton variant="rectangular" animate={false} />)
    skeleton = container.firstChild as HTMLElement
    expect(skeleton.className).toContain('rounded-none')
    expect(skeleton.className).not.toContain('animate-shimmer')
  })

  it('renders text and avatar skeleton helpers', () => {
    const { container, rerender } = render(<SkeletonText lines={3} lastLineWidth="35%" />)

    expect(container.querySelectorAll('div.bg-gray-200')).toHaveLength(3)

    rerender(<SkeletonAvatar size={48} className="avatar-shell" />)
    const avatar = container.firstChild as HTMLElement
    expect(avatar.style.width).toBe('48px')
    expect(avatar.className).toContain('avatar-shell')
  })
})

describe('LoadingSkeleton composed components', () => {
  it('renders card, message, and search result skeletons', () => {
    const { container, rerender } = render(<SkeletonCard className="card-shell" />)

    expect(container.querySelector('.card-shell')).not.toBeNull()

    rerender(<SkeletonMessage isUser className="message-shell" />)
    expect(container.querySelector('.message-shell')).not.toBeNull()
    expect(container.textContent).toBe('')

    rerender(<SkeletonSearchResult className="search-shell" />)
    expect(container.querySelector('.search-shell')).not.toBeNull()
  })

  it('renders table, document list, and sidebar skeleton structures', () => {
    const { container, rerender } = render(
      <table>
        <tbody>
          <SkeletonTableRow columns={3} className="row-shell" />
        </tbody>
      </table>
    )

    expect(container.querySelectorAll('td')).toHaveLength(3)

    rerender(<SkeletonDocumentList count={4} className="docs-shell" />)
    expect(container.querySelector('.docs-shell')).not.toBeNull()

    rerender(<SkeletonSidebar className="sidebar-shell" />)
    expect(container.querySelector('.sidebar-shell')).not.toBeNull()
    expect(container.querySelectorAll('.sidebar-shell .p-2')).toHaveLength(6)
  })
})

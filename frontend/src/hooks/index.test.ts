import { describe, expect, it } from 'vitest'

import { copyToClipboard, useClipboard } from './index'
import {
  copyToClipboard as directCopyToClipboard,
  useClipboard as directUseClipboard,
} from './useClipboard'

describe('hooks index', () => {
  it('re-exports clipboard helpers', () => {
    expect(useClipboard).toBe(directUseClipboard)
    expect(copyToClipboard).toBe(directCopyToClipboard)
  })
})

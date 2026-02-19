/**
 * CopyButton - Reusable copy-to-clipboard button with visual feedback
 * Shows a checkmark when copy is successful
 */

import { useState, useCallback } from 'react'
import { ClipboardIcon, ClipboardDocumentCheckIcon } from '@heroicons/react/24/outline'
import { useClipboard } from '../hooks/useClipboard'
import { useToast } from '../contexts/ToastContext'

interface CopyButtonProps {
  /** Text to copy to clipboard */
  text: string
  /** Optional label for accessibility */
  label?: string
  /** Size variant */
  size?: 'xs' | 'sm' | 'md'
  /** Whether to show toast notification */
  showToast?: boolean
  /** Toast message on success */
  successMessage?: string
  /** Additional class names */
  className?: string
  /** Button variant style */
  variant?: 'ghost' | 'outline' | 'filled'
  /** Icon only (no tooltip) */
  iconOnly?: boolean
}

export default function CopyButton({
  text,
  label = 'Copy to clipboard',
  size = 'sm',
  showToast = true,
  successMessage = 'Copied to clipboard',
  className = '',
  variant = 'ghost',
  iconOnly = true,
}: CopyButtonProps) {
  const toast = useToast()
  const { copied, copy } = useClipboard({
    timeout: 2000,
    onSuccess: () => {
      if (showToast) {
        toast.success(successMessage)
      }
    },
    onError: () => {
      if (showToast) {
        toast.error('Failed to copy')
      }
    },
  })

  const handleCopy = useCallback(async (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    await copy(text)
  }, [copy, text])

  // Size classes
  const sizeClasses = {
    xs: 'p-1',
    sm: 'p-1.5',
    md: 'p-2',
  }

  const iconSizes = {
    xs: 'h-3 w-3',
    sm: 'h-4 w-4',
    md: 'h-5 w-5',
  }

  // Variant classes
  const variantClasses = {
    ghost: 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200',
    outline: 'border border-gray-200 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 text-gray-500 dark:text-gray-400',
    filled: 'bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-600 dark:text-gray-300',
  }

  return (
    <button
      onClick={handleCopy}
      className={`
        inline-flex items-center justify-center rounded-lg transition-all duration-150
        ${sizeClasses[size]}
        ${variantClasses[variant]}
        ${copied ? 'text-green-500 dark:text-green-400' : ''}
        ${className}
      `}
      title={copied ? 'Copied!' : label}
      aria-label={label}
    >
      {copied ? (
        <ClipboardDocumentCheckIcon className={`${iconSizes[size]} text-green-500`} />
      ) : (
        <ClipboardIcon className={iconSizes[size]} />
      )}
      {!iconOnly && (
        <span className="ml-1.5 text-xs">
          {copied ? 'Copied!' : 'Copy'}
        </span>
      )}
    </button>
  )
}

/**
 * CopyIconButton - Minimal icon-only version for inline use
 */
export function CopyIconButton({ 
  text, 
  size = 'xs',
  className = '' 
}: { 
  text: string
  size?: 'xs' | 'sm'
  className?: string
}) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Fallback
      const textArea = document.createElement('textarea')
      textArea.value = text
      textArea.style.position = 'fixed'
      textArea.style.left = '-9999px'
      document.body.appendChild(textArea)
      textArea.select()
      document.execCommand('copy')
      document.body.removeChild(textArea)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }, [text])

  const iconSize = size === 'xs' ? 'h-3 w-3' : 'h-4 w-4'
  const padding = size === 'xs' ? 'p-0.5' : 'p-1'

  return (
    <button
      onClick={handleCopy}
      className={`
        inline-flex items-center justify-center rounded transition-all
        hover:bg-gray-200 dark:hover:bg-gray-600
        ${padding}
        ${copied ? 'text-green-500' : 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'}
        ${className}
      `}
      title={copied ? 'Copied!' : 'Copy'}
    >
      {copied ? (
        <ClipboardDocumentCheckIcon className={iconSize} />
      ) : (
        <ClipboardIcon className={iconSize} />
      )}
    </button>
  )
}

/**
 * CopyCodeButton - Positioned button for code blocks
 */
export function CopyCodeButton({ code, className = '' }: { code: string; className?: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Fallback
      const textArea = document.createElement('textarea')
      textArea.value = code
      textArea.style.position = 'fixed'
      textArea.style.left = '-9999px'
      document.body.appendChild(textArea)
      textArea.select()
      document.execCommand('copy')
      document.body.removeChild(textArea)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    }
  }, [code])

  return (
    <button
      onClick={handleCopy}
      className={`
        absolute top-2 right-2 
        px-2 py-1 rounded-md text-xs font-medium
        transition-all duration-150
        ${copied 
          ? 'bg-green-500/20 text-green-400 border border-green-500/30' 
          : 'bg-gray-700/50 text-gray-300 hover:bg-gray-600/50 hover:text-white border border-gray-600/50'
        }
        ${className}
      `}
      title={copied ? 'Copied!' : 'Copy code'}
    >
      {copied ? '✓ Copied' : 'Copy'}
    </button>
  )
}

/**
 * Hooks index export
 */
export { useClipboard } from '../hooks/useClipboard'

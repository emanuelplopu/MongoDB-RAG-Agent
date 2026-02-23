/**
 * MarkdownRenderer - Custom markdown renderer with copy buttons for code blocks
 * Provides a consistent markdown rendering experience with QoL features
 */

import { ReactNode, useState, useCallback } from 'react'
import ReactMarkdown from 'react-markdown'
import { ClipboardIcon, ClipboardDocumentCheckIcon } from '@heroicons/react/24/outline'

interface MarkdownRendererProps {
  content: string
  className?: string
  darkMode?: boolean
}

// Code block with copy button
function CodeBlock({ 
  language, 
  code,
}: { 
  language: string
  code: string
}) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Fallback for older browsers
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
    <div className="relative group my-2 max-w-full">
      {/* Language badge and copy button */}
      <div className="absolute top-0 left-0 right-0 flex items-center justify-between px-3 py-1.5 bg-gray-800 dark:bg-gray-900 rounded-t-lg border-b border-gray-700">
        <span className="text-xs font-mono text-gray-400">{language || 'text'}</span>
        <button
          onClick={handleCopy}
          className={`
            flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium
            transition-all duration-150
            ${copied 
              ? 'bg-green-500/20 text-green-400' 
              : 'bg-gray-700 text-gray-300 hover:bg-gray-600 hover:text-white opacity-0 group-hover:opacity-100'
            }
          `}
          title={copied ? 'Copied!' : 'Copy code'}
        >
          {copied ? (
            <>
              <ClipboardDocumentCheckIcon className="h-3.5 w-3.5" />
              Copied
            </>
          ) : (
            <>
              <ClipboardIcon className="h-3.5 w-3.5" />
              Copy
            </>
          )}
        </button>
      </div>
      
      {/* Code content */}
      <pre className="bg-gray-900 dark:bg-gray-950 rounded-lg pt-10 pb-4 px-4 overflow-x-auto text-sm max-w-full">
        <code className="text-gray-100 font-mono whitespace-pre">
          {code}
        </code>
      </pre>
    </div>
  )
}

// Inline code with copy on click
function InlineCode({ children }: { children: ReactNode }) {
  const [copied, setCopied] = useState(false)
  const text = String(children)

  const handleCopy = useCallback(async () => {
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

  return (
    <code
      onClick={handleCopy}
      className={`
        px-1.5 py-0.5 rounded text-sm font-mono cursor-pointer transition-colors
        ${copied 
          ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-300' 
          : 'bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-200 hover:bg-gray-200 dark:hover:bg-gray-600'
        }
      `}
      title={copied ? 'Copied!' : 'Click to copy'}
    >
      {copied ? '✓ ' : ''}{children}
    </code>
  )
}

export default function MarkdownRenderer({ 
  content, 
  className = '',
}: MarkdownRendererProps) {
  return (
    <ReactMarkdown
      className={`prose prose-sm max-w-none dark:prose-invert overflow-x-auto ${className}`}
      components={{
        // Code blocks with copy button
        code({ className, children }) {
          const match = /language-(\w+)/.exec(className || '')
          const language = match ? match[1] : ''
          const code = String(children).replace(/\n$/, '')
          
          // Check if it's a code block (has newlines or language specified)
          const isCodeBlock = code.includes('\n') || match
          
          if (isCodeBlock) {
            return <CodeBlock language={language} code={code} />
          }
          
          // Inline code
          return <InlineCode>{children}</InlineCode>
        },
        // Wrap pre to avoid default styling conflicts
        pre({ children }) {
          return <>{children}</>
        },
        // Custom link handling
        a({ node, children, href, ...props }) {
          const isExternal = href?.startsWith('http')
          return (
            <a
              href={href}
              target={isExternal ? '_blank' : undefined}
              rel={isExternal ? 'noopener noreferrer' : undefined}
              className="text-primary-600 dark:text-primary-400 hover:underline"
              {...props}
            >
              {children}
              {isExternal && <span className="text-xs ml-0.5">↗</span>}
            </a>
          )
        },
        // Custom paragraph with better spacing
        p({ node, children, ...props }) {
          return (
            <p className="mb-2 last:mb-0" {...props}>
              {children}
            </p>
          )
        },
        // Custom list styling
        ul({ node, children, ...props }) {
          return (
            <ul className="list-disc pl-4 mb-2 space-y-1" {...props}>
              {children}
            </ul>
          )
        },
        ol({ node, children, ...props }) {
          return (
            <ol className="list-decimal pl-4 mb-2 space-y-1" {...props}>
              {children}
            </ol>
          )
        },
        // Custom blockquote
        blockquote({ node, children, ...props }) {
          return (
            <blockquote 
              className="border-l-4 border-primary-300 dark:border-primary-700 pl-4 italic text-gray-600 dark:text-gray-400 my-2"
              {...props}
            >
              {children}
            </blockquote>
          )
        },
        // Custom table
        table({ node, children, ...props }) {
          return (
            <div className="overflow-x-auto my-2">
              <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700" {...props}>
                {children}
              </table>
            </div>
          )
        },
        th({ node, children, ...props }) {
          return (
            <th 
              className="px-3 py-2 text-left text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wider bg-gray-50 dark:bg-gray-800"
              {...props}
            >
              {children}
            </th>
          )
        },
        td({ node, children, ...props }) {
          return (
            <td 
              className="px-3 py-2 text-sm text-gray-900 dark:text-gray-100"
              {...props}
            >
              {children}
            </td>
          )
        },
      }}
    >
      {content}
    </ReactMarkdown>
  )
}

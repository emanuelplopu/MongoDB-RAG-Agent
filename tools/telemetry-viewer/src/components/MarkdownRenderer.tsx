import React, { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface MarkdownRendererProps {
  content: string
  maxLines?: number  // If set, collapse after N lines
  collapsible?: boolean
  className?: string
}

export function MarkdownRenderer({ content, maxLines, collapsible = true, className = '' }: MarkdownRendererProps) {
  const [expanded, setExpanded] = useState(false)

  if (!content) {
    return <div className={`text-gray-500 text-sm italic ${className}`}>No content</div>
  }

  const lines = content.split('\n')
  const shouldCollapse = collapsible && maxLines && lines.length > maxLines
  const displayContent = shouldCollapse && !expanded
    ? lines.slice(0, maxLines).join('\n') + '\n...'
    : content

  return (
    <div className={`markdown-content ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Custom renderers for dark theme
          code({ node, className: codeClassName, children, ...props }) {
            const isInline = !codeClassName
            if (isInline) {
              return (
                <code className="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-surface-lighter text-accent-orange text-sm font-mono" {...props}>
                  {children}
                </code>
              )
            }
            return (
              <pre className="bg-gray-100 dark:bg-surface rounded-lg p-4 overflow-x-auto my-2">
                <code className={`${codeClassName} text-sm font-mono text-gray-800 dark:text-gray-200`} {...props}>
                  {children}
                </code>
              </pre>
            )
          },
          p({ children }) {
            return <p className="mb-2 text-gray-800 dark:text-gray-200 leading-relaxed">{children}</p>
          },
          h1({ children }) {
            return <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100 mb-3 mt-4">{children}</h1>
          },
          h2({ children }) {
            return <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-2 mt-3">{children}</h2>
          },
          h3({ children }) {
            return <h3 className="text-base font-semibold text-gray-800 dark:text-gray-200 mb-2 mt-2">{children}</h3>
          },
          ul({ children }) {
            return <ul className="list-disc list-inside mb-2 text-gray-800 dark:text-gray-200 space-y-1">{children}</ul>
          },
          ol({ children }) {
            return <ol className="list-decimal list-inside mb-2 text-gray-800 dark:text-gray-200 space-y-1">{children}</ol>
          },
          li({ children }) {
            return <li className="text-gray-800 dark:text-gray-200">{children}</li>
          },
          blockquote({ children }) {
            return (
              <blockquote className="border-l-4 border-accent-blue/50 pl-4 my-2 text-gray-600 dark:text-gray-300 italic">
                {children}
              </blockquote>
            )
          },
          table({ children }) {
            return (
              <div className="overflow-x-auto my-2">
                <table className="min-w-full border border-gray-200 dark:border-gray-700 rounded">{children}</table>
              </div>
            )
          },
          th({ children }) {
            return <th className="px-3 py-2 bg-gray-100 dark:bg-surface-lighter text-left text-sm font-medium text-gray-700 dark:text-gray-300 border-b border-gray-200 dark:border-gray-700">{children}</th>
          },
          td({ children }) {
            return <td className="px-3 py-2 text-sm text-gray-800 dark:text-gray-200 border-b border-gray-200 dark:border-gray-700/50">{children}</td>
          },
          a({ href, children }) {
            return <a href={href} className="text-accent-blue hover:underline" target="_blank" rel="noopener">{children}</a>
          },
          strong({ children }) {
            return <strong className="font-semibold text-gray-900 dark:text-gray-100">{children}</strong>
          },
        }}
      >
        {displayContent}
      </ReactMarkdown>

      {shouldCollapse && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="mt-1 text-xs text-accent-blue hover:text-accent-blue/80 transition-colors"
        >
          {expanded ? '▲ Show less' : `▼ Show all (${lines.length} lines)`}
        </button>
      )}
    </div>
  )
}

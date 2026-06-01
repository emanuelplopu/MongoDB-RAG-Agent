import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { LLMCallRecord } from '../types/strategy'

interface LLMCallViewerProps {
  calls: LLMCallRecord[]
  loading?: boolean
}

function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—'
  return n.toLocaleString()
}

function formatLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function formatCost(cost: number | null | undefined): string {
  if (cost === null || cost === undefined || Number.isNaN(cost)) return '—'
  if (cost < 0.0001) return `€${cost.toExponential(2)}`
  return `€${cost.toFixed(4)}`
}

function approxTokens(text: string): number {
  // Rough heuristic: ~4 chars per token
  return Math.ceil(text.length / 4)
}

interface CopyablePromptBlockProps {
  label: string
  content: string
}

function CopyablePromptBlock({ label, content }: CopyablePromptBlockProps) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      setCopied(false)
    }
  }

  const charCount = content?.length || 0
  const tokenEstimate = approxTokens(content || '')

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[11px] uppercase tracking-wide text-gray-400">
          {label}
          <span className="ml-2 text-[10px] text-gray-500 normal-case tracking-normal">
            ({charCount.toLocaleString()} {t('strategy.llm.chars', { defaultValue: 'chars' })}, ~
            {tokenEstimate.toLocaleString()} {t('strategy.llm.tokens', { defaultValue: 'tokens' })})
          </span>
        </div>
        <button
          onClick={handleCopy}
          className="text-[10px] px-2 py-0.5 rounded border border-gray-700/50 text-gray-300 bg-surface hover:bg-surface-lighter transition-colors"
        >
          {copied
            ? t('strategy.llm.copied', { defaultValue: 'Copied!' })
            : t('strategy.llm.copy', { defaultValue: 'Copy' })}
        </button>
      </div>
      <pre className="bg-surface border border-gray-700/50 rounded-md p-2 max-h-96 overflow-y-auto text-xs font-mono text-gray-100 whitespace-pre-wrap break-words leading-relaxed">
        {content || (
          <span className="text-gray-500 italic">
            {t('strategy.llm.empty', { defaultValue: '(empty)' })}
          </span>
        )}
      </pre>
    </div>
  )
}

interface LLMCallRowProps {
  call: LLMCallRecord
  index: number
}

function LLMCallRow({ call, index }: LLMCallRowProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)

  const success = call.success !== false
  const statusBadge = success
    ? 'bg-accent-green/20 text-accent-green border-accent-green/30'
    : 'bg-red-500/20 text-red-400 border-red-500/30'

  return (
    <div className="border border-gray-700/50 rounded-md bg-surface overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-surface-lighter transition-colors"
      >
        <span className="text-[10px] text-gray-500 font-mono w-6 flex-shrink-0">
          {String(index + 1).padStart(2, '0')}
        </span>
        <span
          className={`text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded border ${statusBadge}`}
        >
          {success ? 'ok' : 'err'}
        </span>
        <span className="text-xs text-gray-100 font-mono truncate flex-shrink-0" title={call.model}>
          {call.model || '—'}
        </span>
        <span className="text-[11px] text-gray-400 truncate" title={call.node_id}>
          {call.node_type || '—'}
          <span className="text-gray-500"> · {call.node_id || '—'}</span>
        </span>
        <span className="ml-auto flex items-center gap-3 flex-shrink-0 text-[11px] font-mono text-gray-300">
          <span title="latency">{formatLatency(call.latency_ms)}</span>
          <span title="total_tokens" className="text-gray-400">
            {formatNumber(call.total_tokens)} tok
          </span>
          {call.cost_eur !== null && call.cost_eur !== undefined && (
            <span title="cost_eur" className="text-gray-400">
              {formatCost(call.cost_eur)}
            </span>
          )}
          <span className="text-gray-500">{open ? '▲' : '▼'}</span>
        </span>
      </button>

      {open && (
        <div className="bg-surface-light border-t border-gray-700/50 p-3 space-y-3">
          {/* Metadata grid */}
          <div className="grid grid-cols-2 md:grid-cols-3 gap-x-4 gap-y-1 text-[11px]">
            <MetaField label="model" value={call.model} mono />
            <MetaField label="provider" value={call.provider} />
            <MetaField label="model_role" value={call.model_role || '—'} />
            <MetaField label="temperature" value={call.temperature?.toString() ?? '—'} mono />
            <MetaField label="max_tokens" value={formatNumber(call.max_tokens)} mono />
            <MetaField label="latency_ms" value={formatLatency(call.latency_ms)} mono />
            <MetaField label="prompt_tokens" value={formatNumber(call.prompt_tokens)} mono />
            <MetaField
              label="completion_tokens"
              value={formatNumber(call.completion_tokens)}
              mono
            />
            <MetaField label="total_tokens" value={formatNumber(call.total_tokens)} mono />
          </div>

          {!success && call.error && (
            <div className="text-[11px] text-red-400 bg-red-500/10 border border-red-500/30 rounded p-2 font-mono whitespace-pre-wrap break-words">
              {call.error}
            </div>
          )}

          <CopyablePromptBlock
            label={t('strategy.llm.systemPrompt', { defaultValue: 'System Prompt' })}
            content={call.system_prompt || ''}
          />
          <CopyablePromptBlock
            label={t('strategy.llm.userPrompt', { defaultValue: 'User Prompt' })}
            content={call.user_prompt || ''}
          />
          <CopyablePromptBlock
            label={t('strategy.llm.assistantResponse', { defaultValue: 'Assistant Response' })}
            content={call.assistant_response || ''}
          />
        </div>
      )}
    </div>
  )
}

function MetaField({
  label,
  value,
  mono = false,
}: {
  label: string
  value: React.ReactNode
  mono?: boolean
}) {
  return (
    <div className="flex gap-2">
      <span className="text-gray-500 w-28 flex-shrink-0">{label}</span>
      <span
        className={`${mono ? 'font-mono ' : ''}text-gray-200 break-all`}
      >
        {value}
      </span>
    </div>
  )
}

export default function LLMCallViewer({ calls, loading = false }: LLMCallViewerProps) {
  const { t } = useTranslation()

  if (loading) {
    return (
      <div className="space-y-2">
        {[0, 1, 2].map(i => (
          <div
            key={i}
            className="h-9 rounded-md bg-surface border border-gray-700/50 animate-pulse"
          />
        ))}
        <div className="text-center text-[11px] text-gray-500 pt-1">
          <span className="animate-spin inline-block mr-1">⏳</span>
          {t('strategy.llm.loading', { defaultValue: 'Loading LLM calls…' })}
        </div>
      </div>
    )
  }

  if (!calls || calls.length === 0) {
    return (
      <div className="text-[11px] text-gray-500 italic py-4 text-center">
        {t('strategy.llm.noCalls', { defaultValue: 'No LLM calls recorded' })}
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {calls.map((call, i) => (
        <LLMCallRow key={call.call_id || `${call.trace_id}-${i}`} call={call} index={i} />
      ))}
    </div>
  )
}

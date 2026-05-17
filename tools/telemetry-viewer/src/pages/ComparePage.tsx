import { useState, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useDataSource } from '../api'
import { TelemetryRecord } from '../types/telemetry'
import { PIIHighlighter, countPIIEntities, stripPIIMarkers, hasPIIMarkers } from '../components/PIIHighlighter'
import { MarkdownRenderer } from '../components/MarkdownRenderer'
import { MonospaceBlock } from '../components/MonospaceBlock'
import { Collapsible } from '../components/Collapsible'
import { CompareView } from '../components/CompareView'
import { TimelineWaterfall } from '../components/TimelineWaterfall'

type CompareMode = 'pii' | 'records'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTime(ts: string): string {
  try {
    const d = new Date(ts)
    return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return ts
  }
}

function truncate(text: string, max: number): string {
  if (!text) return '(empty)'
  const clean = text.replace(/\n/g, ' ').trim()
  return clean.length > max ? clean.slice(0, max) + '...' : clean
}

function formatDuration(ms: number | undefined): string {
  if (ms === undefined || ms === null) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function recordLabel(r: TelemetryRecord): string {
  return `${formatTime(r.timestamp)} — ${truncate(r.prompt_pseudonymized, 40)}`
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function DeltaBadge({ label, left, right, unit = 'ms', lowerIsBetter = true }: {
  label: string; left: number; right: number; unit?: string; lowerIsBetter?: boolean
}) {
  const delta = right - left
  const isGood = lowerIsBetter ? delta < 0 : delta > 0
  const color = delta === 0 ? 'text-gray-400' : isGood ? 'text-green-400' : 'text-red-400'
  const sign = delta > 0 ? '+' : ''

  return (
    <div className="flex flex-col items-center p-3 bg-gray-100 dark:bg-surface rounded-lg">
      <span className="text-[10px] text-gray-500 uppercase">{label}</span>
      <div className="flex items-baseline gap-2 mt-1">
        <span className="text-sm text-gray-700 dark:text-gray-300">{left}{unit}</span>
        <span className="text-xs text-gray-500">vs</span>
        <span className="text-sm text-gray-700 dark:text-gray-300">{right}{unit}</span>
      </div>
      <span className={`text-xs font-mono mt-1 ${color}`}>{sign}{delta}{unit}</span>
    </div>
  )
}

const PII_PILL_COLORS: Record<string, string> = {
  PERSON: 'bg-blue-500/20 text-blue-300 border-blue-500/40',
  EMAIL: 'bg-purple-500/20 text-purple-300 border-purple-500/40',
  PHONE: 'bg-green-500/20 text-green-300 border-green-500/40',
  ADDRESS: 'bg-orange-500/20 text-orange-300 border-orange-500/40',
  COMPANY: 'bg-teal-500/20 text-teal-300 border-teal-500/40',
  IBAN: 'bg-red-500/20 text-red-300 border-red-500/40',
}

function PIISummaryBar({ texts }: { texts: string[] }) {
  const { t } = useTranslation()
  const counts = useMemo(() => {
    const merged: Record<string, number> = {}
    for (const txt of texts) {
      const c = countPIIEntities(txt)
      for (const [k, v] of Object.entries(c)) {
        merged[k] = (merged[k] || 0) + v
      }
    }
    return merged
  }, [texts])

  const entries = Object.entries(counts)
  if (entries.length === 0) {
    return (
      <div className="text-xs text-gray-500 italic py-2 text-center">{t('compare.pii.noPii')}</div>
    )
  }

  return (
    <div className="flex flex-wrap gap-2 justify-center py-2">
      {entries.map(([type, count]) => {
        const colors = PII_PILL_COLORS[type] || 'bg-gray-500/20 text-gray-300 border-gray-500/40'
        return (
          <span
            key={type}
            className={`inline-flex items-center gap-1 px-2 py-1 rounded-full border text-xs font-mono ${colors}`}
          >
            <span className="font-semibold">{count}</span>
            <span className="capitalize">{type.toLowerCase()}{count !== 1 ? 's' : ''}</span>
          </span>
        )
      })}
    </div>
  )
}

function RecordSelector({
  label,
  value,
  records,
  onChange,
}: {
  label: string
  value: string
  records: TelemetryRecord[]
  onChange: (id: string) => void
}) {
  const { t } = useTranslation()
  return (
    <div className="flex items-center gap-2 min-w-0 flex-1">
      <span className="text-xs text-gray-400 whitespace-nowrap">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="flex-1 min-w-0 bg-white dark:bg-surface border border-gray-200 dark:border-gray-700/50 rounded-lg px-3 py-1.5 text-sm text-gray-800 dark:text-gray-200 focus:outline-none focus:border-accent-blue/50 truncate"
      >
        <option value="">{t('compare.selectRecord')}</option>
        {records.map((r) => (
          <option key={r.record_id} value={r.record_id}>
            {recordLabel(r)}
          </option>
        ))}
      </select>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Content renderers (PII-aware)
// ---------------------------------------------------------------------------

function RenderText({ text, highlighted }: { text: string; highlighted: boolean }) {
  if (!text) return <span className="text-gray-500 italic text-sm">No content</span>
  if (highlighted && hasPIIMarkers(text)) {
    return <PIIHighlighter text={text} />
  }
  return <MarkdownRenderer content={highlighted ? text : stripPIIMarkers(text)} />
}

// ---------------------------------------------------------------------------
// Records comparison content
// ---------------------------------------------------------------------------

function RecordsComparison({ left: l, right: r }: { left: TelemetryRecord; right: TelemetryRecord }) {
  const { t } = useTranslation()
  const totalTokensL = (l.llm_calls || []).reduce((s, c) => s + (c.total_tokens || 0), 0)
  const totalTokensR = (r.llm_calls || []).reduce((s, c) => s + (c.total_tokens || 0), 0)
  const modelsDiffer = l.orchestrator_model !== r.orchestrator_model || l.worker_model !== r.worker_model

  return (
    <div className="space-y-4">
      {/* Metric deltas */}
      <div className="flex flex-wrap gap-3 justify-center">
        <DeltaBadge label={t('compare.metrics.latency')} left={l.total_duration_ms} right={r.total_duration_ms} unit="ms" lowerIsBetter />
        <DeltaBadge label={t('compare.metrics.tokens')} left={totalTokensL} right={totalTokensR} unit=" tok" lowerIsBetter />
        <div className="flex flex-col items-center p-3 bg-gray-100 dark:bg-surface rounded-lg">
          <span className="text-[10px] text-gray-500 uppercase">{t('compare.metrics.model')}</span>
          <div className="flex items-baseline gap-2 mt-1">
            <span className="text-xs text-gray-700 dark:text-gray-300 font-mono truncate max-w-[120px]" title={l.orchestrator_model}>{l.orchestrator_model}</span>
            <span className="text-xs text-gray-500">vs</span>
            <span className="text-xs text-gray-700 dark:text-gray-300 font-mono truncate max-w-[120px]" title={r.orchestrator_model}>{r.orchestrator_model}</span>
          </div>
          {modelsDiffer && (
            <span className="text-[10px] text-accent-orange mt-1">{t('compare.metrics.differentModels')}</span>
          )}
        </div>
      </div>

      {/* Prompts */}
      <Collapsible
        title={t('compare.section.prompts')}
        badge={l.prompt_pseudonymized !== r.prompt_pseudonymized ? 'differ' : 'same'}
        defaultOpen
      >
        {l.prompt_pseudonymized !== r.prompt_pseudonymized && (
          <div className="mb-3 px-2 py-1 rounded bg-accent-orange/10 border border-accent-orange/30 text-accent-orange text-xs text-center">
            {t('compare.promptsDiffer')}
          </div>
        )}
        <CompareView
          leftLabel={t('compare.leftRecord')}
          rightLabel={t('compare.rightRecord')}
          left={<RenderText text={l.prompt_pseudonymized} highlighted />}
          right={<RenderText text={r.prompt_pseudonymized} highlighted />}
        />
      </Collapsible>

      {/* Responses */}
      <Collapsible title={t('compare.section.responses')} defaultOpen>
        <CompareView
          leftLabel={t('compare.leftResponse')}
          rightLabel={t('compare.rightResponse')}
          left={<RenderText text={l.response_pseudonymized} highlighted />}
          right={<RenderText text={r.response_pseudonymized} highlighted />}
        />
      </Collapsible>

      {/* LLM Calls */}
      <Collapsible
        title={t('compare.section.llmCalls')}
        badge={`${l.llm_calls?.length ?? 0} vs ${r.llm_calls?.length ?? 0}`}
      >
        <LLMCallsComparison leftCalls={l.llm_calls || []} rightCalls={r.llm_calls || []} />
      </Collapsible>

      {/* Timing */}
      <Collapsible title={t('compare.section.timing')}>
        <CompareView
          leftLabel={`${t('compare.left')} — ${formatDuration(l.total_duration_ms)}`}
          rightLabel={`${t('compare.right')} — ${formatDuration(r.total_duration_ms)}`}
          left={<TimelineWaterfall phases={l.phase_metrics} totalDuration={l.total_duration_ms} />}
          right={<TimelineWaterfall phases={r.phase_metrics} totalDuration={r.total_duration_ms} />}
        />
      </Collapsible>
    </div>
  )
}

function LLMCallsComparison({
  leftCalls,
  rightCalls,
}: {
  leftCalls: TelemetryRecord['llm_calls']
  rightCalls: TelemetryRecord['llm_calls']
}) {
  const { t } = useTranslation()
  const maxLen = Math.max(leftCalls.length, rightCalls.length)

  if (maxLen === 0) {
    return <p className="text-sm text-gray-500 italic">{t('compare.noLlmCalls')}</p>
  }

  return (
    <div className="space-y-3">
      {Array.from({ length: maxLen }).map((_, i) => {
        const lc = leftCalls[i]
        const rc = rightCalls[i]
        const title = [
          lc ? `${lc.phase} (${lc.latency_ms}ms)` : '—',
          rc ? `${rc.phase} (${rc.latency_ms}ms)` : '—',
        ].join(' vs ')

        return (
          <Collapsible key={i} title={`Call ${i + 1}: ${title}`}>
            <div className="space-y-3">
              {lc && rc && (
                <div className="flex gap-3 justify-center">
                  <DeltaBadge label={t('compare.metrics.latency')} left={lc.latency_ms} right={rc.latency_ms} unit="ms" lowerIsBetter />
                  <DeltaBadge label={t('compare.metrics.tokens')} left={lc.total_tokens} right={rc.total_tokens} unit=" tok" lowerIsBetter />
                </div>
              )}
              <CompareView
                leftLabel={lc ? `${lc.phase} — ${lc.model}` : 'N/A'}
                rightLabel={rc ? `${rc.phase} — ${rc.model}` : 'N/A'}
                left={
                  lc ? (
                    <div className="space-y-2">
                      <MonospaceBlock content={lc.prompt_text} label={t('browser.detail.promptSent')} maxLines={10} />
                      <div>
                        <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">{t('browser.detail.response')}</div>
                        <MarkdownRenderer content={lc.response_text} maxLines={15} />
                      </div>
                    </div>
                  ) : (
                    <span className="text-gray-500 italic text-sm">{t('browser.detail.noCorrespondingCall')}</span>
                  )
                }
                right={
                  rc ? (
                    <div className="space-y-2">
                      <MonospaceBlock content={rc.prompt_text} label={t('browser.detail.promptSent')} maxLines={10} />
                      <div>
                        <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">{t('browser.detail.response')}</div>
                        <MarkdownRenderer content={rc.response_text} maxLines={15} />
                      </div>
                    </div>
                  ) : (
                    <span className="text-gray-500 italic text-sm">{t('browser.detail.noCorrespondingCall')}</span>
                  )
                }
              />
            </div>
          </Collapsible>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// PII comparison content
// ---------------------------------------------------------------------------

function PIIComparison({ record }: { record: TelemetryRecord }) {
  const { t } = useTranslation()
  const piiTexts = useMemo(
    () => [
      record.prompt_pseudonymized,
      record.response_pseudonymized,
      ...(record.llm_calls || []).flatMap((c) => [c.prompt_text, c.response_text]),
    ],
    [record],
  )

  const chunksWithPII = useMemo(
    () =>
      (record.search_operations || []).flatMap((op) =>
        (op.chunks_returned || []).filter((ch) => hasPIIMarkers(ch)),
      ),
    [record],
  )

  return (
    <div className="space-y-4">
      {/* PII summary */}
      <div className="bg-gray-50 dark:bg-surface-light rounded-lg px-4 py-2 border border-gray-200 dark:border-gray-700/50">
        <div className="text-[10px] uppercase tracking-wider text-gray-500 text-center mb-1">{t('compare.pii.summary')}</div>
        <PIISummaryBar texts={piiTexts} />
      </div>

      {/* Prompt */}
      <Collapsible title={t('browser.detail.prompt')} defaultOpen>
        <CompareView
          leftLabel={t('compare.pii.protected')}
          rightLabel={t('compare.pii.raw')}
          left={<RenderText text={record.prompt_pseudonymized} highlighted />}
          right={<RenderText text={record.prompt_pseudonymized} highlighted={false} />}
        />
      </Collapsible>

      {/* Response */}
      <Collapsible title={t('browser.detail.response')} defaultOpen>
        <CompareView
          leftLabel={t('compare.protected')}
          rightLabel={t('compare.raw')}
          left={<RenderText text={record.response_pseudonymized} highlighted />}
          right={<RenderText text={record.response_pseudonymized} highlighted={false} />}
        />
      </Collapsible>

      {/* LLM Calls */}
      {(record.llm_calls?.length ?? 0) > 0 && (
        <Collapsible title={t('compare.section.llmCalls')} badge={record.llm_calls.length}>
          <div className="space-y-3">
            {record.llm_calls.map((call, i) => (
              <Collapsible key={call.call_id || i} title={`${call.phase} — ${call.model}`} badge={`${call.latency_ms}ms`}>
                <div className="space-y-3">
                  <div className="text-[10px] uppercase tracking-wider text-gray-500">{t('browser.detail.promptSent')}</div>
                  <CompareView
                    leftLabel={t('compare.protected')}
                    rightLabel={t('compare.raw')}
                    left={<RenderText text={call.prompt_text} highlighted />}
                    right={<RenderText text={call.prompt_text} highlighted={false} />}
                  />
                  <div className="text-[10px] uppercase tracking-wider text-gray-500 mt-2">{t('browser.detail.response')}</div>
                  <CompareView
                    leftLabel={t('compare.protected')}
                    rightLabel={t('compare.raw')}
                    left={<RenderText text={call.response_text} highlighted />}
                    right={<RenderText text={call.response_text} highlighted={false} />}
                  />
                </div>
              </Collapsible>
            ))}
          </div>
        </Collapsible>
      )}

      {/* Search chunks with PII */}
      {chunksWithPII.length > 0 && (
        <Collapsible title={t('compare.section.searchChunks')} badge={chunksWithPII.length}>
          <div className="space-y-3">
            {chunksWithPII.map((chunk, i) => (
              <Collapsible key={i} title={`Chunk ${i + 1}`}>
                <CompareView
                  leftLabel={t('compare.protected')}
                  rightLabel={t('compare.raw')}
                  left={<PIIHighlighter text={chunk} />}
                  right={<span className="text-sm text-gray-200 whitespace-pre-wrap">{stripPIIMarkers(chunk)}</span>}
                />
              </Collapsible>
            ))}
          </div>
        </Collapsible>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function ComparePage() {
  const { state } = useDataSource()
  const { t } = useTranslation()
  const [mode, setMode] = useState<CompareMode>('records')
  const [leftId, setLeftId] = useState<string>('')
  const [rightId, setRightId] = useState<string>('')
  const [piiRecordId, setPiiRecordId] = useState<string>('')

  const sortedRecords = useMemo(
    () => [...state.records].sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()),
    [state.records],
  )

  const leftRecord = useMemo(() => sortedRecords.find((r) => r.record_id === leftId), [sortedRecords, leftId])
  const rightRecord = useMemo(() => sortedRecords.find((r) => r.record_id === rightId), [sortedRecords, rightId])
  const piiRecord = useMemo(() => sortedRecords.find((r) => r.record_id === piiRecordId), [sortedRecords, piiRecordId])

  // Empty state: no data
  if (state.records.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-gray-500 dark:text-gray-400">
        <div className="text-center">
          <p className="text-2xl mb-2">⚖️ {t('compare.title')}</p>
          <p>{t('compare.empty')}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Top bar */}
      <div className="flex-shrink-0 border-b border-gray-200 dark:border-gray-700/50 bg-gray-50 dark:bg-surface-light px-4 py-3 space-y-3">
        {/* Mode toggle */}
        <div className="flex items-center gap-2">
          <button
            onClick={() => setMode('pii')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              mode === 'pii'
                ? 'bg-accent-purple/20 text-accent-purple border border-accent-purple/40'
                : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 border border-gray-200 dark:border-gray-700/50 hover:border-gray-300 dark:hover:border-gray-600'
            }`}
          >
            {t('compare.mode.pii')}
          </button>
          <button
            onClick={() => setMode('records')}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              mode === 'records'
                ? 'bg-accent-blue/20 text-accent-blue border border-accent-blue/40'
                : 'text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 border border-gray-200 dark:border-gray-700/50 hover:border-gray-300 dark:hover:border-gray-600'
            }`}
          >
            {t('compare.mode.records')}
          </button>
        </div>

        {/* Selectors */}
        <div className="flex gap-4">
          {mode === 'records' ? (
            <>
              <RecordSelector label={`${t('compare.left')}:`} value={leftId} records={sortedRecords} onChange={setLeftId} />
              <RecordSelector label={`${t('compare.right')}:`} value={rightId} records={sortedRecords} onChange={setRightId} />
            </>
          ) : (
            <RecordSelector label="Record:" value={piiRecordId} records={sortedRecords} onChange={setPiiRecordId} />
          )}
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 overflow-y-auto p-4">
        {mode === 'records' ? (
          leftRecord && rightRecord ? (
            <RecordsComparison left={leftRecord} right={rightRecord} />
          ) : (
            <div className="h-full flex items-center justify-center text-gray-500 dark:text-gray-400">
              <div className="text-center">
                <p className="text-lg mb-1">⚖️</p>
                <p>{t('compare.selectRecords')}</p>
              </div>
            </div>
          )
        ) : piiRecord ? (
          <PIIComparison record={piiRecord} />
        ) : (
          <div className="h-full flex items-center justify-center text-gray-500 dark:text-gray-400">
            <div className="text-center">
              <p className="text-lg mb-1">🔒</p>
              <p>{t('compare.selectPiiRecord')}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

import React from 'react'
import { useTranslation } from 'react-i18next'
import { TelemetryRecord } from '../types/telemetry'
import { Collapsible } from './Collapsible'
import { MonospaceBlock } from './MonospaceBlock'
import { MarkdownRenderer } from './MarkdownRenderer'
import { PIIHighlighter, hasPIIMarkers } from './PIIHighlighter'
import { TimelineWaterfall } from './TimelineWaterfall'

interface RecordDetailProps {
  record: TelemetryRecord
}

function MetaItem({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-0.5">{label}</div>
      <div className="text-sm text-gray-800 dark:text-gray-200 font-mono break-all">{value || '—'}</div>
    </div>
  )
}

function TokenBadge({ count }: { count: number | undefined }) {
  if (!count) return null
  return (
    <span className="px-1.5 py-0.5 text-[10px] rounded-full bg-accent-purple/20 text-accent-purple font-mono">
      {count} tokens
    </span>
  )
}

function PhaseBadge({ phase }: { phase: string }) {
  const colors: Record<string, string> = {
    analyze: 'bg-accent-blue/20 text-accent-blue',
    plan: 'bg-accent-purple/20 text-accent-purple',
    evaluate: 'bg-accent-green/20 text-accent-green',
    synthesize: 'bg-accent-orange/20 text-accent-orange',
    search: 'bg-accent-teal/20 text-accent-teal',
    summarize: 'bg-accent-yellow/20 text-accent-yellow',
    refine: 'bg-pink-400/20 text-pink-400',
  }
  const c = colors[phase] || 'bg-gray-500/20 text-gray-400'
  return (
    <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono ${c}`}>
      {phase}
    </span>
  )
}

function formatTimestamp(ts: string): string {
  try {
    return new Date(ts).toLocaleString()
  } catch {
    return ts
  }
}

function formatDuration(ms: number | undefined): string {
  if (ms === undefined || ms === null) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

export function RecordDetail({ record }: RecordDetailProps) {
  const { t } = useTranslation()
  return (
    <div className="p-4 space-y-3">
      {/* Metadata */}
      <Collapsible title={t('browser.detail.metadata')} defaultOpen>
        <div className="grid grid-cols-3 gap-x-4 gap-y-3">
          <MetaItem label="Timestamp" value={formatTimestamp(record.timestamp)} />
          <MetaItem label="Session ID" value={record.session_id} />
          <MetaItem label="User ID" value={record.user_id} />
          <MetaItem label="Tenant" value={record.tenant} />
          <MetaItem label="Agent Mode" value={record.agent_mode} />
          <MetaItem label="Language" value={record.language} />
          <MetaItem
            label="Orchestrator Model"
            value={
              <span>
                {record.orchestrator_model}
                {record.orchestrator_provider && (
                  <span className="text-gray-500 text-[10px] ml-1">({record.orchestrator_provider})</span>
                )}
              </span>
            }
          />
          <MetaItem
            label="Worker Model"
            value={
              <span>
                {record.worker_model}
                {record.worker_provider && (
                  <span className="text-gray-500 text-[10px] ml-1">({record.worker_provider})</span>
                )}
              </span>
            }
          />
          <MetaItem label="Strategy" value={record.agent_strategy} />
          <MetaItem label="Total Duration" value={formatDuration(record.total_duration_ms)} />
          <MetaItem label="Orchestrator Duration" value={formatDuration(record.orchestrator_duration_ms)} />
          <MetaItem label="Worker Duration" value={formatDuration(record.worker_duration_ms)} />
        </div>
      </Collapsible>

      {/* Prompt */}
      <Collapsible
        title={t('browser.detail.prompt')}
        badge={record.prompt_tokens ? `${record.prompt_tokens} tok` : undefined}
        defaultOpen
      >
        <div className="text-sm leading-relaxed">
          {hasPIIMarkers(record.prompt_pseudonymized) ? (
            <PIIHighlighter text={record.prompt_pseudonymized} />
          ) : (
            <MarkdownRenderer content={record.prompt_pseudonymized} />
          )}
        </div>
      </Collapsible>

      {/* LLM Calls */}
      <Collapsible
        title={t('browser.detail.llmCalls')}
        badge={record.llm_calls?.length ?? 0}
      >
        {(!record.llm_calls || record.llm_calls.length === 0) ? (
          <p className="text-sm text-gray-500 italic">{t('browser.detail.noLlmCalls')}</p>
        ) : (
          <div className="space-y-2">
            {record.llm_calls.map((call, i) => (
              <Collapsible
                key={call.call_id || i}
                title={`${call.phase} — ${call.model}`}
                badge={`${call.latency_ms}ms`}
              >
                <div className="space-y-3">
                  <div className="flex items-center gap-2 flex-wrap">
                    <PhaseBadge phase={call.phase} />
                    <span className="text-xs text-gray-700 dark:text-gray-300 font-mono">{call.model}</span>
                    <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono ${
                      call.latency_ms > 5000
                        ? 'bg-accent-red/20 text-accent-red'
                        : call.latency_ms > 2000
                          ? 'bg-accent-yellow/20 text-accent-yellow'
                          : 'bg-accent-green/20 text-accent-green'
                    }`}>
                      {call.latency_ms}ms
                    </span>
                  </div>

                  <MonospaceBlock content={call.prompt_text} label={t('browser.detail.promptSent')} maxLines={15} />

                  <div>
                    <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">{t('browser.detail.response')}</div>
                    <MarkdownRenderer content={call.response_text} maxLines={20} />
                  </div>

                  <div className="flex items-center gap-3 text-[10px] text-gray-500 border-t border-gray-200 dark:border-gray-700/30 pt-2">
                    <span>Prompt: {call.prompt_tokens} tok</span>
                    <span>Response: {call.response_tokens} tok</span>
                    <span>Total: {call.total_tokens} tok</span>
                    <span>Finish: <span className="text-gray-600 dark:text-gray-400">{call.finish_reason}</span></span>
                    {call.is_cold_start && (
                      <span className="px-1 py-0.5 rounded bg-accent-orange/20 text-accent-orange">cold start</span>
                    )}
                    {!call.success && (
                      <span className="px-1 py-0.5 rounded bg-accent-red/20 text-accent-red">error</span>
                    )}
                  </div>
                </div>
              </Collapsible>
            ))}
          </div>
        )}
      </Collapsible>

      {/* Search Operations */}
      <Collapsible
        title={t('browser.detail.searchOps')}
        badge={record.search_operations?.length ?? 0}
      >
        {(!record.search_operations || record.search_operations.length === 0) ? (
          <p className="text-sm text-gray-500 italic">{t('browser.detail.noSearchOps')}</p>
        ) : (
          <div className="space-y-2">
            {record.search_operations.map((op, i) => (
              <Collapsible
                key={i}
                title={`${op.search_type} — ${op.sources_queried.length} sources — ${op.total_results} results`}
              >
                <div className="space-y-3">
                  {/* Query */}
                  <div>
                    <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">{t('browser.detail.query')}</div>
                    <div className="text-sm">
                      {hasPIIMarkers(op.query) ? (
                        <PIIHighlighter text={op.query} />
                      ) : (
                        <span className="text-gray-600 dark:text-gray-200">{op.query}</span>
                      )}
                    </div>
                  </div>

                  {/* Sources queried */}
                  <div>
                    <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">{t('browser.detail.sourcesQueried')}</div>
                    <div className="flex flex-wrap gap-1">
                      {op.sources_queried.map((src, j) => (
                        <span key={j} className="px-1.5 py-0.5 text-[10px] rounded bg-gray-100 dark:bg-surface-lighter text-gray-700 dark:text-gray-300 font-mono">
                          {src}
                        </span>
                      ))}
                    </div>
                  </div>

                  {/* Results per source */}
                  {op.results_per_source && Object.keys(op.results_per_source).length > 0 && (
                    <div>
                      <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">{t('browser.detail.resultsPerSource')}</div>
                      <div className="flex flex-wrap gap-2">
                        {Object.entries(op.results_per_source).map(([src, count]) => (
                          <span key={src} className="text-xs text-gray-300">
                            <span className="text-gray-500">{src}:</span> {count}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Chunks returned */}
                  {op.chunks_returned && op.chunks_returned.length > 0 && (
                    <Collapsible title={t('browser.detail.chunks')} badge={op.chunks_returned.length}>
                      <div className="space-y-2">
                        {op.chunks_returned.map((chunk, j) => (
                          <MonospaceBlock key={j} content={chunk} maxLines={8} label={`Chunk ${j + 1}`} />
                        ))}
                      </div>
                    </Collapsible>
                  )}

                  {/* Top scores */}
                  {op.top_scores && op.top_scores.length > 0 && (
                    <div>
                      <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">{t('browser.detail.scores')}</div>
                      <div className="flex gap-1.5">
                        {op.top_scores.map((score, j) => (
                          <span key={j} className="px-1.5 py-0.5 text-[10px] font-mono rounded bg-accent-green/15 text-accent-green">
                            {score.toFixed(4)}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  <div className="flex gap-3 text-[10px] text-gray-500 border-t border-gray-200 dark:border-gray-700/30 pt-2">
                    <span>Duration: {op.duration_ms}ms</span>
                    <span>Embedding: {op.embedding_duration_ms}ms</span>
                    <span>Deduplicated: {op.deduplicated_results}</span>
                    {op.rrf_applied && (
                      <span className="px-1 py-0.5 rounded bg-accent-teal/20 text-accent-teal">RRF</span>
                    )}
                  </div>
                </div>
              </Collapsible>
            ))}
          </div>
        )}
      </Collapsible>

      {/* Tool Executions */}
      <Collapsible
        title={t('browser.detail.toolExec')}
        badge={record.tool_executions?.length ?? 0}
      >
        {(!record.tool_executions || record.tool_executions.length === 0) ? (
          <p className="text-sm text-gray-500 italic">{t('browser.detail.noToolExec')}</p>
        ) : (
          <div className="space-y-2">
            {record.tool_executions.map((exec, i) => (
              <Collapsible
                key={exec.task_id || i}
                title={`${exec.task_type} — ${exec.duration_ms}ms`}
                badge={exec.success ? '✓' : '✗'}
              >
                <div className="space-y-3">
                  <div className="flex items-center gap-2">
                    <span className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-accent-orange/20 text-accent-orange">
                      {exec.task_type}
                    </span>
                    <span className="text-[10px] text-gray-400 font-mono">{exec.duration_ms}ms</span>
                    {exec.success ? (
                      <span className="text-accent-green text-xs">✓</span>
                    ) : (
                      <span className="text-accent-red text-xs">✗</span>
                    )}
                  </div>

                  <MonospaceBlock content={exec.input_query} label={t('browser.detail.inputQuery')} maxLines={10} />

                  <div>
                    <div className="text-[10px] uppercase tracking-wider text-gray-500 mb-1">{t('browser.detail.output')}</div>
                    {exec.output_text.length > 500 ? (
                      <MarkdownRenderer content={exec.output_text} maxLines={15} />
                    ) : (
                      <MonospaceBlock content={exec.output_text} maxLines={15} />
                    )}
                  </div>

                  <div className="flex gap-3 text-[10px] text-gray-500 border-t border-gray-200 dark:border-gray-700/30 pt-2">
                    <span>Tokens: {exec.tokens_used}</span>
                    <span>Results: {exec.results_count}</span>
                    {exec.sources_searched.length > 0 && (
                      <span>Sources: {exec.sources_searched.join(', ')}</span>
                    )}
                    {exec.error && (
                      <span className="text-accent-red">{exec.error}</span>
                    )}
                  </div>
                </div>
              </Collapsible>
            ))}
          </div>
        )}
      </Collapsible>

      {/* Response */}
      <Collapsible
        title={t('browser.detail.response')}
        badge={record.response_tokens ? `${record.response_tokens} tok` : undefined}
        defaultOpen
      >
        <div className="text-sm leading-relaxed">
          {hasPIIMarkers(record.response_pseudonymized) ? (
            <PIIHighlighter text={record.response_pseudonymized} />
          ) : (
            <MarkdownRenderer content={record.response_pseudonymized} />
          )}
        </div>
      </Collapsible>

      {/* Timing */}
      <Collapsible title={t('browser.detail.timing')}>
        <TimelineWaterfall
          phases={record.phase_metrics}
          totalDuration={record.total_duration_ms}
        />
      </Collapsible>
    </div>
  )
}

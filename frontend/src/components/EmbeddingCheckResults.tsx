import { useTranslation } from 'react-i18next'
import {
  CheckCircleIcon,
  ExclamationTriangleIcon,
  CpuChipIcon,
  PhotoIcon,
  ServerStackIcon,
} from '@heroicons/react/24/outline'
import type {
  EmbeddingChecksResult,
  EmbeddingProviderCheckResult,
} from '../api/client'

const fmt = (value: number | null | undefined, digits = 1): string =>
  value === null || value === undefined || Number.isNaN(value) ? '-' : value.toFixed(digits)

const pct = (value: number | null | undefined): string =>
  value === null || value === undefined ? '-' : `${(value * 100).toFixed(0)}%`

function Stat({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: 'good' | 'bad' }) {
  const toneClass =
    tone === 'good'
      ? 'text-green-600 dark:text-green-400'
      : tone === 'bad'
        ? 'text-red-600 dark:text-red-400'
        : 'text-primary-900 dark:text-gray-200'
  return (
    <div className="text-center p-2 bg-surface dark:bg-gray-800 rounded-lg">
      <p className={`text-lg font-bold ${toneClass}`}>{value}</p>
      <p className="text-xs text-secondary dark:text-gray-400">{label}</p>
      {hint && <p className="text-[10px] text-secondary dark:text-gray-500 mt-0.5">{hint}</p>}
    </div>
  )
}

function ProviderChecksCard({ result }: { result: EmbeddingProviderCheckResult }) {
  const { t } = useTranslation()
  const best = result.throughput.length
    ? result.throughput.reduce((a, b) => (b.tokens_per_s > a.tokens_per_s ? b : a))
    : null

  return (
    <div
      data-testid={`check-result-${result.provider}`}
      className={`rounded-2xl p-5 ${
        result.success
          ? 'bg-surface dark:bg-gray-800 shadow-elevation-1'
          : 'bg-red-50 dark:bg-red-900/20 ring-2 ring-red-400'
      }`}
    >
      <div className="flex items-center justify-between mb-4">
        <div>
          <p className="font-semibold text-primary-900 dark:text-gray-200">{result.provider}</p>
          <p className="text-xs text-secondary dark:text-gray-400">
            {result.model} · {result.provider_type}
            {result.base_url ? ` · ${result.base_url}` : ''}
            {` · ${(result.total_ms / 1000).toFixed(1)}s`}
          </p>
        </div>
        {result.success ? (
          <span className="flex items-center gap-1 text-xs font-medium text-green-600 dark:text-green-400">
            <CheckCircleIcon className="h-4 w-4" />
            {t('benchmark.checksPassed', 'Completed')}
          </span>
        ) : (
          <span className="flex items-center gap-1 text-xs font-medium text-red-600 dark:text-red-400">
            <ExclamationTriangleIcon className="h-4 w-4" />
            {t('benchmark.failed', 'Failed')}
          </span>
        )}
      </div>

      {!result.success && result.error && (
        <p className="text-sm text-red-600 dark:text-red-400 mb-3">{result.error}</p>
      )}

      {result.success && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            <Stat
              label={t('benchmark.queryLatency', 'Query latency p50 / p95')}
              value={result.latency ? `${fmt(result.latency.p50_ms, 0)} / ${fmt(result.latency.p95_ms, 0)} ms` : '-'}
              hint={result.latency ? `${t('benchmark.cold', 'cold')} ${fmt(result.latency.cold_ms, 0)} ms` : undefined}
            />
            <Stat
              label={t('benchmark.bestThroughput', 'Best throughput')}
              value={best ? `${fmt(best.tokens_per_s, 0)} tok/s` : '-'}
              hint={best ? `${fmt(best.texts_per_s, 1)} ${t('benchmark.textsPerSec', 'texts/s')} @ ${t('benchmark.batch', 'batch')} ${best.batch_size}` : undefined}
            />
            <Stat
              label={t('benchmark.dimension', 'Dimensions')}
              value={result.vectors ? `${result.vectors.dimension}d` : '-'}
              hint={
                result.vectors
                  ? result.vectors.normalized
                    ? t('benchmark.normalized', 'unit-normalised')
                    : t('benchmark.notNormalized', 'NOT normalised')
                  : undefined
              }
              tone={result.vectors ? (result.vectors.normalized && result.vectors.dimension_matches !== false ? 'good' : 'bad') : undefined}
            />
            <Stat
              label={t('benchmark.indexAgreement', 'Agreement with index')}
              value={result.agreement_with_index === null || result.agreement_with_index === undefined ? '-' : pct(result.agreement_with_index)}
              hint={t('benchmark.topkOverlap', 'top-10 neighbour overlap')}
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
            {/* Throughput by batch size */}
            <div>
              <p className="font-medium text-primary-900 dark:text-gray-200 mb-1">
                {t('benchmark.throughput', 'Throughput by batch size')}
              </p>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-secondary dark:text-gray-400">
                    <th className="text-left py-1">{t('benchmark.batch', 'batch')}</th>
                    <th className="text-right py-1">{t('benchmark.texts', 'texts')}</th>
                    <th className="text-right py-1">{t('benchmark.textsPerSec', 'texts/s')}</th>
                    <th className="text-right py-1">{t('benchmark.tokensPerSec', 'tok/s')}</th>
                  </tr>
                </thead>
                <tbody>
                  {result.throughput.map((p) => (
                    <tr key={p.batch_size} className="border-t border-gray-100 dark:border-gray-700 text-primary-900 dark:text-gray-200">
                      <td className="py-1">{p.batch_size}</td>
                      <td className="text-right py-1">{p.texts}</td>
                      <td className="text-right py-1">{fmt(p.texts_per_s, 1)}</td>
                      <td className="text-right py-1">
                        {fmt(p.tokens_per_s, 0)}
                        {p.token_source === 'estimate' && <span className="text-secondary"> ~</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Quality checks */}
            <div className="space-y-2">
              <div>
                <p className="font-medium text-primary-900 dark:text-gray-200">
                  {t('benchmark.mrlStability', 'MRL truncation stability')}
                </p>
                {result.truncation.length === 0 ? (
                  <p className="text-xs text-secondary dark:text-gray-400">-</p>
                ) : (
                  <p className="text-xs text-primary-900 dark:text-gray-200">
                    {result.truncation.map((c) => `${c.dim}d: ${pct(c.topk_overlap)}`).join(' · ')}
                  </p>
                )}
              </div>
              <div>
                <p className="font-medium text-primary-900 dark:text-gray-200">
                  {t('benchmark.retrievalSanity', 'Pseudo-query retrieval')}
                </p>
                <p className="text-xs text-primary-900 dark:text-gray-200">
                  {result.retrieval
                    ? `top-1 ${result.retrieval.top1_hits}/${result.retrieval.queries} (${pct(result.retrieval.top1_rate)}) · top-5 ${result.retrieval.top5_hits}/${result.retrieval.queries} (${pct(result.retrieval.top5_rate)})`
                    : '-'}
                </p>
              </div>
              <div className="flex items-center gap-1">
                <PhotoIcon className="h-4 w-4 text-secondary" />
                <p className="font-medium text-primary-900 dark:text-gray-200">
                  {t('benchmark.imageEmbedding', 'Image embedding')}:
                </p>
                <p className="text-xs text-primary-900 dark:text-gray-200">
                  {result.image
                    ? result.image.supported
                      ? `${t('benchmark.supported', 'supported')} (${result.image.tokens ?? '?'} tok, ${fmt(result.image.latency_ms, 0)} ms)`
                      : `${t('benchmark.notSupported', 'not supported')}${result.image.error ? ` — ${result.image.error}` : ''}`
                    : '-'}
                </p>
              </div>
              {(result.query_prefix || result.document_prefix) && (
                <p className="text-[11px] text-secondary dark:text-gray-400">
                  {t('benchmark.prefixes', 'Task prefixes')}: <code>{result.query_prefix || '∅'}</code> / <code>{result.document_prefix || '∅'}</code>
                </p>
              )}
            </div>
          </div>

          {/* Server + hardware */}
          <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-4 text-xs text-secondary dark:text-gray-400">
            <div className="flex items-start gap-2">
              <ServerStackIcon className="h-4 w-4 mt-0.5" />
              <div>
                <p className="font-medium text-primary-900 dark:text-gray-200">{t('benchmark.capabilities', 'Server')}</p>
                {result.capabilities ? (
                  <p>
                    {result.capabilities.model_alias || result.model}
                    {result.capabilities.build ? ` · ${result.capabilities.build}` : ''}
                    {result.capabilities.context_length ? ` · ctx ${result.capabilities.context_length}` : ''}
                    {result.capabilities.slots ? ` · ${result.capabilities.slots} slots` : ''}
                    {Object.keys(result.capabilities.modalities).length > 0 &&
                      ` · ${Object.entries(result.capabilities.modalities)
                        .filter(([, v]) => v)
                        .map(([k]) => k)
                        .join(', ')}`}
                  </p>
                ) : (
                  <p>-</p>
                )}
              </div>
            </div>
            <div className="flex items-start gap-2">
              <CpuChipIcon className="h-4 w-4 mt-0.5" />
              <div>
                <p className="font-medium text-primary-900 dark:text-gray-200">{t('benchmark.hardware', 'Check host')}</p>
                {result.hardware ? (
                  <p>
                    {result.hardware.hostname} · {result.hardware.cpu_count ?? '?'} CPU · {fmt(result.hardware.ram_gb, 0)} GB RAM ·{' '}
                    {result.hardware.gpus.length > 0
                      ? result.hardware.gpus.map((g) => `${g.name}${g.memory_total_mb ? ` ${Math.round(g.memory_total_mb / 1024)} GB` : ''}`).join(', ')
                      : t('benchmark.noGpu', 'no NVIDIA GPU visible')}
                  </p>
                ) : (
                  <p>-</p>
                )}
              </div>
            </div>
          </div>

          {result.warnings.length > 0 && (
            <ul className="mt-3 text-xs text-amber-700 dark:text-amber-400 list-disc list-inside">
              {result.warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}

export default function EmbeddingCheckResults({ result }: { result: EmbeddingChecksResult }) {
  const { t } = useTranslation()

  return (
    <div className="space-y-4" data-testid="embedding-check-results">
      <div className="rounded-2xl bg-surface-variant dark:bg-gray-700 p-4 text-sm text-secondary dark:text-gray-400">
        <strong>{t('benchmark.sampleInfo', 'Sample')}:</strong> {result.sample.count} {t('benchmark.texts', 'texts')} ·{' '}
        {result.sample.source === 'index'
          ? `${result.sample.database ?? ''}${result.sample.database ? '.' : ''}${result.sample.collection ?? 'chunks'}`
          : result.sample.file_name}{' '}
        · ø {Math.round(result.sample.avg_chars)} {t('benchmark.chars', 'chars')}
        {result.sample.reference_model &&
          ` · ${t('benchmark.referenceIndex', 'index vectors')}: ${result.sample.reference_model} (${result.sample.reference_dimension}d)`}
      </div>

      {result.results.map((r) => (
        <ProviderChecksCard key={r.provider} result={r} />
      ))}

      {result.agreement.length > 0 && (
        <div className="rounded-2xl bg-surface dark:bg-gray-800 p-4 shadow-elevation-1 text-sm">
          <p className="font-medium text-primary-900 dark:text-gray-200 mb-1">
            {t('benchmark.providerAgreement', 'Ranking agreement between providers (top-10 overlap)')}
          </p>
          <ul className="text-xs text-primary-900 dark:text-gray-200">
            {result.agreement.map((p) => (
              <li key={`${p.a}-${p.b}`}>
                {p.a} ↔ {p.b}: {pct(p.topk_overlap)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

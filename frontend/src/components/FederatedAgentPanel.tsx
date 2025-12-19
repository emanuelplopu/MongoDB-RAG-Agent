/**
 * FederatedAgentPanel - Displays the full trace of orchestrator-worker operations
 * 
 * This component shows:
 * - Orchestrator phases (analyze, plan, evaluate, synthesize)
 * - Worker task executions with inputs/outputs
 * - All documents and web links found
 * - Timing and token usage
 */

import { useState } from 'react'
import {
  ChevronDownIcon,
  ChevronRightIcon,
  CpuChipIcon,
  ClockIcon,
  DocumentTextIcon,
  GlobeAltIcon,
  LightBulbIcon,
  MagnifyingGlassIcon,
  CheckCircleIcon,
  XCircleIcon,
  SparklesIcon,
  BeakerIcon,
} from '@heroicons/react/24/outline'
import { FederatedAgentTrace } from '../api/client'

interface FederatedAgentPanelProps {
  trace: FederatedAgentTrace
}

export default function FederatedAgentPanel({ trace }: FederatedAgentPanelProps) {
  const [expanded, setExpanded] = useState(false)
  const [expandedSections, setExpandedSections] = useState<Record<string, boolean>>({
    orchestrator: false,
    workers: true,
    sources: true,
  })

  const toggleSection = (section: string) => {
    setExpandedSections(prev => ({ ...prev, [section]: !prev[section] }))
  }

  // Phase icons and colors
  const phaseConfig: Record<string, { icon: typeof LightBulbIcon; color: string }> = {
    analyze: { icon: MagnifyingGlassIcon, color: 'text-blue-600 dark:text-blue-400' },
    plan: { icon: LightBulbIcon, color: 'text-yellow-600 dark:text-yellow-400' },
    evaluate: { icon: BeakerIcon, color: 'text-purple-600 dark:text-purple-400' },
    synthesize: { icon: SparklesIcon, color: 'text-green-600 dark:text-green-400' },
  }

  // Task type icons
  const taskTypeConfig: Record<string, { icon: typeof DocumentTextIcon; label: string }> = {
    search_profile: { icon: DocumentTextIcon, label: 'Profile Docs' },
    search_cloud: { icon: GlobeAltIcon, label: 'Cloud Storage' },
    search_personal: { icon: DocumentTextIcon, label: 'Personal Data' },
    search_all: { icon: MagnifyingGlassIcon, label: 'All Sources' },
    web_search: { icon: GlobeAltIcon, label: 'Web Search' },
    browse_web: { icon: GlobeAltIcon, label: 'Browse Web' },
  }

  const totalDocs = trace.sources.documents.length
  const totalLinks = trace.sources.web_links.length

  return (
    <div className="mt-2">
      {/* Header - always visible */}
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 text-xs text-secondary dark:text-gray-400 hover:text-primary dark:hover:text-primary-300 transition-colors"
      >
        {expanded ? (
          <ChevronDownIcon className="h-3 w-3" />
        ) : (
          <ChevronRightIcon className="h-3 w-3" />
        )}
        <CpuChipIcon className="h-3 w-3" />
        <span className="font-medium">Agent Operations</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-primary-100 dark:bg-primary-900 text-primary-700 dark:text-primary-300">
          {trace.mode}
        </span>
        <span className="text-[10px] opacity-60">
          {trace.iterations} iteration{trace.iterations !== 1 ? 's' : ''}
          {' | '}
          {totalDocs} docs, {totalLinks} links
          {' | '}
          {trace.timing.total_ms.toFixed(0)}ms
        </span>
      </button>

      {expanded && (
        <div className="mt-2 space-y-3 pl-4 border-l-2 border-primary-200 dark:border-primary-800">
          {/* Models used */}
          <div className="flex items-center gap-3 text-[10px] text-secondary dark:text-gray-500">
            <span>🧠 Orchestrator: <code className="bg-gray-100 dark:bg-gray-800 px-1 rounded">{trace.models.orchestrator}</code></span>
            <span>⚡ Worker: <code className="bg-gray-100 dark:bg-gray-800 px-1 rounded">{trace.models.worker}</code></span>
          </div>

          {/* Orchestrator Steps */}
          <div className="space-y-1">
            <button
              onClick={() => toggleSection('orchestrator')}
              className="flex items-center gap-1 text-xs font-medium text-primary-700 dark:text-primary-300"
            >
              {expandedSections.orchestrator ? <ChevronDownIcon className="h-3 w-3" /> : <ChevronRightIcon className="h-3 w-3" />}
              🧠 Orchestrator Steps ({trace.orchestrator_steps.length})
              <span className="text-[10px] font-normal text-secondary dark:text-gray-500 ml-2">
                {trace.timing.orchestrator_ms.toFixed(0)}ms
              </span>
            </button>

            {expandedSections.orchestrator && (
              <div className="space-y-2 pl-4">
                {trace.orchestrator_steps.map((step, idx) => {
                  const config = phaseConfig[step.phase] || { icon: CpuChipIcon, color: 'text-gray-600' }
                  const PhaseIcon = config.icon

                  return (
                    <div
                      key={idx}
                      className="p-2 rounded-lg bg-surface dark:bg-gray-800/50 text-xs space-y-1"
                    >
                      <div className="flex items-center gap-2">
                        <PhaseIcon className={`h-3 w-3 ${config.color}`} />
                        <span className="font-medium capitalize">{step.phase}</span>
                        <span className="text-[10px] text-secondary dark:text-gray-500 ml-auto flex items-center gap-1">
                          <ClockIcon className="h-3 w-3" />
                          {step.duration_ms.toFixed(0)}ms
                        </span>
                      </div>
                      {step.reasoning && (
                        <p className="text-[10px] text-gray-600 dark:text-gray-400 line-clamp-2">
                          {step.reasoning.length > 200 ? step.reasoning.slice(0, 200) + '...' : step.reasoning}
                        </p>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {/* Worker Steps */}
          <div className="space-y-1">
            <button
              onClick={() => toggleSection('workers')}
              className="flex items-center gap-1 text-xs font-medium text-primary-700 dark:text-primary-300"
            >
              {expandedSections.workers ? <ChevronDownIcon className="h-3 w-3" /> : <ChevronRightIcon className="h-3 w-3" />}
              ⚡ Worker Executions ({trace.worker_steps.length})
              <span className="text-[10px] font-normal text-secondary dark:text-gray-500 ml-2">
                {trace.timing.worker_ms.toFixed(0)}ms
              </span>
            </button>

            {expandedSections.workers && (
              <div className="space-y-2 pl-4">
                {trace.worker_steps.map((step, idx) => {
                  const config = taskTypeConfig[step.task_type] || { icon: CpuChipIcon, label: step.task_type }
                  const TaskIcon = config.icon

                  return (
                    <div
                      key={idx}
                      className="p-2 rounded-lg bg-surface dark:bg-gray-800/50 text-xs space-y-2"
                    >
                      {/* Header */}
                      <div className="flex items-center gap-2">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium flex items-center gap-1 ${
                          step.success
                            ? 'bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-300'
                            : 'bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300'
                        }`}>
                          <TaskIcon className="h-3 w-3" />
                          {config.label}
                        </span>
                        {step.success ? (
                          <CheckCircleIcon className="h-3 w-3 text-green-500" />
                        ) : (
                          <XCircleIcon className="h-3 w-3 text-red-500" />
                        )}
                        <span className="text-[10px] text-secondary dark:text-gray-500 ml-auto flex items-center gap-1">
                          <ClockIcon className="h-3 w-3" />
                          {step.duration_ms.toFixed(0)}ms
                        </span>
                      </div>

                      {/* Input */}
                      {step.input && Object.keys(step.input).length > 0 && (
                        <div className="bg-gray-100 dark:bg-gray-700/50 rounded px-2 py-1">
                          <span className="text-[10px] text-secondary dark:text-gray-500 font-medium">Input: </span>
                          <span className="text-primary-900 dark:text-gray-200 text-[10px]">
                            {(() => {
                              const inputStr = JSON.stringify(step.input)
                              return inputStr.length > 150 ? inputStr.substring(0, 150) + '...' : inputStr
                            })()}
                          </span>
                        </div>
                      )}

                      {/* Results summary */}
                      <div className="flex items-center gap-3 text-[10px] text-secondary dark:text-gray-400">
                        <span className="flex items-center gap-1">
                          <DocumentTextIcon className="h-3 w-3" />
                          {step.documents.length} docs
                        </span>
                        <span className="flex items-center gap-1">
                          <GlobeAltIcon className="h-3 w-3" />
                          {step.web_links.length} links
                        </span>
                      </div>

                      {/* Document excerpts */}
                      {step.documents.length > 0 && (
                        <div className="space-y-1 border-t border-gray-200 dark:border-gray-700 pt-1">
                          {step.documents.slice(0, 2).map((doc, didx) => (
                            <div key={didx} className="bg-gray-50 dark:bg-gray-800 rounded px-2 py-1">
                              <div className="flex items-center justify-between">
                                <span className="text-[10px] font-medium text-primary-700 dark:text-primary-300 truncate max-w-[180px]">
                                  {doc.title}
                                </span>
                                <span className="text-[9px] text-secondary dark:text-gray-500">
                                  {(doc.score * 100).toFixed(0)}%
                                </span>
                              </div>
                              <p className="text-[10px] text-gray-600 dark:text-gray-400 line-clamp-1">
                                {doc.excerpt}
                              </p>
                            </div>
                          ))}
                          {step.documents.length > 2 && (
                            <span className="text-[9px] text-secondary dark:text-gray-500">
                              +{step.documents.length - 2} more documents
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {/* All Sources */}
          <div className="space-y-1">
            <button
              onClick={() => toggleSection('sources')}
              className="flex items-center gap-1 text-xs font-medium text-primary-700 dark:text-primary-300"
            >
              {expandedSections.sources ? <ChevronDownIcon className="h-3 w-3" /> : <ChevronRightIcon className="h-3 w-3" />}
              📚 All Sources ({totalDocs + totalLinks})
            </button>

            {expandedSections.sources && (
              <div className="space-y-2 pl-4">
                {/* Documents */}
                {trace.sources.documents.length > 0 && (
                  <div className="space-y-1">
                    <span className="text-[10px] font-medium text-secondary dark:text-gray-500">
                      Documents ({trace.sources.documents.length})
                    </span>
                    {trace.sources.documents.slice(0, 5).map((doc, idx) => (
                      <div
                        key={idx}
                        className="p-2 rounded bg-gray-50 dark:bg-gray-800 text-xs"
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-medium text-primary-700 dark:text-primary-300 truncate max-w-[200px]">
                            {doc.title}
                          </span>
                          <span className="text-[9px] px-1 py-0.5 rounded bg-primary-100 dark:bg-primary-900 text-primary-600 dark:text-primary-400">
                            {doc.source_type}
                          </span>
                        </div>
                        <p className="text-[10px] text-gray-600 dark:text-gray-400 line-clamp-2 mt-1">
                          {doc.excerpt}
                        </p>
                        <span className="text-[9px] text-secondary dark:text-gray-500">
                          {doc.source_database} | {(doc.score * 100).toFixed(0)}% match
                        </span>
                      </div>
                    ))}
                    {trace.sources.documents.length > 5 && (
                      <span className="text-[10px] text-secondary dark:text-gray-500">
                        +{trace.sources.documents.length - 5} more documents
                      </span>
                    )}
                  </div>
                )}

                {/* Web Links */}
                {trace.sources.web_links.length > 0 && (
                  <div className="space-y-1">
                    <span className="text-[10px] font-medium text-secondary dark:text-gray-500">
                      Web Links ({trace.sources.web_links.length})
                    </span>
                    {trace.sources.web_links.slice(0, 3).map((link, idx) => (
                      <div
                        key={idx}
                        className="p-2 rounded bg-gray-50 dark:bg-gray-800 text-xs"
                      >
                        <a
                          href={link.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-blue-600 dark:text-blue-400 hover:underline truncate block"
                        >
                          {link.title || link.url}
                        </a>
                        {link.excerpt && (
                          <p className="text-[10px] text-gray-600 dark:text-gray-400 line-clamp-1 mt-0.5">
                            {link.excerpt}
                          </p>
                        )}
                      </div>
                    ))}
                    {trace.sources.web_links.length > 3 && (
                      <span className="text-[10px] text-secondary dark:text-gray-500">
                        +{trace.sources.web_links.length - 3} more links
                      </span>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Stats Footer */}
          <div className="flex items-center gap-4 text-[10px] text-secondary dark:text-gray-500 pt-2 border-t border-gray-200 dark:border-gray-700">
            <span>{trace.tokens.total.toLocaleString()} tokens</span>
            <span>${trace.cost_usd.toFixed(4)}</span>
            <span>
              🧠 {trace.tokens.orchestrator.toLocaleString()} | ⚡ {trace.tokens.worker.toLocaleString()}
            </span>
          </div>
        </div>
      )}
    </div>
  )
}

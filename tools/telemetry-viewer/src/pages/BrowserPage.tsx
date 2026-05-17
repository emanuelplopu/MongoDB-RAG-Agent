import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useDataSource } from '../api'
import { RecordList } from '../components/RecordList'
import { RecordDetail } from '../components/RecordDetail'
import { TelemetryRecord } from '../types/telemetry'

export default function BrowserPage() {
  const { state } = useDataSource()
  const { t } = useTranslation()
  const [selectedRecord, setSelectedRecord] = useState<TelemetryRecord | null>(null)
  const [selectedForCompare, setSelectedForCompare] = useState<string[]>([])

  if (state.records.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-gray-500 dark:text-gray-400">
        <div className="text-center">
          <p className="text-2xl mb-2">📋 {t('browser.title')}</p>
          <p>{t('browser.emptyHint')}</p>
          <p className="text-sm mt-2 text-gray-400 dark:text-gray-500">{t('browser.emptyDragHint')}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="h-full flex">
      <div className="w-2/5 border-r border-gray-200 dark:border-gray-700/50 flex flex-col overflow-hidden">
        <RecordList
          records={state.records}
          selectedId={selectedRecord?.record_id}
          selectedForCompare={selectedForCompare}
          onSelect={setSelectedRecord}
          onToggleCompare={(id) => {
            setSelectedForCompare(prev =>
              prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]
            )
          }}
        />
      </div>
      <div className="w-3/5 overflow-y-auto">
        {selectedRecord ? (
          <RecordDetail record={selectedRecord} />
        ) : (
          <div className="h-full flex items-center justify-center text-gray-400 dark:text-gray-500">
            <p>{t('browser.selectRecord')}</p>
          </div>
        )}
      </div>
    </div>
  )
}

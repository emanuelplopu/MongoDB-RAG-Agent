import { CalendarIcon, DocumentTextIcon } from '@heroicons/react/24/outline'
import type { FilterGroup } from './SearchFilters'

// Example filter groups
export const exampleFilterGroups: FilterGroup[] = [
  {
    id: 'file_type',
    label: 'File Type',
    type: 'checkbox',
    icon: DocumentTextIcon,
    options: [
      { id: 'pdf', label: 'PDF', count: 42 },
      { id: 'docx', label: 'Word Document', count: 28 },
      { id: 'txt', label: 'Text File', count: 15 },
      { id: 'md', label: 'Markdown', count: 8 },
    ],
  },
  {
    id: 'date_range',
    label: 'Date Added',
    type: 'date-range',
    icon: CalendarIcon,
  },
  {
    id: 'source',
    label: 'Source',
    type: 'checkbox',
    options: [
      { id: 'upload', label: 'Uploaded', count: 50 },
      { id: 'google_drive', label: 'Google Drive', count: 30 },
      { id: 'dropbox', label: 'Dropbox', count: 13 },
    ],
  },
]

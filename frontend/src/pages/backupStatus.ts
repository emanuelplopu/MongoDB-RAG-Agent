import type { BackupStatus } from '../api/client'

/** Text colour classes for a backup status badge. */
export function getStatusColor(status: BackupStatus): string {
  switch (status) {
    case 'completed':
      return 'text-green-600 dark:text-green-400'
    case 'in_progress':
      return 'text-blue-600 dark:text-blue-400'
    case 'pending':
      return 'text-yellow-600 dark:text-yellow-400'
    case 'failed':
      return 'text-red-600 dark:text-red-400'
    default:
      return 'text-gray-600 dark:text-gray-400'
  }
}

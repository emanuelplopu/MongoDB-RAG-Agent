/**
 * Error helpers shared by pages and components.
 *
 * Kept apart from `client.ts` so tests that mock the API module wholesale do
 * not have to re-export them.
 */

/**
 * Human-readable message of a value thrown by an API call.
 *
 * A server `detail` carried on an axios-style `response.data` wins, then the
 * `message` of an `ApiError` or any other `Error`; anything else (or an empty
 * message) yields `fallback`.
 */
export function getErrorMessage(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } } | null | undefined)?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (err instanceof Error && err.message) return err.message
  return fallback
}

/** HTTP status carried by a thrown value (`ApiError.status`, or a mocked error's `status`), if any. */
export function getErrorStatus(err: unknown): number | undefined {
  if (typeof err !== 'object' || err === null || !('status' in err)) return undefined
  const status = (err as { status?: unknown }).status
  return typeof status === 'number' ? status : undefined
}

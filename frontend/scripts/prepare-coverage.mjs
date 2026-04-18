import { mkdirSync } from 'node:fs'

mkdirSync(new URL('../.coverage/.tmp/', import.meta.url), { recursive: true })

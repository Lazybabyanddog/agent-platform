import { spawnSync } from 'node:child_process'

const url = process.env.OPENAPI_URL ?? 'http://localhost:8010/openapi.json'
const result = spawnSync('npx', ['openapi-typescript', url, '-o', 'src/api/schema.d.ts'], {
  stdio: 'inherit',
  shell: process.platform === 'win32',
})

process.exit(result.status ?? 1)

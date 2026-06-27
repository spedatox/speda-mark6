import { BRANDS, DEFAULT_AGENT } from './brands'
import { deriveAccents } from './theme'
import type { AppProfile } from './types'

/**
 * The active brand for THIS build. Same code, every fork — pick the agent with
 * VITE_AGENT at build/dev time:
 *
 *   $env:VITE_AGENT='ultron'; npm run heartbreaker:dev          # dev
 *   powershell -File build-app.ps1 -Agent ultron -ApiBase ...   # installer
 *
 * Unset → SPEDA. Unknown value → SPEDA (safe fallback).
 */
const selected = (import.meta.env.VITE_AGENT as string | undefined)?.trim().toLowerCase()
const brand = (selected && BRANDS[selected]) || BRANDS[DEFAULT_AGENT]

const profile: AppProfile = { ...brand, accentHover: deriveAccents(brand.accent).bright }

export default profile

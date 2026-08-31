// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { ElectronAPI } from '@electron-toolkit/preload'

declare global {
  interface Window {
    electron: ElectronAPI
    api: {
      platform: string
      getConfig: () => Promise<{ apiBase: string; apiKey: string; configured: boolean; isDev: boolean }>
      setConfig: (cfg: { apiBase: string; apiKey: string }) => Promise<{ apiBase: string; apiKey: string; configured: boolean }>
      windowMinimize: () => void
      windowMaximize: () => void
      windowClose: () => void
      openExternal: (url: string) => void
      selectDirectory: (current?: string) => Promise<string | null>
    }
  }
}

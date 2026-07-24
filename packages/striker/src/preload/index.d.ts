import { ElectronAPI } from '@electron-toolkit/preload'

declare global {
  interface Window {
    electron: ElectronAPI
    api: {
      platform: string
      getConfig: () => Promise<{ apiBase: string; apiKey: string }>
      windowMinimize: () => void
      windowMaximize: () => void
      windowClose: () => void
      openExternal: (url: string) => void
    }
  }
}

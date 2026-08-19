import { app, BrowserWindow, ipcMain, shell, dialog, net, protocol } from 'electron'
import { existsSync, readFileSync, writeFileSync } from 'fs'
import { join, normalize, sep } from 'path'
import { pathToFileURL } from 'url'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'

/**
 * The window icon, for the runs where Windows has nothing else to go on.
 *
 * A PACKAGED build takes its icon from the .exe, which electron-builder stamps
 * from the same `build/icon.ico` (see electron-builder.yml). `electron-vite dev`
 * has no .exe, so with no `icon` on the BrowserWindow the window and the
 * taskbar fall back to Electron's own logo — which is what every dev run has
 * been showing. `__dirname` is `out/main` at runtime, so the build resources
 * sit two levels up. Guarded because `build/` is not inside the packaged
 * `files` glob; there the .exe icon is already correct.
 */
const WINDOW_ICON = join(__dirname, '../../build/icon.ico')

/**
 * The packaged renderer is served over `app://bundle/` — NOT `file://`.
 *
 * A `file://` document has an opaque origin, so Chromium refuses to start a
 * worker from a `blob:file:///…` URL no matter what the CSP says. MapLibre GL
 * builds its tile worker exactly that way, which is why every ```map fence in
 * the installed build degraded to "MAP // CANVAS UNAVAILABLE — Failed to
 * construct 'Worker'". The dev server (http://localhost) and the web build never
 * hit this; only the packaged desktop app did.
 *
 * Registering a standard, secure scheme gives the renderer a real origin, so
 * blob workers, same-origin fetch and storage all behave as they do on the web.
 * `standard: true` is what makes `app://bundle` an origin at all; without it the
 * scheme is treated as opaque and nothing is gained over file://.
 */
const RENDERER_SCHEME = 'app'
const RENDERER_HOST = 'bundle'
const RENDERER_ORIGIN = `${RENDERER_SCHEME}://${RENDERER_HOST}`

protocol.registerSchemesAsPrivileged([
  {
    scheme: RENDERER_SCHEME,
    privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true }
  }
])

/** Map `app://bundle/<path>` onto the built renderer directory, on disk. */
function registerRendererProtocol(): void {
  const root = join(__dirname, '../renderer')
  protocol.handle(RENDERER_SCHEME, (request) => {
    const { pathname } = new URL(request.url)
    const relative = decodeURIComponent(pathname === '/' ? '/index.html' : pathname)
    const target = normalize(join(root, relative))
    // Refuse anything that escapes the bundle — `app://bundle/../../secrets`.
    if (target !== root && !target.startsWith(root + sep)) {
      return new Response('Not found', { status: 404 })
    }
    // A miss (e.g. the browser's implicit /favicon.ico probe) must answer 404,
    // not reject — an unhandled rejection here surfaces as a bare net:: error.
    return net
      .fetch(pathToFileURL(target).toString())
      .catch(() => new Response('Not found', { status: 404 }))
  })
}

function createWindow(): void {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 900,
    minHeight: 600,
    show: false,
    autoHideMenuBar: true,
    ...(existsSync(WINDOW_ICON) ? { icon: WINDOW_ICON } : {}),
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false
    }
  })

  win.on('ready-to-show', () => win.show())

  // Open external links in the OS browser, not in the app
  win.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url)
    return { action: 'deny' }
  })

  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    win.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    win.loadURL(`${RENDERER_ORIGIN}/index.html`)
  }
}

/**
 * Where the owner's server URL + key land when they fill in the connection
 * popup (see `set-config` below). One JSON file in userData, not electron-store —
 * this is the only piece of persisted app state that needs to exist, so a
 * dependency would outweigh the twelve lines it replaces.
 */
let connectionFile = ''

function readPersistedConnection(): { apiBase?: string; apiKey?: string } {
  try {
    const data = JSON.parse(readFileSync(connectionFile, 'utf-8'))
    return {
      apiBase: typeof data.apiBase === 'string' ? data.apiBase : undefined,
      apiKey: typeof data.apiKey === 'string' ? data.apiKey : undefined,
    }
  } catch {
    return {}
  }
}

/** First non-blank string, left to right — env beats a build-time bake beats
 *  whatever the owner last typed into the connection popup. */
function firstNonEmpty(...vals: (string | undefined)[]): string | undefined {
  for (const v of vals) if (v && v.trim()) return v.trim()
  return undefined
}

app.whenReady().then(() => {
  electronApp.setAppUserModelId('com.speda.heartbreaker')
  connectionFile = join(app.getPath('userData'), 'connection.json')

  registerRendererProtocol()

  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  // Window control IPC — used by custom title bar buttons
  ipcMain.on('window-minimize', (e) => BrowserWindow.fromWebContents(e.sender)?.minimize())
  ipcMain.on('window-maximize', (e) => {
    const w = BrowserWindow.fromWebContents(e.sender)
    // The optional chain only guarded the TEST: with no window, `w?.isMaximized()`
    // is undefined, the ternary took the false branch, and `w.maximize()` threw.
    if (!w) return
    if (w.isMaximized()) w.unmaximize()
    else w.maximize()
  })
  ipcMain.on('window-close', (e) => BrowserWindow.fromWebContents(e.sender)?.close())

  // Open a URL in the system browser (used by Sign in with Google)
  ipcMain.on('open-external', (_e, url: string) => {
    if (typeof url === 'string' && /^https?:\/\//.test(url)) shell.openExternal(url)
  })

  // Native folder picker — used to choose the Forge workspace for Optimus.
  // Returns the absolute directory path, or null if the dialog was cancelled.
  ipcMain.handle('select-directory', async (e, current?: string) => {
    const w = BrowserWindow.fromWebContents(e.sender)
    const opts = {
      properties: ['openDirectory' as const],
      ...(current ? { defaultPath: current } : {}),
    }
    const res = w
      ? await dialog.showOpenDialog(w, opts)
      : await dialog.showOpenDialog(opts)
    return res.canceled || res.filePaths.length === 0 ? null : res.filePaths[0]
  })

  // Config IPC — renderer reads API base + key from the main process env.
  // Defaults must match the backend (speda_api_key="dev-key") and the web
  // fallback in App.tsx, otherwise the auth middleware rejects every request 401.
  // Resolution order: runtime env > value baked at build time (MAIN_VITE_*) >
  // the connection popup's saved file > localhost default. build-app.ps1 bakes
  // the server URL + key into the installer for a shipped build; a build that
  // skipped that step (or `npm run heartbreaker:dev` outside dev, in principle)
  // falls through to the popup instead of silently pointing at a localhost the
  // end user's machine will never have.
  ipcMain.handle('get-config', () => {
    const persisted = readPersistedConnection()
    const envBase = process.env.SPEDA_API_BASE
    // @ts-ignore - electron-vite injects MAIN_VITE_* into import.meta.env
    const bakedBase = import.meta.env.MAIN_VITE_SPEDA_API_BASE as string | undefined
    const envKey = process.env.SPEDA_API_KEY
    // @ts-ignore
    const bakedKey = import.meta.env.MAIN_VITE_SPEDA_API_KEY as string | undefined

    const resolvedBase = firstNonEmpty(envBase, bakedBase, persisted.apiBase)
    const resolvedKey = firstNonEmpty(envKey, bakedKey, persisted.apiKey)

    return {
      apiBase: resolvedBase ?? 'http://localhost:8000',
      apiKey: resolvedKey ?? 'dev-key',
      // False only when nothing beyond the bare local-dev default was found —
      // the signal the renderer uses to raise the connection popup.
      configured: Boolean(resolvedBase),
      isDev: is.dev,
    }
  })

  // Written by the connection popup (first run, or "Edit" in Settings → Account).
  // Takes effect immediately — the renderer updates its own config state after
  // this resolves, no restart needed — and survives every future launch.
  ipcMain.handle('set-config', (_e, next: { apiBase?: string; apiKey?: string }) => {
    const apiBase = (next?.apiBase ?? '').trim()
    const apiKey = (next?.apiKey ?? '').trim()
    if (!apiBase || !apiKey) throw new Error('A server URL and an API key are both required.')
    writeFileSync(connectionFile, JSON.stringify({ apiBase, apiKey }, null, 2), 'utf-8')
    return { apiBase, apiKey, configured: true }
  })

  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

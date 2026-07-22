import { app, BrowserWindow, ipcMain, shell, dialog, net, protocol } from 'electron'
import { join, normalize, sep } from 'path'
import { pathToFileURL } from 'url'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'

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

app.whenReady().then(() => {
  electronApp.setAppUserModelId('com.speda.heartbreaker')

  registerRendererProtocol()

  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  // Window control IPC — used by custom title bar buttons
  ipcMain.on('window-minimize', (e) => BrowserWindow.fromWebContents(e.sender)?.minimize())
  ipcMain.on('window-maximize', (e) => {
    const w = BrowserWindow.fromWebContents(e.sender)
    w?.isMaximized() ? w.unmaximize() : w.maximize()
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
  // localhost default. build-app.ps1 bakes the server URL + key into the installer.
  ipcMain.handle('get-config', () => ({
    apiBase:
      process.env.SPEDA_API_BASE ??
      // @ts-ignore - electron-vite injects MAIN_VITE_* into import.meta.env
      (import.meta.env.MAIN_VITE_SPEDA_API_BASE as string | undefined) ??
      'http://localhost:8000',
    apiKey:
      process.env.SPEDA_API_KEY ??
      // @ts-ignore
      (import.meta.env.MAIN_VITE_SPEDA_API_KEY as string | undefined) ??
      'dev-key'
  }))

  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

import { app, BrowserWindow, clipboard, ipcMain, Menu, nativeImage, nativeTheme, net, protocol, session, shell, Tray } from 'electron'
import { existsSync } from 'node:fs'
import { dirname, join, normalize, relative, resolve, sep } from 'node:path'
import { release } from 'node:os'
import { pathToFileURL } from 'node:url'
import trayIconPath from '../../resources/tray.png?asset'
import appIconPath from '../../resources/icon.png?asset'
import winIconPath from '../../resources/icon.ico?asset'
import { CoreSupervisor } from './core'

const isDev = !app.isPackaged && !!process.env.ELECTRON_RENDERER_URL

// Portable build: a `portable` marker file next to the exe keeps runs, blobs and Electron state beside the app.
const portableDir = app.isPackaged ? dirname(process.execPath) : null
const isPortable = !!portableDir && existsSync(join(portableDir, 'portable'))
if (isPortable && portableDir) {
  process.env.STUDIO_DATA_DIR ??= join(portableDir, 'data')
  app.setPath('userData', join(portableDir, 'userdata'))
}
const isMock = import.meta.env.VITE_MOCK === '1' || process.env.VITE_MOCK === '1'
const RENDERER_ORIGIN = isDev ? new URL(process.env.ELECTRON_RENDERER_URL!).origin : 'app://studio'
const rendererRoot = join(__dirname, '../renderer')
// Mica needs Windows 11 22H2+ (build 22621); older builds would render the "transparent" window black.
const vibrancy = process.platform === 'win32' && Number(release().split('.')[2] ?? 0) >= 22621

protocol.registerSchemesAsPrivileged([
  { scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true, codeCache: true } }
])

if (!app.requestSingleInstanceLock()) app.quit()

const core = new CoreSupervisor({
  repoRoot: process.env.STUDIO_REPO ?? resolve(app.getAppPath(), '..'),
  mock: isMock,
  packaged: app.isPackaged,
  resourcesPath: process.resourcesPath,
  appDataPath: app.getPath('appData')
})
let win: BrowserWindow | null = null
let tray: Tray | null = null
let quitting = false
let coreStopped = false

function csp(): string {
  const local = 'http://127.0.0.1:* ws://127.0.0.1:*'
  const devExtra = isDev ? ` ${RENDERER_ORIGIN} ${RENDERER_ORIGIN.replace('http', 'ws')}` : ''
  return [
    "default-src 'none'",
    "script-src 'self'",
    // Vue/motion write inline style attributes; no inline <script> is ever allowed.
    "style-src 'self' 'unsafe-inline'",
    `img-src 'self' data: blob: http://127.0.0.1:*`,
    "font-src 'self' data:",
    // three.js GLTFLoader fetches embedded textures through blob: URLs.
    `connect-src 'self' blob: data: ${local}${devExtra}`,
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
    "object-src 'none'"
  ].join('; ')
}

function serveAppProtocol(): void {
  protocol.handle('app', async (req) => {
    const url = new URL(req.url)
    if (url.host !== 'studio') return new Response('not found', { status: 404 })
    const rel = normalize(decodeURIComponent(url.pathname)).replace(/^[\\/]+/, '') || 'index.html'
    const file = resolve(rendererRoot, rel)
    const back = relative(rendererRoot, file)
    if (back.startsWith('..') || back.includes(`..${sep}`)) return new Response('forbidden', { status: 403 })
    const res = await net.fetch(pathToFileURL(file).toString())
    const headers = new Headers(res.headers)
    headers.set('Content-Security-Policy', csp())
    headers.set('X-Content-Type-Options', 'nosniff')
    return new Response(res.body, { status: res.status, headers })
  })
}

function hardenSession(): void {
  const ses = session.defaultSession
  ses.setPermissionRequestHandler((_wc, _perm, cb) => cb(false))
  ses.setPermissionCheckHandler(() => false)
  if (isDev) {
    ses.webRequest.onHeadersReceived((details, cb) => {
      if (!details.url.startsWith(RENDERER_ORIGIN)) return cb({})
      cb({ responseHeaders: { ...details.responseHeaders, 'Content-Security-Policy': [csp()] } })
    })
  }
}

function overlayColors(): { color: string; symbolColor: string; height: number } {
  const dark = nativeTheme.shouldUseDarkColors
  return { color: '#00000000', symbolColor: dark ? '#f5f5f7' : '#1d1d1f', height: 44 }
}

function createWindow(): void {
  const dark = nativeTheme.shouldUseDarkColors
  win = new BrowserWindow({
    width: 1320,
    height: 860,
    minWidth: 960,
    minHeight: 620,
    show: false,
    title: 'AI Asset Studio',
    icon: process.platform === 'win32' ? winIconPath : appIconPath,
    backgroundColor: vibrancy ? '#00000000' : dark ? '#1c1c1e' : '#f2f2f5',
    backgroundMaterial: vibrancy ? 'mica' : undefined,
    titleBarStyle: 'hidden',
    titleBarOverlay: overlayColors(),
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      nodeIntegrationInWorker: false,
      webviewTag: false,
      spellcheck: false,
      devTools: !app.isPackaged
    }
  })

  win.once('ready-to-show', () => win?.show())
  if (isDev) {
    win.webContents.on('console-message', (e) => {
      if (e.level === 'error' || e.level === 'warning') console.log(`[renderer:${e.level}] ${e.message} (${e.sourceId}:${e.lineNumber})`)
    })
  }
  win.on('close', (e) => {
    if (quitting) return
    e.preventDefault()
    void core.hasActiveRuns().then((active) => {
      if (active) {
        win?.hide()
        tray?.displayBalloon?.({ title: 'AI Asset Studio', content: 'Vẫn đang chạy nền. Mở lại từ khay hệ thống.' })
      } else {
        app.quit()
      }
    })
  })
  win.on('closed', () => {
    win = null
  })

  const query = `?vibrancy=${vibrancy ? 1 : 0}&platform=${process.platform}`
  if (isDev) void win.loadURL(`${process.env.ELECTRON_RENDERER_URL}/${query}`)
  else void win.loadURL(`app://studio/index.html${query}`)
}

function createTray(): void {
  // The multi-size .ico lets Windows pick the exact 16/20/24/32px frame for the current DPI instead of downscaling.
  tray = new Tray(nativeImage.createFromPath(process.platform === 'win32' ? winIconPath : trayIconPath))
  tray.setToolTip('AI Asset Studio')
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: 'Mở AI Asset Studio', click: showWindow },
      { type: 'separator' },
      { label: 'Thoát', click: () => app.quit() }
    ])
  )
  tray.on('click', showWindow)
}

function showWindow(): void {
  if (!win) createWindow()
  else {
    if (win.isMinimized()) win.restore()
    win.show()
    win.focus()
  }
}

// Node's URL reports origin "null" for custom schemes like app://, so compare scheme + host instead.
function originOf(url: string): string | null {
  try {
    const u = new URL(url)
    return `${u.protocol}//${u.host}`
  } catch {
    return null
  }
}

function isTrustedSender(frameUrl: string | undefined): boolean {
  return !!frameUrl && originOf(frameUrl) === RENDERER_ORIGIN
}

function registerIpc(): void {
  ipcMain.handle('studio:getConnection', (e) => {
    if (!isTrustedSender(e.senderFrame?.url)) throw new Error('untrusted sender')
    return core.waitReady()
  })
  ipcMain.handle('studio:mcpInfo', (e) => {
    if (!isTrustedSender(e.senderFrame?.url)) throw new Error('untrusted sender')
    const launch = core.launch(...(isPortable ? ['mcp', '--data-dir', core.dataDir] : ['mcp']))
    return { ...launch, coreDir: core.coreDir, packaged: app.isPackaged }
  })
  ipcMain.handle('studio:copyText', (e, text: unknown) => {
    if (!isTrustedSender(e.senderFrame?.url)) throw new Error('untrusted sender')
    if (typeof text !== 'string' || text.length > 10_000) throw new Error('invalid text')
    clipboard.writeText(text)
  })
  ipcMain.handle('studio:coreStatus', (e) => {
    if (!isTrustedSender(e.senderFrame?.url)) throw new Error('untrusted sender')
    return core.current
  })
  core.on('status', (s) => {
    if (isDev) console.log(`[core] ${s.state}${s.baseUrl ? ` ${s.baseUrl}` : ''}${s.message ? ` — ${s.message}` : ''}`)
    for (const w of BrowserWindow.getAllWindows()) w.webContents.send('studio:coreStatus', s)
  })
}

app.on('web-contents-created', (_e, contents) => {
  contents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://')) void shell.openExternal(url)
    return { action: 'deny' }
  })
  contents.on('will-navigate', (e, url) => {
    if (originOf(url) !== RENDERER_ORIGIN) e.preventDefault()
  })
  contents.on('will-attach-webview', (e) => e.preventDefault())
})

app.on('second-instance', showWindow)

app.on('before-quit', (e) => {
  quitting = true
  if (coreStopped) return
  e.preventDefault()
  void core.stop().finally(() => {
    coreStopped = true
    app.quit()
  })
})

app.on('window-all-closed', () => {
  app.quit()
})

nativeTheme.on('updated', () => {
  win?.setTitleBarOverlay?.(overlayColors())
})

void app.whenReady().then(() => {
  app.setAppUserModelId('studio.ai-asset')
  hardenSession()
  if (!isDev) serveAppProtocol()
  registerIpc()
  core.start()
  createWindow()
  createTray()
})

import { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, nativeImage, nativeTheme, net, Notification, protocol, session, shell, Tray, type IpcMainInvokeEvent } from 'electron'
import { existsSync, readFileSync } from 'node:fs'
import { copyFile, cp, mkdir, writeFile } from 'node:fs/promises'
import { basename, dirname, extname, isAbsolute, join, normalize, relative, resolve, sep } from 'node:path'
import { release } from 'node:os'
import { pathToFileURL } from 'node:url'
import trayIconPath from '../../resources/tray.png?asset'
import appIconPath from '../../resources/icon.png?asset'
import winIconPath from '../../resources/icon.ico?asset'
import { CoreSupervisor } from './core'
import { deviceInfo } from './device'
import { AssetExporter, checkHash, checkName, checkPreset, checkStep } from './assets'
import { Devlog, listLogs, RateLimiter, type LogLevel } from './devlog'
import { buildBundle, buildReport, parsePayload, REPORT_MAX_CHARS } from './diagnostics'
import type { AppNotification, DiagInfo, PickPathOptions, ProgressMode, ThemeMode } from '../preload/types'

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

// exit() rather than quit(): quit() still lets whenReady fire, which spawned a second core before exiting.
if (!app.requestSingleInstanceLock()) app.exit(0)

const core = new CoreSupervisor({
  repoRoot: process.env.STUDIO_REPO ?? resolve(app.getAppPath(), '..'),
  mock: isMock,
  packaged: app.isPackaged,
  resourcesPath: process.resourcesPath,
  appDataPath: app.getPath('appData')
})
const devlog = new Devlog(join(core.dataDir, 'logs'), () => core.secrets)
devlog.app(
  'info',
  `start v${app.getVersion()} electron ${process.versions.electron} ${process.platform}/${process.arch} packaged=${app.isPackaged} portable=${isPortable} mock=${isMock} data=${core.dataDir}`
)
core.on('output', (stream: 'out' | 'err', line: string) => devlog.core(stream, line))
core.on('log', (level: LogLevel, msg: string) => devlog.app(level, msg))
process.on('uncaughtExceptionMonitor', (err, origin) => devlog.app('error', `${origin}: ${err.stack ?? err.message}`))
const rendererLimit = new RateLimiter(60, 10_000)
const assets = new AssetExporter(core)

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

const THEMES: readonly ThemeMode[] = ['system', 'light', 'dark']
const uiPrefsFile = (): string => join(app.getPath('userData'), 'ui.json')

function loadTheme(): void {
  try {
    const t = (JSON.parse(readFileSync(uiPrefsFile(), 'utf8')) as { theme?: unknown }).theme
    if (THEMES.includes(t as ThemeMode)) nativeTheme.themeSource = t as ThemeMode
  } catch {
    /* no prefs yet → follow Windows */
  }
}

async function saveTheme(mode: ThemeMode): Promise<void> {
  nativeTheme.themeSource = mode
  await mkdir(dirname(uiPrefsFile()), { recursive: true })
  await writeFile(uiPrefsFile(), JSON.stringify({ theme: mode }) + '\n', 'utf8')
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
  const wc = win.webContents
  wc.on('did-fail-load', (_e, code, desc, url) => devlog.app('error', `renderer load failed ${code} ${desc} ${url}`))
  wc.on('preload-error', (_e, path, err) => devlog.app('error', `preload ${path}: ${err.stack ?? err.message}`))
  wc.on('render-process-gone', (_e, d) => devlog.app('error', `renderer gone: ${d.reason} (exit ${d.exitCode})`))
  wc.on('unresponsive', () => devlog.app('warn', 'renderer unresponsive'))
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

function handle(channel: string, fn: (e: IpcMainInvokeEvent, ...args: unknown[]) => unknown): void {
  ipcMain.handle(channel, async (e, ...args: unknown[]) => {
    if (!isTrustedSender(e.senderFrame?.url)) {
      devlog.app('warn', `${channel}: rejected untrusted sender ${e.senderFrame?.url ?? '?'}`)
      throw new Error('untrusted sender')
    }
    try {
      return await fn(e, ...args)
    } catch (err) {
      devlog.app('error', `${channel}: ${(err as Error).stack ?? String(err)}`)
      throw err
    }
  })
}

const logsDir = (): string => join(core.dataDir, 'logs')

async function diagInfo(): Promise<DiagInfo> {
  return {
    appVersion: app.getVersion(),
    electron: process.versions.electron,
    chrome: process.versions.chrome,
    node: process.versions.node,
    platform: process.platform,
    arch: process.arch,
    osRelease: release(),
    packaged: app.isPackaged,
    portable: isPortable,
    mock: isMock,
    dataDir: core.dataDir,
    logsDir: logsDir(),
    core: core.current,
    logs: await listLogs(logsDir())
  }
}

function stamp(): string {
  const d = new Date()
  const p = (n: number): string => String(n).padStart(2, '0')
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}`
}

const RENDERER_LEVELS = new Set<string>(['info', 'warn', 'error'])
const RENDERER_LINE_MAX = 4000

function registerIpc(): void {
  handle('studio:getConnection', () => core.waitReady())
  handle('studio:mcpInfo', () => {
    const launch = core.launch(...(isPortable ? ['mcp', '--data-dir', core.dataDir] : ['mcp']))
    return { ...launch, coreDir: core.coreDir, packaged: app.isPackaged }
  })
  handle('studio:copyText', (_e, text) => {
    if (typeof text !== 'string' || text.length > 10_000) throw new Error('invalid text')
    clipboard.writeText(text)
  })
  handle('studio:coreStatus', () => core.current)
  handle('studio:diagInfo', () => diagInfo())
  handle('studio:copyDiagnostics', async (_e, raw) => {
    const report = await buildReport(await diagInfo(), parsePayload(raw), devlog)
    if (report.length > REPORT_MAX_CHARS) throw new Error('report too large')
    clipboard.writeText(report)
    devlog.app('info', `copied diagnostics report (${report.length} chars)`)
    return { chars: report.length }
  })
  handle('studio:exportDiagnostics', async (e, raw) => {
    const payload = parsePayload(raw)
    const owner = BrowserWindow.fromWebContents(e.sender)
    const opts = {
      title: 'Xuất nhật ký chẩn đoán',
      defaultPath: join(app.getPath('downloads'), `ai-asset-studio-logs-${stamp()}.zip`),
      filters: [{ name: 'Zip', extensions: ['zip'] }]
    }
    const res = owner ? await dialog.showSaveDialog(owner, opts) : await dialog.showSaveDialog(opts)
    if (res.canceled || !res.filePath) return null
    const { zip, files } = await buildBundle(await diagInfo(), payload, devlog)
    await writeFile(res.filePath, zip)
    devlog.app('info', `exported diagnostics bundle ${res.filePath} (${files} files, ${zip.length} bytes)`)
    return { path: res.filePath, files, bytes: zip.length }
  })
  handle('studio:openLogsDir', async () => {
    await mkdir(logsDir(), { recursive: true })
    const err = await shell.openPath(logsDir())
    if (err) throw new Error(err)
  })
  ipcMain.on('studio:log', (e, level: unknown, message: unknown) => {
    if (!isTrustedSender(e.senderFrame?.url)) return
    if (typeof level !== 'string' || !RENDERER_LEVELS.has(level) || typeof message !== 'string') return
    const dropped = rendererLimit.take()
    if (dropped === null) return
    if (dropped > 0) devlog.renderer('warn', `… bỏ qua ${dropped} dòng (quá giới hạn tốc độ)`)
    devlog.renderer(level as LogLevel, message.length > RENDERER_LINE_MAX ? `${message.slice(0, RENDERER_LINE_MAX)}…` : message)
  })
  handle('studio:openSettingsFile', async () => {
    const file = join(core.dataDir, 'settings.json')
    if (!existsSync(file)) {
      await mkdir(core.dataDir, { recursive: true })
      await writeFile(file, '{}\n', 'utf8')
    }
    const err = await shell.openPath(file)
    if (err) throw new Error(err)
  })
  handle('studio:restartCore', () => core.restart())
  handle('studio:getTheme', () => nativeTheme.themeSource)
  handle('studio:deviceInfo', () => deviceInfo())
  handle('studio:setTheme', (_e, mode) => {
    if (!THEMES.includes(mode as ThemeMode)) throw new Error('invalid theme')
    return saveTheme(mode as ThemeMode)
  })
  handle('studio:pickPath', (e, raw) => pickPath(e, raw))
  registerAssetIpc()
  registerShellIpc()
  core.on('status', (s) => {
    if (isDev) console.log(`[core] ${s.state}${s.baseUrl ? ` ${s.baseUrl}` : ''}${s.message ? ` — ${s.message}` : ''}`)
    devlog.app(
      s.state === 'error' || s.state === 'restarting' ? 'warn' : 'info',
      `core ${s.state} attempt=${s.attempt}${s.pid ? ` pid=${s.pid}` : ''}${s.attached ? ' attached' : ''}${s.baseUrl ? ` ${s.baseUrl}` : ''}${s.message ? ` — ${s.message}` : ''}${s.detail ? `\n${s.detail}` : ''}`
    )
    for (const w of BrowserWindow.getAllWindows()) w.webContents.send('studio:coreStatus', s)
  })
}

function ownerOf(e: { sender: Electron.WebContents }): BrowserWindow | null {
  return BrowserWindow.fromWebContents(e.sender)
}

async function pickPath(e: IpcMainInvokeEvent, raw: unknown): Promise<string | null> {
  const o = (raw ?? {}) as Partial<PickPathOptions>
  if (o.kind !== 'file' && o.kind !== 'dir') throw new Error('invalid kind')
  const str = (v: unknown, max: number): string | undefined => (typeof v === 'string' && v.length <= max ? v : undefined)
  const exts = Array.isArray(o.extensions) ? o.extensions.filter((x): x is string => typeof x === 'string' && /^\w{1,10}$/.test(x)).slice(0, 8) : []
  const defaultPath = str(o.defaultPath, 1024)
  const opts: Electron.OpenDialogOptions = {
    title: str(o.title, 120),
    defaultPath: defaultPath && isAbsolute(defaultPath) ? defaultPath : undefined,
    properties: o.kind === 'dir' ? ['openDirectory', 'createDirectory'] : ['openFile'],
    filters: o.kind === 'file' && exts.length ? [{ name: exts.join(', ').toUpperCase(), extensions: exts }, { name: 'Tất cả', extensions: ['*'] }] : undefined
  }
  const owner = ownerOf(e)
  const res = owner ? await dialog.showOpenDialog(owner, opts) : await dialog.showOpenDialog(opts)
  return res.canceled ? null : (res.filePaths[0] ?? null)
}

function dragIcon(file: string, mime: string): Electron.NativeImage {
  if (mime.startsWith('image/')) {
    const img = nativeImage.createFromPath(file)
    if (!img.isEmpty()) {
      const { width, height } = img.getSize()
      return img.resize(width >= height ? { width: 64 } : { height: 64 })
    }
  }
  return nativeImage.createFromPath(appIconPath).resize({ width: 48 })
}

function registerAssetIpc(): void {
  handle('studio:asset:prepare', (_e, hash, name) => assets.export(checkHash(hash), checkName(name)))
  handle('studio:asset:saveAs', async (e, hash, name) => {
    const a = await assets.export(checkHash(hash), checkName(name))
    const ext = extname(a.name).slice(1)
    const opts: Electron.SaveDialogOptions = {
      title: 'Xuất tệp',
      defaultPath: join(app.getPath('downloads'), a.name),
      filters: ext ? [{ name: ext.toUpperCase(), extensions: [ext] }, { name: 'Tất cả', extensions: ['*'] }] : undefined
    }
    const owner = ownerOf(e)
    const res = owner ? await dialog.showSaveDialog(owner, opts) : await dialog.showSaveDialog(opts)
    if (res.canceled || !res.filePath) return null
    await copyFile(a.path, res.filePath)
    devlog.app('info', `exported ${a.hash} → ${res.filePath}`)
    return res.filePath
  })
  handle('studio:asset:reveal', async (_e, hash, name) => {
    shell.showItemInFolder((await assets.export(checkHash(hash), checkName(name))).path)
  })
  handle('studio:asset:copyPath', async (_e, hash, name) => {
    const a = await assets.export(checkHash(hash), checkName(name))
    clipboard.writeText(a.path)
    return a.path
  })
  handle('studio:asset:bundleOptions', (_e, stepId) => assets.bundleOptions(checkStep(stepId)))
  handle('studio:asset:saveBundle', async (e, stepId, preset, name) => {
    const b = await assets.exportBundle(checkStep(stepId), checkPreset(preset), checkName(name))
    const opts: Electron.OpenDialogOptions = {
      title: `Chọn nơi lưu thư mục “${b.name}”`,
      defaultPath: app.getPath('downloads'),
      properties: ['openDirectory', 'createDirectory']
    }
    const owner = ownerOf(e)
    const res = owner ? await dialog.showOpenDialog(owner, opts) : await dialog.showOpenDialog(opts)
    const parent = res.canceled ? undefined : res.filePaths[0]
    if (!parent) return null
    const dest = join(parent, basename(b.dir))
    await cp(b.dir, dest, { recursive: true, force: true })
    devlog.app('info', `exported step ${stepId} (${b.preset ?? 'raw'}, ${b.files} files) → ${dest}`)
    return dest
  })
  handle('studio:asset:revealBundle', async (_e, stepId, preset, name) => {
    const b = await assets.exportBundle(checkStep(stepId), checkPreset(preset), checkName(name))
    await shell.openPath(b.dir)
  })
  // startDrag must run while the OS drag gesture is live, so the renderer pre-warms the copy via prepare on hover.
  ipcMain.on('studio:asset:drag', (e, rawHash: unknown, rawName: unknown) => {
    if (!isTrustedSender(e.senderFrame?.url)) return
    let hash: string
    let name: string | undefined
    try {
      hash = checkHash(rawHash)
      name = checkName(rawName)
    } catch {
      return
    }
    const start = (a: { path: string; mime: string }): void => {
      if (!e.sender.isDestroyed()) e.sender.startDrag({ file: a.path, icon: dragIcon(a.path, a.mime) })
    }
    const hit = assets.cached(hash, name)
    if (hit) return start(hit)
    assets.export(hash, name).then(start, (err: Error) => devlog.app('warn', `drag ${hash}: ${err.message}`))
  })
}

const PROGRESS_MODES = new Set<ProgressMode>(['normal', 'paused', 'error', 'indeterminate'])
// Windows drops a toast's click handler once its Notification object is garbage-collected.
const liveNotifications = new Set<Notification>()
const ROUTE_RE = /^\/[\w\-/?=&%.]{0,200}$/

function registerShellIpc(): void {
  ipcMain.on('studio:setProgress', (e, value: unknown, mode: unknown) => {
    if (!isTrustedSender(e.senderFrame?.url)) return
    const w = ownerOf(e)
    if (!w) return
    const m = typeof mode === 'string' && PROGRESS_MODES.has(mode as ProgressMode) ? (mode as ProgressMode) : 'normal'
    if (value === null) return w.setProgressBar(-1)
    if (m === 'indeterminate') return w.setProgressBar(2, { mode: 'indeterminate' })
    if (typeof value !== 'number' || !Number.isFinite(value)) return
    w.setProgressBar(Math.min(1, Math.max(0, value)), { mode: m })
  })
  ipcMain.on('studio:notify', (e, raw: unknown) => {
    if (!isTrustedSender(e.senderFrame?.url) || !Notification.isSupported()) return
    const n = (raw ?? {}) as Partial<AppNotification>
    if (typeof n.title !== 'string' || typeof n.body !== 'string') return
    const w = ownerOf(e)
    if (w && w.isVisible() && w.isFocused()) return
    const route = typeof n.route === 'string' && ROUTE_RE.test(n.route) ? n.route : null
    const note = new Notification({ title: n.title.slice(0, 120), body: n.body.slice(0, 300), icon: appIconPath })
    liveNotifications.add(note)
    const drop = (): void => void liveNotifications.delete(note)
    note.on('click', () => {
      drop()
      showWindow()
      if (route) win?.webContents.send('studio:navigate', route)
    })
    note.on('close', drop)
    note.on('failed', drop)
    note.show()
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

app.on('child-process-gone', (_e, d) => {
  if (d.reason !== 'clean-exit') devlog.app('error', `${d.type} process gone: ${d.reason} (exit ${d.exitCode})`)
})

app.on('will-quit', () => {
  devlog.app('info', 'quit')
  devlog.close()
})

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
  if (!vibrancy) win?.setBackgroundColor(nativeTheme.shouldUseDarkColors ? '#1c1c1e' : '#f2f2f5')
})

void app.whenReady().then(() => {
  app.setAppUserModelId('studio.ai-asset')
  hardenSession()
  if (!isDev) serveAppProtocol()
  loadTheme()
  registerIpc()
  core.start()
  createWindow()
  createTray()
})

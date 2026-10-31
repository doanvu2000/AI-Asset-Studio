import { execFile } from 'child_process'
import { existsSync } from 'fs'
import { copyFile, cp, mkdir, readdir, readFile, rm, writeFile } from 'fs/promises'
import { homedir } from 'os'
import { join } from 'path'
import type { AgentInstallResult, AgentStatus, AgentTarget, AgentTargetStatus } from '../preload/types'
import type { CoreLaunch } from './core'

export const PLUGIN = 'asset-studio'
export const MARKETPLACE = 'asset-studio-local'
const SERVER = 'asset-studio'
const CLI_TIMEOUT_MS = 120_000
// Codex ends a tool call after 60 s by default; wait_run/generate legitimately block for many minutes.
const CODEX_TOOL_TIMEOUT_S = 1900
const BACKUPS_KEPT = 5

export interface AgentPaths {
  launch: CoreLaunch
  /** Shipped integrations/ (resources/integrations when packaged, <repo>/integrations in dev). */
  integrationsDir: string
  /** Writable folder for the stamped plugin copy and config backups. */
  workDir: string
}

const claudeHome = (): string => join(homedir(), '.claude')
const codexHome = (): string => process.env.CODEX_HOME || join(homedir(), '.codex')
const stamp = (): string => new Date().toISOString().replace(/[:.]/g, '-').slice(0, 23)

function run(cmd: string, args: string[]): Promise<{ ok: boolean; out: string }> {
  // npm installs Windows shims as .cmd, which only start through a shell; quote every argument ourselves.
  const shell = /\.(cmd|bat)$/i.test(cmd)
  const q = (s: string): string => (shell ? `"${s.replace(/"/g, '')}"` : s)
  return new Promise((resolve) => {
    execFile(shell ? q(cmd) : cmd, args.map(q), { shell, windowsHide: true, timeout: CLI_TIMEOUT_MS, encoding: 'utf8' }, (err, stdout, stderr) =>
      resolve({ ok: !err, out: `${stdout ?? ''}${stderr ?? ''}`.trim() })
    )
  })
}

async function findCli(name: string): Promise<string | null> {
  if (process.platform !== 'win32') {
    const r = await run('which', [name])
    return r.ok ? r.out.split(/\r?\n/)[0] || null : null
  }
  const r = await run('where.exe', [name])
  if (!r.ok) return null
  const hits = r.out.split(/\r?\n/).map((l) => l.trim()).filter(Boolean)
  return hits.find((h) => /\.(exe|cmd|bat)$/i.test(h)) ?? null
}

async function readJson(path: string): Promise<Record<string, unknown> | null> {
  try {
    return JSON.parse(await readFile(path, 'utf8')) as Record<string, unknown>
  } catch {
    return null
  }
}

async function backup(src: string, workDir: string, label: string): Promise<string | null> {
  if (!existsSync(src)) return null
  const dir = join(workDir, 'backups')
  await mkdir(dir, { recursive: true })
  const dst = join(dir, `${label}-${stamp()}`)
  await copyFile(src, dst)
  await pruneBackups(dir, label)
  return dst
}

export async function pruneBackups(dir: string, label: string, keep = BACKUPS_KEPT): Promise<string[]> {
  const prefix = `${label}-`
  const isStamp = (n: string): boolean => n.startsWith(prefix) && /^\d{4}-\d{2}-\d{2}T[\d-]+$/.test(n.slice(prefix.length))
  // The ISO stamp sorts chronologically as text; ~/.claude.json backups can be MBs each, so old ones must go.
  const mine = (await readdir(dir).catch(() => [] as string[])).filter(isStamp).sort()
  const stale = mine.slice(0, Math.max(0, mine.length - keep))
  await Promise.all(stale.map((n) => rm(join(dir, n), { force: true })))
  return stale
}

async function claudeStatus(): Promise<AgentTargetStatus> {
  const cli = await findCli('claude')
  const plugins = (await readJson(join(claudeHome(), 'plugins', 'installed_plugins.json')))?.plugins as Record<string, unknown> | undefined
  const plugin = !!plugins && `${PLUGIN}@${MARKETPLACE}` in plugins
  const servers = (await readJson(join(homedir(), '.claude.json')))?.mcpServers as Record<string, { command?: string }> | undefined
  const manual = servers?.[SERVER]
  return {
    cli,
    installed: plugin || !!manual,
    mode: plugin ? 'plugin' : manual ? 'mcp' : 'none',
    detail: plugin ? `plugin ${PLUGIN}@${MARKETPLACE}` : manual ? `MCP thủ công: ${manual.command ?? ''}` : '',
    configPath: join(claudeHome(), 'plugins', 'installed_plugins.json')
  }
}

const SECTION = /^\s*\[\s*mcp_servers\.("asset-studio"|asset-studio)\s*\]\s*$/

async function codexStatus(): Promise<AgentTargetStatus> {
  const cli = await findCli('codex')
  const configPath = join(codexHome(), 'config.toml')
  let text = ''
  try {
    text = await readFile(configPath, 'utf8')
  } catch {
    /* no config yet */
  }
  const lines = text.split(/\r?\n/)
  const at = lines.findIndex((l) => SECTION.test(l))
  const command = at >= 0 ? lines.slice(at + 1).find((l) => /^\s*command\s*=/.test(l))?.split('=').slice(1).join('=').trim() : undefined
  const skill = existsSync(join(codexHome(), 'skills', PLUGIN, 'SKILL.md'))
  return {
    cli,
    installed: at >= 0,
    mode: at >= 0 ? 'mcp' : 'none',
    detail: at >= 0 ? `config.toml: ${command ?? ''}${skill ? ' + skill' : ''}` : '',
    configPath,
    skill
  }
}

export async function agentStatus(): Promise<AgentStatus> {
  const [claude, codex] = await Promise.all([claudeStatus(), codexStatus()])
  return { claude, codex }
}

export function codexBlock(launch: CoreLaunch): string {
  // JSON strings are valid TOML basic strings (backslashes escaped), so any Windows path survives.
  return [
    `[mcp_servers.${SERVER}]`,
    `command = ${JSON.stringify(launch.command)}`,
    `args = ${JSON.stringify(launch.args).replace(/","/g, '", "')}`,
    'startup_timeout_sec = 90',
    `tool_timeout_sec = ${CODEX_TOOL_TIMEOUT_S}`,
    'default_tools_approval_mode = "approve"'
  ].join('\n')
}

export function upsertCodexSection(text: string, block: string): string {
  const eol = text.includes('\r\n') ? '\r\n' : '\n'
  const lines = text ? text.split(/\r?\n/) : []
  const at = lines.findIndex((l) => SECTION.test(l))
  if (at < 0) {
    const body = lines.join(eol).replace(/\s*$/, '')
    return `${body}${body ? eol + eol : ''}${block.split('\n').join(eol)}${eol}`
  }
  let end = at + 1
  // Stop at the next header, subtables included: a user's [mcp_servers.asset-studio.env] survives a reinstall.
  while (end < lines.length && !/^\s*\[/.test(lines[end])) end++
  while (end > at + 1 && lines[end - 1].trim() === '') end--
  return [...lines.slice(0, at), ...block.split('\n'), ...lines.slice(end)].join(eol)
}

export async function manualCommands(paths: AgentPaths): Promise<{ claude: string[]; codexToml: string }> {
  const dir = await stagePlugin(paths)
  return {
    claude: [`claude plugin marketplace add "${dir}"`, `claude plugin install ${PLUGIN}@${MARKETPLACE}`],
    codexToml: codexBlock(paths.launch)
  }
}

async function stagePlugin(paths: AgentPaths): Promise<string> {
  const dst = join(paths.workDir, 'claude-plugin')
  await rm(dst, { recursive: true, force: true })
  await mkdir(paths.workDir, { recursive: true })
  await cp(join(paths.integrationsDir, 'claude-plugin'), dst, { recursive: true })
  await rm(join(dst, 'mcp.dev.json'), { force: true })
  // The plugin is copied into Claude's cache on install, so the server command must be absolute, not plugin-relative.
  const mcp = { mcpServers: { [SERVER]: { command: paths.launch.command, args: paths.launch.args } } }
  await writeFile(join(dst, '.mcp.json'), JSON.stringify(mcp, null, 2) + '\n', 'utf8')
  return dst
}

async function installClaude(paths: AgentPaths): Promise<AgentInstallResult> {
  const log: string[] = []
  const cli = await findCli('claude')
  if (!cli) return { ok: false, log: ['Không thấy lệnh claude trong PATH. Cài Claude Code rồi thử lại.'], backups: [] }
  const backups = (
    await Promise.all([backup(join(claudeHome(), 'settings.json'), paths.workDir, 'claude-settings.json'), backup(join(homedir(), '.claude.json'), paths.workDir, 'claude.json')])
  ).filter((b): b is string => !!b)
  const dir = await stagePlugin(paths)
  log.push(`plugin: ${dir}`)
  const add = await run(cli, ['plugin', 'marketplace', 'add', dir])
  log.push(add.out)
  if (!add.ok && !/already/i.test(add.out)) return { ok: false, log, backups }
  log.push((await run(cli, ['plugin', 'marketplace', 'update', MARKETPLACE])).out)
  const inst = await run(cli, ['plugin', 'install', `${PLUGIN}@${MARKETPLACE}`, '--scope', 'user'])
  log.push(inst.out)
  if (!inst.ok && !/already/i.test(inst.out)) return { ok: false, log, backups }
  if (/already/i.test(inst.out)) log.push((await run(cli, ['plugin', 'update', `${PLUGIN}@${MARKETPLACE}`])).out)
  // A manual `claude mcp add asset-studio` would list every tool twice next to the plugin's server.
  const servers = (await readJson(join(homedir(), '.claude.json')))?.mcpServers as Record<string, unknown> | undefined
  if (servers?.[SERVER]) log.push((await run(cli, ['mcp', 'remove', SERVER, '-s', 'user'])).out)
  return { ok: true, log: log.filter(Boolean), backups }
}

async function installCodex(paths: AgentPaths): Promise<AgentInstallResult> {
  const configPath = join(codexHome(), 'config.toml')
  const backups = [await backup(configPath, paths.workDir, 'codex-config.toml')].filter((b): b is string => !!b)
  let text = ''
  try {
    text = await readFile(configPath, 'utf8')
  } catch {
    /* first config */
  }
  await mkdir(codexHome(), { recursive: true })
  await writeFile(configPath, upsertCodexSection(text, codexBlock(paths.launch)), 'utf8')
  const skillDst = join(codexHome(), 'skills', PLUGIN)
  await rm(skillDst, { recursive: true, force: true })
  await cp(join(paths.integrationsDir, 'codex', 'skills', PLUGIN), skillDst, { recursive: true })
  const log = [`config: ${configPath}`, `skill: ${skillDst}`]
  if (!(await findCli('codex'))) log.push('Chưa thấy lệnh codex trong PATH; cấu hình đã ghi sẵn, cài Codex CLI là dùng được.')
  return { ok: true, log, backups }
}

export function installAgent(target: AgentTarget, paths: AgentPaths): Promise<AgentInstallResult> {
  return target === 'claude' ? installClaude(paths) : installCodex(paths)
}

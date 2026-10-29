import { existsSync } from 'node:fs'
import { cp, mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises'
import { join } from 'node:path'

export type PluginEngine = 'unity' | 'godot'

export interface PluginInstallResult {
  engine: PluginEngine
  projectDir: string
  dest: string
}

const UNITY_PACKAGE = 'com.assetstudio.bridge'
const GODOT_PLUGIN_CFG = 'res://addons/asset_studio/plugin.cfg'

export function detectEngine(projectDir: string): PluginEngine | null {
  if (existsSync(join(projectDir, 'project.godot'))) return 'godot'
  if (existsSync(join(projectDir, 'Assets')) && existsSync(join(projectDir, 'ProjectSettings'))) return 'unity'
  return null
}

/** Adds the addon to `[editor_plugins] enabled=PackedStringArray(...)` without touching the rest of project.godot. */
export function enableGodotPlugin(text: string): string {
  const eol = text.includes('\r\n') ? '\r\n' : '\n'
  const lines = text.split(/\r?\n/)
  const head = lines.findIndex((l) => l.trim() === '[editor_plugins]')
  if (head < 0) {
    const body = text.endsWith(eol) || text === '' ? text : text + eol
    return `${body}${eol}[editor_plugins]${eol}${eol}enabled=PackedStringArray("${GODOT_PLUGIN_CFG}")${eol}`
  }
  let end = lines.findIndex((l, i) => i > head && l.trim().startsWith('['))
  if (end < 0) end = lines.length
  const at = lines.findIndex((l, i) => i > head && i < end && /^enabled\s*=/.test(l.trim()))
  if (at < 0) {
    lines.splice(head + 1, 0, '', `enabled=PackedStringArray("${GODOT_PLUGIN_CFG}")`)
    return lines.join(eol)
  }
  if (lines[at].includes(GODOT_PLUGIN_CFG)) return text
  const m = /PackedStringArray\((.*)\)/.exec(lines[at])
  const items = m && m[1].trim() ? `${m[1].trim()}, "${GODOT_PLUGIN_CFG}"` : `"${GODOT_PLUGIN_CFG}"`
  lines[at] = `enabled=PackedStringArray(${items})`
  return lines.join(eol)
}

async function writeAtomic(path: string, text: string): Promise<void> {
  const tmp = `${path}.${process.pid}.tmp`
  await writeFile(tmp, text, 'utf8')
  await rename(tmp, path)
}

/**
 * Copies the bundled Unity package / Godot addon into the project and records where the studio keeps its data
 * (the plugin reads the core's port + token from `<data>/core.lock.json` at run time; no token is written here).
 */
export async function installEnginePlugin(
  integrationsDir: string,
  projectDir: string,
  info: { dataDir: string; appExe: string }
): Promise<PluginInstallResult> {
  const engine = detectEngine(projectDir)
  if (!engine) throw new Error('Thư mục này không phải project Unity (Assets + ProjectSettings) hay Godot (project.godot)')
  const config = JSON.stringify({ data_dir: info.dataDir, app_exe: info.appExe }, null, 2) + '\n'
  if (engine === 'unity') {
    const src = join(integrationsDir, 'unity', UNITY_PACKAGE)
    if (!existsSync(src)) throw new Error(`Thiếu bản plugin Unity: ${src}`)
    const dest = join(projectDir, 'Packages', UNITY_PACKAGE)
    await rm(dest, { recursive: true, force: true })
    await cp(src, dest, { recursive: true })
    await mkdir(join(projectDir, 'UserSettings'), { recursive: true })
    await writeAtomic(join(projectDir, 'UserSettings', 'AssetStudioBridge.json'), config)
    return { engine, projectDir, dest }
  }
  const src = join(integrationsDir, 'godot', 'addons', 'asset_studio')
  if (!existsSync(src)) throw new Error(`Thiếu bản plugin Godot: ${src}`)
  const dest = join(projectDir, 'addons', 'asset_studio')
  await rm(dest, { recursive: true, force: true })
  await cp(src, dest, { recursive: true })
  await writeFile(join(dest, 'bridge_config.json'), config, 'utf8')
  const projectFile = join(projectDir, 'project.godot')
  const before = await readFile(projectFile, 'utf8')
  const after = enableGodotPlugin(before)
  if (after !== before) await writeAtomic(projectFile, after)
  return { engine, projectDir, dest }
}

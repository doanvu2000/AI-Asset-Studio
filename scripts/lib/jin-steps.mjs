import { spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const argv = process.argv.slice(2);
export const flag = (k) => argv.includes(k);
export const opt = (k, d) => {
  const eq = argv.find((a) => a.startsWith(`${k}=`));
  if (eq) return eq.slice(k.length + 1);
  return argv.includes(k) ? argv[argv.indexOf(k) + 1] : d;
};
export const threads = Number(process.env.JIN_MAX_THREADS) || Math.max(1, Math.floor(os.cpus().length * 0.8));
const VERBOSE = flag('--verbose');
const ONLY = opt('--only', '')?.split(',').filter(Boolean) || [];
const SKIP = opt('--skip', '')?.split(',').filter(Boolean) || [];
const MAX_LINES = Number(process.env.JIN_SCRIPT_MAX_LINES) || 20;
const DEFAULT_ERR = /\b(error|fail(ed|ure)?|exception|fatal)\b|\[E\]$|^\s*(✗|×|E\/)/i;
const KEEP_LOGS = 10;

function runCapped(cmd, args) {
  const capped = process.env.JIN_RUN_CAPPED || path.join(os.homedir(), 'bin', 'run-capped.ps1');
  if (process.platform !== 'win32' || !fs.existsSync(capped)) return [cmd, args];
  return ['pwsh', ['-NoProfile', '-File', capped, '-Exe', cmd, '-ArgList', ...args]];
}

function resolveWin(cmd) {
  if (path.extname(cmd) && fs.existsSync(cmd)) return cmd;
  const exts = (process.env.PATHEXT || '.EXE;.CMD;.BAT').split(';').map((e) => e.toLowerCase());
  const dirs = path.isAbsolute(cmd) || cmd.includes('/') || cmd.includes('\\') ? [''] : (process.env.PATH || '').split(path.delimiter);
  for (const d of dirs) for (const e of ['', ...exts]) {
    const p = d ? path.join(d, cmd + e) : cmd + e;
    if (e && fs.existsSync(p)) return p;
  }
  return cmd;
}

const quoteArg = (a) => {
  const s = String(a);
  if (s && !/[\s"&|<>^%()!]/.test(s)) return s;
  return `"${s.replace(/(\\*)"/g, '$1$1\\"').replace(/(\\*)$/, '$1$1')}"`.replace(/[&|<>^%()!]/g, '^$&');
};

function spawnPortable(cmd, args, opts) {
  if (process.platform !== 'win32') return spawn(cmd, args, opts);
  const file = resolveWin(cmd);
  if (/\.(cmd|bat)$/i.test(file)) {
    const line = [file, ...args].map(quoteArg).join(' ');
    return spawn(process.env.ComSpec || 'cmd.exe', ['/d', '/s', '/c', `"${line}"`], { ...opts, windowsVerbatimArguments: true });
  }
  return spawn(file, args, opts);
}

function exec(step, cwd, log) {
  return new Promise((resolve) => {
    const [cmd, args] = step.heavy ? runCapped(step.cmd, step.args || []) : [step.cmd, step.args || []];
    const child = spawnPortable(cmd, args, { cwd: step.cwd ? path.resolve(cwd, step.cwd) : cwd, env: { ...process.env, ...(step.env || {}) } });
    let out = '';
    const onData = (d) => {
      const s = d.toString();
      out += s;
      log.write(s);
      if (VERBOSE) process.stdout.write(s);
    };
    child.stdout.on('data', onData);
    child.stderr.on('data', onData);
    const timer = setTimeout(() => { out += '\n[timeout]'; child.kill(); }, (step.timeoutSec || 900) * 1000);
    child.on('error', (e) => { out += `\n${e.message}`; });
    child.on('close', (code) => { clearTimeout(timer); resolve({ code: code ?? 1, out }); });
  });
}

function pruneLogs(dir, prefix) {
  const files = fs.readdirSync(dir).filter((f) => f.startsWith(`${prefix}-`) && f.endsWith('.log')).sort();
  for (const f of files.slice(0, Math.max(0, files.length - KEEP_LOGS))) fs.rmSync(path.join(dir, f), { force: true });
}

export async function runSteps(name, steps, { root = process.cwd() } = {}) {
  const logDir = path.join(root, '.jin', 'logs');
  fs.mkdirSync(logDir, { recursive: true });
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15);
  const logFile = path.join(logDir, `${name}-${stamp}.log`);
  const log = fs.createWriteStream(logFile);
  const status = new Map();
  const lines = [];
  let budget = MAX_LINES - 2;

  for (const step of steps) {
    if ((ONLY.length && !ONLY.includes(step.name)) || SKIP.includes(step.name)) continue;
    const blocked = (step.needs || []).find((n) => status.get(n) === false);
    if (blocked) { lines.push(`-    ${step.name} (bỏ qua: ${blocked} FAIL)`); status.set(step.name, null); continue; }
    if (step.when && !(await step.when())) { status.set(step.name, null); continue; }
    log.write(`\n===== ${step.name}: ${step.cmd} ${(step.args || []).join(' ')}\n`);
    const t0 = Date.now();
    const r = step.run ? await step.run({ exec: (s) => exec(s, root, log), log }) : await exec(step, root, log);
    const ok = step.ok ? step.ok(r) : r.code === 0;
    status.set(step.name, ok);
    const secs = ((Date.now() - t0) / 1000).toFixed(1);
    const picked = step.summary ? step.summary(r, ok) : ok ? [] : pickErrors(r.out, step.errors);
    lines.push(`${ok ? 'OK  ' : 'FAIL'} ${step.name} (${secs}s)${picked.length && !ok ? ` · ${picked.length} dòng lỗi` : ''}`);
    for (const l of picked) {
      if (budget-- <= 0) break;
      lines.push(`     ${l}`);
    }
  }
  log.end();
  pruneLogs(logDir, name);
  const fails = [...status.values()].filter((v) => v === false).length;
  const ran = [...status.values()].filter((v) => v !== null).length;
  for (const l of lines.slice(0, MAX_LINES - 1)) console.log(l);
  console.log(`${fails ? 'FAIL' : 'OK'} ${ran - fails}/${ran} · log: ${path.relative(root, logFile).replace(/\\/g, '/')}`);
  process.exitCode = fails ? 1 : 0;
  return fails === 0;
}

export function pickErrors(out, re = DEFAULT_ERR, max = 8) {
  const seen = new Set();
  const res = [];
  for (const raw of String(out).split(/\r?\n|\r/)) {
    const l = raw.trim();
    if (!l || !re.test(l)) continue;
    const key = l.replace(/\d+/g, '#');
    if (seen.has(key)) continue;
    seen.add(key);
    res.push(l.length > 220 ? `${l.slice(0, 217)}...` : l);
    if (res.length >= max) break;
  }
  if (!res.length) res.push(...String(out).trim().split(/\r?\n/).slice(-3).map((l) => l.slice(0, 220)));
  return res;
}

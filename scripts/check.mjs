#!/usr/bin/env node
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runSteps, threads, flag, opt, pickErrors } from './lib/jin-steps.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const core = path.join(root, 'core');
const venvPy = path.join(core, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const python = fs.existsSync(venvPy) ? venvPy : 'python';
const maxLen = Number(opt('--max-len', '120'));

const git = (...a) => {
  try {
    return execFileSync('git', a, { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).split(/\r?\n/);
  } catch {
    return [];
  }
};

const isCorePy = (f) => f.startsWith('core/') && f.endsWith('.py') && fs.existsSync(path.join(root, f));

const addedLines = () => {
  const out = new Map();
  let file = null;
  let line = 0;
  for (const l of git('diff', '-U0', '--no-color', 'HEAD', '--', 'core')) {
    if (l.startsWith('+++ ')) file = l.startsWith('+++ b/') ? l.slice(6) : null;
    else if (l.startsWith('@@')) line = Number(/\+(\d+)/.exec(l)?.[1] || 0);
    else if (file && l.startsWith('+')) {
      if (!out.has(file)) out.set(file, []);
      out.get(file).push([line++, l.slice(1)]);
    }
  }
  for (const f of git('ls-files', '--others', '--exclude-standard', '--', 'core').filter(Boolean)) {
    if (isCorePy(f)) out.set(f, fs.readFileSync(path.join(root, f), 'utf8').split(/\r?\n/).map((t, i) => [i + 1, t]));
  }
  return out;
};

const longLines = () => {
  const hits = [];
  for (const [f, lines] of addedLines()) {
    if (!isCorePy(f)) continue;
    for (const [n, t] of lines) if (t.length > maxLen) hits.push(`${f}:${n}: ${t.length} ký tự`);
  }
  return hits;
};

const marker = flag('--slow') ? 'not live' : 'not slow and not live';
const testTargets = (opt('--tests', '') || '').split(',').filter(Boolean);
const PYTEST_ERR = /^(FAILED|ERROR) |^E\s+\S|^\S+\.py:\d+: \w*(Error|Exception)|\d+ failed|\d+ error/;
const pyStep = (args) => ({
  cmd: python, cwd: 'core', heavy: true, env: { PYTHONIOENCODING: 'utf-8' },
  args: ['-m', 'pytest', '-q', '--no-header', '--tb=line', ...args],
});
const mainArgs = ['-m', marker, '-n', String(threads), '-rfE', ...testTargets];

await runSteps('check', [
  {
    name: 'lines', cmd: 'node', args: [],
    run: async () => {
      const hits = longLines();
      return { code: hits.length ? 1 : 0, out: hits.join('\n') };
    },
    summary: (r, ok) => {
      if (ok) return [];
      const all = r.out.split('\n');
      return all.length > 6 ? [...all.slice(0, 6), `... +${all.length - 6}`] : all;
    },
  },
  {
    name: 'pytest', ...pyStep(mainArgs),
    run: async ({ exec }) => {
      const first = await exec(pyStep(mainArgs));
      if (first.code === 0 || flag('--no-rerun')) return first;
      const failed = [...first.out.matchAll(/^(?:FAILED|ERROR) (\S+::\S+)/gm)].map((m) => m[1]);
      if (!failed.length || failed.length > 10) return first;
      const again = await exec(pyStep(failed));
      if (again.code !== 0) return { code: first.code, out: `${first.out}\n${again.out}` };
      return { code: 0, out: first.out, flaky: failed };
    },
    summary: (r, ok) => {
      if (r.flaky) return r.flaky.map((t) => `FLAKY ${t}: fail khi chạy song song, pass khi chạy lại tuần tự`);
      return ok ? [] : pickErrors(r.out, PYTEST_ERR);
    },
  },
], { root });

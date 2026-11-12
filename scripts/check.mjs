#!/usr/bin/env node
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { runSteps, threads, flag, opt } from './lib/jin-steps.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const core = path.join(root, 'core');
const venvPy = path.join(core, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const python = fs.existsSync(venvPy) ? venvPy : 'python';
const maxLen = Number(opt('--max-len', '120'));

const git = (...a) => {
  try {
    return execFileSync('git', a, { cwd: root, encoding: 'utf8' }).split(/\r?\n/).filter(Boolean);
  } catch {
    return [];
  }
};

const changedPy = () => [...new Set([
  ...git('diff', '--name-only', 'HEAD'),
  ...git('ls-files', '--others', '--exclude-standard'),
])].filter((f) => f.startsWith('core/') && f.endsWith('.py') && fs.existsSync(path.join(root, f)));

const longLines = () => {
  const hits = [];
  for (const f of changedPy()) {
    fs.readFileSync(path.join(root, f), 'utf8').split(/\r?\n/).forEach((l, i) => {
      if (l.length > maxLen) hits.push(`${f}:${i + 1}: ${l.length} ký tự`);
    });
  }
  return hits;
};

const marker = flag('--slow') ? 'not live' : 'not slow and not live';
const testTargets = (opt('--tests', '') || '').split(',').filter(Boolean);

await runSteps('check', [
  {
    name: 'lines', cmd: 'node', args: [],
    run: async () => {
      const hits = longLines();
      return { code: hits.length ? 1 : 0, out: hits.join('\n') };
    },
    summary: (r, ok) => (ok ? [] : r.out.split('\n').slice(0, 6).concat(r.out.split('\n').length > 6 ? [`... +${r.out.split('\n').length - 6}`] : [])),
  },
  {
    name: 'pytest', cmd: python, cwd: 'core', heavy: true,
    args: ['-m', 'pytest', '-q', '-m', marker, '-n', String(threads), '--no-header', '-rfE', '--tb=line', ...testTargets],
    env: { PYTHONIOENCODING: 'utf-8' },
    errors: /^(FAILED|ERROR) |^E\s+\S|^\S+\.py:\d+: \w*(Error|Exception)|\d+ failed|\d+ error/,
  },
], { root });

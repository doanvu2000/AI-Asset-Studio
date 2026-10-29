<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watch } from 'vue'
import { ART } from '@/assets/art'
import { APP_VERSION } from '@/composables/changelog'
import { SPLASH_MAX_MS, SPLASH_MIN_MS, splashVisible } from '@/composables/splash'
import { useConnection } from '@/stores/connection'

const conn = useConnection()
const TITLE = [...'AI Asset Studio']
const SPARKS = [
  { x: -150, y: 40, d: 0, s: 6, c: 'gold' },
  { x: 140, y: 70, d: 0.35, s: 5, c: 'violet' },
  { x: -90, y: -110, d: 0.7, s: 4, c: 'violet' },
  { x: 110, y: -90, d: 0.2, s: 7, c: 'gold' },
  { x: -175, y: -30, d: 1.0, s: 4, c: 'gold' },
  { x: 180, y: -10, d: 0.55, s: 5, c: 'violet' },
  { x: -40, y: 120, d: 1.2, s: 5, c: 'gold' },
  { x: 60, y: 125, d: 0.85, s: 4, c: 'violet' }
]

const phase = computed(() => {
  if (conn.core.mock || conn.core.state === 'ready') return 'ready'
  if (conn.core.state === 'error' || conn.core.state === 'stopped') return 'error'
  return 'starting'
})
const statusText = computed(
  () => ({ ready: 'Core đã sẵn sàng', error: 'Core chưa khởi động được', starting: 'Đang khởi động core…' })[phase.value]
)

const started = performance.now()
let minTimer = 0
let maxTimer = 0
let minElapsed = false

function close(): void {
  window.clearTimeout(minTimer)
  window.clearTimeout(maxTimer)
  splashVisible.value = false
}

function maybeClose(): void {
  if (minElapsed && phase.value !== 'starting') close()
}

onMounted(() => {
  minTimer = window.setTimeout(() => {
    minElapsed = true
    maybeClose()
  }, Math.max(0, SPLASH_MIN_MS - (performance.now() - started)))
  // A slow core must not hold the app hostage: the banner keeps reporting its state after the splash.
  maxTimer = window.setTimeout(close, SPLASH_MAX_MS)
})
watch(phase, maybeClose)
onBeforeUnmount(() => {
  window.clearTimeout(minTimer)
  window.clearTimeout(maxTimer)
})
</script>

<template>
  <div class="splash" role="status" aria-live="polite" :aria-label="`AI Asset Studio — ${statusText}`">
    <div class="sp-stage">
      <div class="emblem">
        <span class="halo" />
        <span class="ring" />
        <span class="ring ring-2" />
        <span
          v-for="(p, i) in SPARKS"
          :key="i"
          class="spark"
          :class="p.c"
          :style="{ '--x': `${p.x}px`, '--y': `${p.y}px`, '--d': `${p.d}s`, '--s': `${p.s}px` }"
        />
        <div class="float">
          <div class="logo-wrap">
            <img class="logo" :src="ART.appLogo" alt="" width="160" height="160" draggable="false" />
            <span class="sheen" />
          </div>
        </div>
      </div>
      <h1 class="sp-title" aria-hidden="true">
        <span v-for="(ch, i) in TITLE" :key="i" class="ch" :style="{ '--i': i }">{{ ch === ' ' ? ' ' : ch }}</span>
      </h1>
      <p class="tagline">Xưởng asset game chạy ngay trên máy</p>
      <div class="status" :class="phase">
        <span class="dot" />
        <span class="status-text">{{ statusText }}</span>
      </div>
      <div class="bar" :class="phase"><span /></div>
    </div>
    <footer class="copyright">Copyright by Jin Vũ<span class="ver">v{{ APP_VERSION }}</span></footer>
  </div>
</template>

<style scoped>
.splash {
  --sp-bg: #ffffff;
  --violet: #8b5cf6;
  --gold: #e0a33a;
  --halo-a: rgba(139, 92, 246, 0.28);
  --halo-b: rgba(224, 163, 58, 0.18);
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: grid;
  place-items: center;
  background: var(--sp-bg);
  color: var(--text);
  user-select: none;
  -webkit-app-region: drag;
  overflow: hidden;
}
@media (prefers-color-scheme: dark) {
  .splash {
    --sp-bg: #000000;
    --halo-a: rgba(139, 92, 246, 0.38);
    --halo-b: rgba(224, 163, 58, 0.2);
  }
}

.sp-stage {
  display: flex;
  flex-direction: column;
  align-items: center;
  transform: translateY(-14px);
}

.emblem {
  position: relative;
  width: 200px;
  height: 200px;
  display: grid;
  place-items: center;
}
.halo {
  position: absolute;
  inset: -70px;
  border-radius: 50%;
  background: radial-gradient(circle closest-side, var(--halo-a) 0%, var(--halo-b) 45%, transparent 100%);
  animation:
    halo-in 1.3s var(--ease-out) 0.1s both,
    breathe 3.2s ease-in-out 1.4s infinite;
}
.ring {
  position: absolute;
  inset: 20px;
  border-radius: 50%;
  border: 2px solid var(--violet);
  opacity: 0;
  animation: ripple 1.3s var(--ease-out) 0.7s both;
}
.ring-2 {
  border-color: var(--gold);
  animation-delay: 0.95s;
}
.spark {
  position: absolute;
  left: 50%;
  top: 50%;
  width: var(--s);
  height: var(--s);
  margin: calc(var(--s) / -2) 0 0 calc(var(--s) / -2);
  border-radius: 50%;
  opacity: 0;
  animation: spark 2.6s var(--ease-out) calc(0.9s + var(--d)) infinite;
}
.spark.gold {
  background: var(--gold);
  box-shadow: 0 0 10px var(--gold);
}
.spark.violet {
  background: var(--violet);
  box-shadow: 0 0 10px var(--violet);
}

.float {
  animation: float 3.4s ease-in-out 1.3s infinite;
}
.logo-wrap {
  position: relative;
  width: 160px;
  height: 160px;
  border-radius: 36px;
  overflow: hidden;
  box-shadow:
    0 18px 40px -12px rgba(76, 29, 149, 0.45),
    0 4px 12px rgba(0, 0, 0, 0.12);
  animation: pop 1s var(--ease-spring) 0.15s both;
}
.logo {
  display: block;
  width: 100%;
  height: 100%;
}
.sheen {
  position: absolute;
  inset: 0;
  background: linear-gradient(105deg, transparent 35%, rgba(255, 255, 255, 0.65) 50%, transparent 65%);
  transform: translateX(-120%);
  animation: sheen 1s ease-in-out 1.35s both;
  pointer-events: none;
}

.sp-title {
  margin: 34px 0 0;
  font-size: 40px;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.1;
  display: flex;
}
.ch {
  display: inline-block;
  animation: rise 0.7s var(--ease-spring) calc(0.75s + var(--i) * 0.04s) both;
}
.tagline {
  margin: 10px 0 0;
  font-size: var(--fs-title);
  color: var(--text-2);
  animation: fade-up 0.7s var(--ease-out) 1.45s both;
}

.status {
  margin-top: 34px;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--fs-callout);
  color: var(--text-3);
  font-variant-numeric: tabular-nums;
  animation: fade-up 0.6s var(--ease-out) 1.8s both;
}
.status .dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--violet);
  animation: blink 1s ease-in-out infinite;
  transition: background var(--t-base) var(--ease-out);
}
.status.ready .dot {
  background: #30a46c;
  animation: none;
}
.status.error .dot {
  background: #e5484d;
  animation: none;
}
.status-text {
  transition: color var(--t-base) var(--ease-out);
}
.status.ready .status-text {
  color: var(--text-2);
}

.bar {
  position: relative;
  margin-top: 12px;
  width: 180px;
  height: 3px;
  border-radius: 3px;
  background: var(--separator);
  overflow: hidden;
  animation: fade-up 0.6s var(--ease-out) 1.85s both;
}
.bar span {
  position: absolute;
  inset: 0 auto 0 0;
  width: 40%;
  border-radius: inherit;
  background: linear-gradient(90deg, var(--violet), var(--gold));
  animation: scan 1.3s ease-in-out infinite;
}
.bar.ready span,
.bar.error span {
  width: 100%;
  animation: none;
  transform: none;
  transition: width 0.5s var(--ease-out);
}
.bar.error span {
  background: #e5484d;
}

.copyright {
  position: absolute;
  bottom: 22px;
  left: 0;
  right: 0;
  display: flex;
  justify-content: center;
  gap: 10px;
  font-size: var(--fs-caption);
  color: var(--text-3);
  letter-spacing: 0.02em;
  animation: fade-in 1s var(--ease-out) 1.2s both;
}
.ver {
  opacity: 0.7;
}

@keyframes halo-in {
  from {
    opacity: 0;
    transform: scale(0.4);
  }
}
@keyframes breathe {
  50% {
    transform: scale(1.08);
    opacity: 0.85;
  }
}
@keyframes ripple {
  0% {
    opacity: 0.55;
    transform: scale(0.7);
  }
  100% {
    opacity: 0;
    transform: scale(1.9);
  }
}
@keyframes spark {
  0% {
    opacity: 0;
    transform: translate(calc(var(--x) * 0.4), calc(var(--y) * 0.4)) scale(0.4);
  }
  25% {
    opacity: 1;
  }
  100% {
    opacity: 0;
    transform: translate(var(--x), calc(var(--y) - 40px)) scale(1);
  }
}
@keyframes pop {
  0% {
    opacity: 0;
    transform: translateY(28px) scale(0.55) rotate(-10deg);
    filter: blur(14px);
  }
  60% {
    opacity: 1;
    filter: blur(0);
    transform: translateY(-4px) scale(1.06) rotate(2deg);
  }
  100% {
    transform: none;
  }
}
@keyframes float {
  50% {
    transform: translateY(-6px);
  }
}
@keyframes sheen {
  to {
    transform: translateX(120%);
  }
}
@keyframes rise {
  from {
    opacity: 0;
    transform: translateY(0.55em);
    filter: blur(6px);
  }
}
@keyframes fade-up {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
}
@keyframes fade-in {
  from {
    opacity: 0;
  }
}
@keyframes blink {
  50% {
    opacity: 0.35;
  }
}
@keyframes scan {
  from {
    transform: translateX(-100%);
  }
  to {
    transform: translateX(250%);
  }
}
</style>

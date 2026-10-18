import '@fontsource-variable/inter'
import './styles/tokens.css'
import './styles/base.css'
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { autoAnimatePlugin } from '@formkit/auto-animate/vue'
import App from './App.vue'
import { router } from './router'
import { installDevlog } from './composables/devlog'

installDevlog()

const params = new URLSearchParams(location.search)
document.documentElement.dataset.vibrancy = params.get('vibrancy') === '1' ? '1' : '0'
document.documentElement.dataset.platform = params.get('platform') ?? window.studio?.platform ?? 'web'

createApp(App).use(createPinia()).use(router).use(autoAnimatePlugin).mount('#app')

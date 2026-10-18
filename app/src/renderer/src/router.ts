import { createRouter, createWebHashHistory } from 'vue-router'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: () => import('./views/DashboardView.vue') },
    { path: '/new', name: 'new', component: () => import('./views/NewRunView.vue') },
    { path: '/runs', name: 'runs', component: () => import('./views/RunsView.vue') },
    { path: '/runs/:id', name: 'run', component: () => import('./views/RunDetailView.vue'), props: true },
    { path: '/gates', name: 'gates', component: () => import('./views/GatesView.vue') },
    { path: '/gallery', name: 'gallery', component: () => import('./views/GalleryView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' }
  ]
})

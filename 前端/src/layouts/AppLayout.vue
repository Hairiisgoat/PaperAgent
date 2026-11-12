 <script setup lang="ts">
import { Collection, Files } from '@element-plus/icons-vue'
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <div class="app-shell" :class="{ 'reading-layout': route.name === 'workflow-run' }">
    <aside class="sidebar" aria-label="主导航">
      <RouterLink class="brand" to="/projects">
        <span class="brand-mark"><Files aria-hidden="true" /></span>
        <span><strong>PaperAgent</strong><small>论文工作台</small></span>
      </RouterLink>
      <div class="nav-heading">工作空间</div>
      <nav>
        <RouterLink class="nav-link" :class="{ 'nav-link-active': route.path === '/projects' || route.path.startsWith('/projects/') }" to="/projects">
          <Collection aria-hidden="true" /><span>我的项目</span>
        </RouterLink>
      </nav>
      <div class="sidebar-note"><span class="note-dot"></span>个人工作空间</div>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <div class="breadcrumb"><span>工作空间</span><span class="slash">/</span><strong>{{ route.meta.title || '我的项目' }}</strong></div>
        <span class="topbar-label">PAPER WORKSPACE</span>
      </header>
      <main class="main-content"><slot /></main>
    </div>
  </div>
</template>

<style scoped>
.app-shell { min-height: 100vh; display: flex; }
.sidebar { width: 228px; min-height: 100vh; padding: 30px 18px 22px; flex: 0 0 228px; display: flex; flex-direction: column; background: #102b40; color: #d8e5ef; }
.brand { display: flex; align-items: center; gap: 12px; padding: 0 8px; color: #fff; text-decoration: none; }
.brand-mark { display: grid; place-items: center; width: 38px; height: 38px; border: 1px solid #6c92a8; border-radius: 10px; background: #234b64; }
.brand-mark svg { width: 21px; height: 21px; }
.brand strong { display: block; font-size: 17px; letter-spacing: -.02em; }
.brand small { display: block; margin-top: 4px; color: #a8bfce; font-size: 11px; }
.nav-heading { margin: 56px 12px 12px; color: #8ba7b9; font-size: 11px; letter-spacing: .12em; }
.nav-link { display: flex; align-items: center; gap: 12px; padding: 12px; border-radius: 8px; color: #bed0dd; font-size: 14px; text-decoration: none; }
.nav-link svg { width: 17px; height: 17px; }
.nav-link-active { color: #fff; background: #274e65; font-weight: 600; }
.sidebar-note { margin-top: auto; padding: 20px 12px 0; border-top: 1px solid #355265; color: #a8bfce; font-size: 12px; }
.note-dot { display: inline-block; width: 7px; height: 7px; margin-right: 8px; border-radius: 50%; background: #8db6bc; }
.workspace { flex: 1; min-width: 0; }
.topbar { height: 70px; padding: 0 40px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #e5ebef; background: #fff; }
.breadcrumb { display: flex; align-items: center; gap: 12px; color: #8594a0; font-size: 13px; }
.breadcrumb strong { color: #344d5e; font-weight: 600; }
.slash { color: #cad4da; }
.topbar-label { font-size: 10px; letter-spacing: .14em; color: #9baab4; }
.main-content { max-width: 1600px; margin: 0 auto; padding: 44px 48px 72px; }
@media (max-width: 900px) { .sidebar { width: 180px; flex-basis: 180px; } .main-content { padding: 32px 24px; } .topbar { padding: 0 24px; } }
@media (max-width: 640px) { .app-shell { display: block; } .sidebar { width: 100%; min-height: auto; padding: 14px 18px; flex-direction: row; align-items: center; justify-content: space-between; } .brand small, .nav-heading, .sidebar-note { display: none; } .nav-link { padding: 8px; } .topbar { height: 52px; } .topbar-label { display: none; } .main-content { padding: 28px 16px; } }
</style>
<style scoped>
.sidebar { background:#17382a; color:#dcebe0; }
.brand-mark { background:#315b42; border-color:#6b9279; }
.nav-heading,.sidebar-note,.brand small { color:#aac5b0; }
.nav-link { color:#c6dbca; }
.nav-link-active { background:#315c40; color:white; }
.sidebar-note { border-color:#41614b; }
.main-content { background:#f5f7f4; }
.reading-layout .main-content { padding:20px 28px 60px; }
@media(max-width:640px) { .reading-layout .main-content { padding:16px 14px 40px; } }
</style>

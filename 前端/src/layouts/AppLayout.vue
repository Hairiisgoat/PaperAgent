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
        <span class="brand-text"><strong>PaperAgent</strong><small>论文工作台</small></span>
      </RouterLink>
      <div class="nav-heading">工作空间</div>
      <nav class="nav-list">
        <RouterLink class="nav-link" :class="{ 'nav-link-active': route.path === '/projects' || route.path.startsWith('/projects/') }" to="/projects">
          <Collection aria-hidden="true" /><span>我的项目</span>
        </RouterLink>
      </nav>
      <div class="sidebar-note"><span class="note-dot"></span>个人工作空间</div>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <div class="breadcrumb"><span>工作空间</span><span class="slash">/</span><strong>{{ route.meta.title || '我的项目' }}</strong></div>
      </header>
      <main class="main-content"><slot /></main>
    </div>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  display: flex;
  background: var(--pa-bg-page);
  color: var(--pa-text-body);
}

/* ---------- 极简浅色侧边栏（Linear / Notion 风） ---------- */
.sidebar {
  width: 220px;
  min-height: 100vh;
  padding: 20px 14px 16px;
  flex: 0 0 220px;
  display: flex;
  flex-direction: column;
  background: var(--pa-bg-sidebar);
  border-right: var(--pa-border);
  color: var(--pa-text-body);
  position: sticky;
  top: 0;
  height: 100vh;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;
  border-radius: var(--pa-radius-control);
  color: var(--pa-color-heading);
  text-decoration: none;
  transition: background-color 0.15s;
}

.brand:hover {
  background: var(--pa-bg-hover);
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  border-radius: 7px;
  background: var(--pa-color-heading);
  color: var(--pa-bg-surface);
  box-shadow: var(--pa-shadow-card);
  flex: none;
}

.brand-mark svg {
  width: 16px;
  height: 16px;
}

.brand-text {
  min-width: 0;
}

.brand strong {
  display: block;
  font-size: 14px;
  font-weight: 600;
  color: var(--pa-color-heading);
  letter-spacing: -0.01em;
  line-height: 1.25;
}

.brand small {
  display: block;
  margin-top: 2px;
  color: var(--pa-text-meta);
  font-size: 11px;
  line-height: 1.2;
}

.nav-heading {
  margin: 24px 10px 8px;
  color: var(--pa-text-faint);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.06em;
}

.nav-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.nav-link {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: var(--pa-radius-control);
  color: var(--pa-text-body);
  font-size: 13px;
  font-weight: 500;
  text-decoration: none;
  transition: background-color 0.15s, color 0.15s;
}

.nav-link:hover {
  background: var(--pa-bg-hover);
  color: var(--pa-color-heading);
}

.nav-link svg {
  width: 16px;
  height: 16px;
  color: var(--pa-text-meta);
  flex: none;
}

.nav-link-active {
  background: var(--pa-bg-nav-active);
  color: var(--el-color-primary);
  font-weight: 600;
}

.nav-link-active:hover {
  background: var(--pa-bg-nav-active);
  color: var(--el-color-primary);
}

.nav-link-active svg {
  color: var(--el-color-primary);
}

.sidebar-note {
  margin-top: auto;
  padding: 14px 10px 0;
  border-top: var(--pa-border);
  color: var(--pa-text-meta);
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.note-dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--pa-color-success-dot);
}

/* ---------- 右侧主工作区（100vh 撑满，无断层） ---------- */
.workspace {
  flex: 1;
  min-width: 0;
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--pa-bg-page);
}

.topbar {
  height: 52px;
  padding: 0 32px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: var(--pa-border);
  background: var(--pa-bg-surface);
  flex: none;
}

.breadcrumb {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--pa-text-meta);
  font-size: 13px;
}

.breadcrumb strong {
  color: var(--pa-color-heading);
  font-weight: 600;
}

.slash {
  color: var(--pa-border-color);
}

.main-content {
  flex: 1;
  width: 100%;
  max-width: 1440px;
  margin: 0 auto;
  padding: 28px 32px 56px;
  background: var(--pa-bg-page);
}

.reading-layout .main-content {
  max-width: 1600px;
  padding: 20px 28px 56px;
}

@media (max-width: 900px) {
  .sidebar { width: 188px; flex-basis: 188px; }
  .main-content { padding: 24px 20px 44px; }
  .topbar { padding: 0 20px; }
}

@media (max-width: 640px) {
  .app-shell { flex-direction: column; }
  .sidebar {
    width: 100%;
    height: auto;
    min-height: auto;
    position: static;
    padding: 10px 16px;
    flex-direction: row;
    align-items: center;
    justify-content: space-between;
    border-right: 0;
    border-bottom: var(--pa-border);
  }
  .brand small, .nav-heading, .sidebar-note { display: none; }
  .nav-link { padding: 6px 10px; }
  .topbar { height: 44px; padding: 0 16px; }
  .main-content, .reading-layout .main-content { padding: 20px 16px 40px; }
}
</style>

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import { router } from './router'
// 样式加载顺序：Element Plus 基础变量 → 全局 Reset → 设计系统主题（必须最后，以覆盖 EP 默认 token）
import 'element-plus/theme-chalk/base.css'
import './assets/main.css'
import './styles/theme.css'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')

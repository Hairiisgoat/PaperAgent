import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import ProjectListView from '../views/ProjectListView.vue'
import ProjectEditView from '../views/ProjectEditView.vue'
import ProjectCreateView from '../views/ProjectCreateView.vue'
import ProjectDetailView from '../views/ProjectDetailView.vue'
import WorkflowRunView from '../views/WorkflowRunView.vue'

export const routes: RouteRecordRaw[] = [
  { path: '/', redirect: '/projects' },
  { path: '/projects', name: 'projects', component: ProjectListView, meta: { title: '我的项目' } },
  { path: '/projects/new', name: 'project-create', component: ProjectCreateView, meta: { title: '创建项目' } },
  { path: '/projects/:projectId', name: 'project-detail', component: ProjectDetailView, meta: { title: '项目详情' } },
  { path: '/projects/:projectId/edit', name: 'project-edit', component: ProjectEditView, meta: { title: '编辑项目' } },
  { path: '/projects/:projectId/runs/:runId', name: 'workflow-run', component: WorkflowRunView, meta: { title: '工作流与论文' } },
]

export const router = createRouter({ history: createWebHistory(), routes })

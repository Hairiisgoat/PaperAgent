import type { WorkflowEvent } from '../types/workflows'

const statuses: Record<string, string> = { queued: '排队中', running: '运行中', waiting_human: '等待人工审核', paused: '已暂停', failed: '运行失败', completed: '已完成', cancelled: '已取消' }
const stages: Record<string, string> = { planning: '任务规划', research: '资料检索', outline: '大纲', writing: '章节写作', review: '审查', export: '导出' }
const messages: Record<string, string> = { 'Workflow queued': '工作流已排队', 'Workflow running': '工作流正在运行', 'Planner completed': '任务规划已完成', 'Waiting for approval': '等待人工审核', 'Workflow completed': '工作流已完成', 'Approval rejected': '审核已拒绝', 'Planner approval complete; workflow paused': '任务审核完成，工作流已暂停', 'Source approval complete; workflow paused': '资料审核完成，工作流已暂停' }
export const runStatusLabel = (status: string): string => statuses[status] ?? '未知状态'
export const stageLabel = (stage: string | null): string => stage ? stages[stage] ?? '未知阶段' : '尚未开始'
export function eventLabel(event: WorkflowEvent): string {
  switch (event.event_type) {
    case 'progress': return messages[event.payload.message] ?? '阶段状态更新'
    case 'approval_required': return event.payload.approval_type === 'task_approval' ? '等待任务审核' : '等待资料审核'
    case 'text_delta': return '写作内容片段'
    case 'completed': return '运行完成事件'
    case 'error': return '运行错误'
    case 'heartbeat': return '连接正常'
  }
}

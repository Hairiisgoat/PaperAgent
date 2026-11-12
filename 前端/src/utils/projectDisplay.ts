const paperTypes = new Map([['course_paper', '课程论文'], ['thesis', '毕业论文'], ['literature_review', '文献综述'], ['research_paper', '学术研究论文']])
const lifecycleStates = new Map([['active', '进行中'], ['archived', '已归档'], ['deleted', '已删除']])
const progressStates = new Map([['not_started', '未开始'], ['in_progress', '进行中'], ['completed', '已完成']])

export const paperTypeLabel = (value: string): string => paperTypes.get(value) ?? '未知类型'
export const lifecycleLabel = (value: string): string => lifecycleStates.get(value) ?? '未知状态'
export const progressLabel = (value: string): string => progressStates.get(value) ?? '未知进度'

const timeFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})
export function formatLocalTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : timeFormatter.format(date)
}

/** Matches the backend's whitespace handling and field constraints. */
export type EditableField = 'title' | 'topic' | 'citation_style'
export const editableFields: EditableField[] = ['title', 'topic', 'citation_style']
export function trimProjectText(value: string): string {
  return value.replace(/^\p{White_Space}+|\p{White_Space}+$/gu, '')
}
export function validateProjectField(field: EditableField, raw: string): string {
  const value = trimProjectText(raw)
  if (!value) return '请填写此项'
  if (field === 'title' && Array.from(value).length > 200) return '项目名称不能超过 200 个字符'
  if (field === 'citation_style' && Array.from(value).length > 40) return '引用格式不能超过 40 个字符'
  if (field === 'topic' && new TextEncoder().encode(value).length > 65535) return '论文主题不能超过 65,535 UTF-8 字节'
  return ''
}

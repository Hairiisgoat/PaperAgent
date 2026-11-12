export interface SSEFrame { event: string; data: string; id?: string }

/** Incremental UTF-8 SSE parser. Only blank lines dispatch an event. */
export class SSEParser {
  private decoder = new TextDecoder('utf-8')
  private buffer = ''
  private data: string[] = []
  private eventName = ''
  private id: string | undefined
  constructor(private readonly onFrame: (frame: SSEFrame) => void) {}

  push(chunk: Uint8Array): void {
    this.buffer += this.decoder.decode(chunk, { stream: true })
    this.consume(false)
  }
  finish(): void {
    this.buffer += this.decoder.decode()
    this.consume(true)
    // Per SSE semantics, a partial event at EOF is discarded.
    this.buffer = ''; this.data = []; this.eventName = ''; this.id = undefined
  }
  private consume(eof: boolean): void {
    while (true) {
      const index = this.buffer.search(/[\r\n]/)
      if (index < 0) break
      if (this.buffer[index] === '\r' && index === this.buffer.length - 1 && !eof) break
      const line = this.buffer.slice(0, index)
      const length = this.buffer[index] === '\r' && this.buffer[index + 1] === '\n' ? 2 : 1
      this.buffer = this.buffer.slice(index + length)
      this.line(line)
    }
    if (this.buffer.length > 65536 || this.data.reduce((sum, item) => sum + item.length, 0) > 65536) throw new Error('SSE event too large')
  }
  private line(line: string): void {
    if (line === '') {
      if (this.data.length) this.onFrame({ event: this.eventName || 'message', data: this.data.join('\n'), ...(this.id === undefined ? {} : { id: this.id }) })
      this.data = []; this.eventName = ''; this.id = undefined
      return
    }
    if (line.startsWith(':')) return
    const colon = line.indexOf(':')
    const field = colon < 0 ? line : line.slice(0, colon)
    let value = colon < 0 ? '' : line.slice(colon + 1)
    if (value.startsWith(' ')) value = value.slice(1)
    if (field === 'data') this.data.push(value)
    else if (field === 'event') this.eventName = value
    else if (field === 'id' && !value.includes('\0')) this.id = value
  }
}

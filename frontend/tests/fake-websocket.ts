import type { WebSocketLike } from "../src/websocket";

export class FakeWebSocket implements WebSocketLike {
  binaryType: BinaryType = "blob";
  readyState = 0;
  bufferedAmount = 0;
  readonly sent: Array<string | ArrayBufferLike | Blob | ArrayBufferView> = [];
  closeCode: number | undefined;
  closeReason: string | undefined;
  private readonly listeners = new Map<string, Set<EventListener>>();

  addEventListener(type: string, listener: EventListener): void {
    const registered = this.listeners.get(type) ?? new Set<EventListener>();
    registered.add(listener);
    this.listeners.set(type, registered);
  }

  removeEventListener(type: string, listener: EventListener): void {
    this.listeners.get(type)?.delete(listener);
  }

  send(data: string | ArrayBufferLike | Blob | ArrayBufferView): void {
    this.sent.push(data);
  }

  close(code?: number, reason?: string): void {
    this.closeCode = code;
    this.closeReason = reason;
    this.readyState = 3;
  }

  open(): void {
    this.readyState = 1;
    this.dispatch("open", new Event("open"));
  }

  message(data: unknown): void {
    this.dispatch("message", new MessageEvent("message", { data }));
  }

  error(): void {
    this.dispatch("error", new Event("error"));
  }

  remoteClose(code = 1000, reason = "closed"): void {
    this.readyState = 3;
    this.dispatch("close", new CloseEvent("close", { code, reason }));
  }

  private dispatch(type: string, event: Event): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

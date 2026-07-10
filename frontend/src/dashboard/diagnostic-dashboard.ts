import type { ServerMessage } from "../protocol";
import type {
  GestureWebSocketClient,
  GestureWebSocketClientEvent,
  WebSocketClientState,
} from "../websocket";

const MAXIMUM_LOG_ENTRIES = 50;

export class DiagnosticDashboard {
  private readonly status: HTMLOutputElement;
  private readonly connectButton: HTMLButtonElement;
  private readonly disconnectButton: HTMLButtonElement;
  private readonly pingButton: HTMLButtonElement;
  private readonly resetButton: HTMLButtonElement;
  private readonly messages: HTMLOListElement;
  private readonly unsubscribe: () => void;

  constructor(
    private readonly root: HTMLElement,
    readonly client: GestureWebSocketClient,
  ) {
    this.root.innerHTML = `
      <main class="diagnostic-dashboard" aria-labelledby="dashboard-title">
        <header>
          <p class="eyebrow">GestureBoard Pro</p>
          <h1 id="dashboard-title">Protocol diagnostics</h1>
          <output class="connection-status" aria-live="polite" aria-atomic="true"></output>
        </header>
        <section aria-labelledby="connection-controls-title">
          <h2 id="connection-controls-title">Connection</h2>
          <p class="connection-url"></p>
          <div class="controls">
            <button type="button" data-action="connect">Connect</button>
            <button type="button" data-action="disconnect">Disconnect</button>
            <button type="button" data-action="ping">Send ping</button>
            <button type="button" data-action="reset">Reset runtime</button>
          </div>
        </section>
        <section aria-labelledby="message-log-title">
          <h2 id="message-log-title">Message log</h2>
          <ol class="message-log" aria-live="polite" aria-relevant="additions"></ol>
        </section>
      </main>`;

    this.status = this.element(".connection-status");
    this.connectButton = this.element('[data-action="connect"]');
    this.disconnectButton = this.element('[data-action="disconnect"]');
    this.pingButton = this.element('[data-action="ping"]');
    this.resetButton = this.element('[data-action="reset"]');
    this.messages = this.element(".message-log");
    this.element<HTMLParagraphElement>(".connection-url").textContent =
      this.client.url;

    this.connectButton.addEventListener("click", () => void this.connect());
    this.disconnectButton.addEventListener("click", () =>
      this.client.disconnect(),
    );
    this.pingButton.addEventListener("click", () =>
      this.sendControl(() => this.client.sendPing()),
    );
    this.resetButton.addEventListener("click", () =>
      this.sendControl(() => this.client.resetRuntime()),
    );
    this.unsubscribe = this.client.subscribe((event) =>
      this.handleEvent(event),
    );
    this.renderState(this.client.getState());
  }

  destroy(): void {
    this.unsubscribe();
    this.root.replaceChildren();
  }

  private async connect(): Promise<void> {
    try {
      await this.client.connect();
    } catch (error) {
      this.append(
        "Connection failed",
        error instanceof Error ? error.message : String(error),
      );
    }
  }

  private sendControl(action: () => void): void {
    try {
      action();
    } catch (error) {
      this.append(
        "Control failed",
        error instanceof Error ? error.message : String(error),
      );
    }
  }

  private handleEvent(event: GestureWebSocketClientEvent): void {
    switch (event.type) {
      case "state.changed":
        this.renderState(event.state);
        break;
      case "protocol.message":
        this.append(event.message.type, this.messageSummary(event.message));
        break;
      case "protocol.error":
      case "socket.error":
        this.append(event.error.code, event.error.message);
        break;
      case "socket.closed":
        this.append(
          "socket.closed",
          `${event.code}: ${event.reason || "No reason supplied"}`,
        );
        break;
    }
  }

  private renderState(state: WebSocketClientState): void {
    const connected = state === "OPEN";
    this.status.textContent = `Connection state: ${state}`;
    this.connectButton.disabled =
      state === "CONNECTING" || connected || state === "CLOSING";
    this.disconnectButton.disabled = state === "IDLE" || state === "CLOSED";
    this.pingButton.disabled = !connected;
    this.resetButton.disabled = !connected;
  }

  private append(title: string, detail: string): void {
    const entry = document.createElement("li");
    const heading = document.createElement("strong");
    heading.textContent = title;
    entry.append(heading, ` — ${detail}`);
    this.messages.prepend(entry);
    while (this.messages.children.length > MAXIMUM_LOG_ENTRIES) {
      this.messages.lastElementChild?.remove();
    }
  }

  private messageSummary(message: ServerMessage): string {
    if (message.type === "gesture.result") {
      return `Sequence ${message.sequence}; gesture ${message.gesture.label ?? "none"}.`;
    }
    if (message.type === "error") return message.error.message;
    return "request_id" in message && message.request_id
      ? `Request ${message.request_id}`
      : "Received";
  }

  private element<T extends Element = HTMLElement>(selector: string): T {
    const element = this.root.querySelector<T>(selector);
    if (!element) throw new Error(`Dashboard element not found: ${selector}`);
    return element;
  }
}

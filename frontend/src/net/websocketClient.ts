// WebSocket client with auto-reconnect (spec §22.4 — must survive backend restart).

import type { ImpactMsg, RallyMsg, ServerMsg, StateMsg } from "../types";

type StateHandler = (s: StateMsg) => void;
type ImpactHandler = (i: ImpactMsg) => void;
type RallyHandler = (r: RallyMsg) => void;
type StatusHandler = (connected: boolean) => void;

export class WebSocketClient {
  private ws: WebSocket | null = null;
  private url: string;
  private reconnectMs = 500;
  private onStateCb: StateHandler = () => {};
  private onImpactCb: ImpactHandler = () => {};
  private onRallyCb: RallyHandler = () => {};
  private onStatusCb: StatusHandler = () => {};

  constructor(path = "/ws") {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.url = `${proto}://${location.host}${path}`;
  }

  onState(cb: StateHandler): void { this.onStateCb = cb; }
  onImpact(cb: ImpactHandler): void { this.onImpactCb = cb; }
  onRally(cb: RallyHandler): void { this.onRallyCb = cb; }
  onStatus(cb: StatusHandler): void { this.onStatusCb = cb; }

  connect(): void {
    try {
      this.ws = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws.onopen = () => {
      this.reconnectMs = 500;
      this.onStatusCb(true);
    };
    this.ws.onclose = () => {
      this.onStatusCb(false);
      this.scheduleReconnect();
    };
    this.ws.onerror = () => this.ws?.close();
    this.ws.onmessage = (ev) => {
      let msg: ServerMsg;
      try {
        msg = JSON.parse(ev.data as string);
      } catch {
        return;
      }
      if (msg.type === "state") this.onStateCb(msg);
      else if (msg.type === "impact") this.onImpactCb(msg);
      else if (msg.type === "rally") this.onRallyCb(msg);
    };
  }

  send(command: string, value?: string): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "command", command, value }));
    }
  }

  /** Mouse control sample: position + swing velocity, wrist tilt/yaw, strike, stance flip. */
  sendInput(input: {
    x?: number;
    y?: number;
    vx?: number;
    vy?: number;
    tilt?: number;
    yaw?: number;
    strike?: boolean;
    power?: number;
    flip?: boolean;
  }): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: "input", ...input }));
    }
  }

  private scheduleReconnect(): void {
    setTimeout(() => this.connect(), this.reconnectMs);
    this.reconnectMs = Math.min(this.reconnectMs * 1.5, 4000);
  }
}

import { applyRealtimeMessage, applySnapshot, setWsStatus } from "../store/rehabStore";
import { isRealtimeMessage } from "../contracts/realtimeMessages";
import { fetchCurrentState } from "./stateApi";

export class RehabWebSocketClient {
  private socket: WebSocket | null = null;
  private reconnectTimer: number | null = null;
  private stopped = false;
  private attempt = 0;

  start() {
    this.stopped = false;
    this.connect();
  }

  stop() {
    this.stopped = true;
    if (this.reconnectTimer) window.clearTimeout(this.reconnectTimer);
    this.socket?.close();
  }

  private connect() {
    setWsStatus("connecting");
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws/live`;
    this.socket = new WebSocket(url);
    this.socket.onopen = () => {
      this.attempt = 0;
      setWsStatus("open");
      fetchCurrentState().then((data) => {
        if (data.snapshot) applySnapshot(data.snapshot);
      }).catch(() => undefined);
    };
    this.socket.onmessage = (event) => {
      const parsed: unknown = JSON.parse(event.data);
      if (isRealtimeMessage(parsed)) applyRealtimeMessage(parsed);
    };
    this.socket.onclose = () => {
      setWsStatus("closed");
      if (!this.stopped) this.scheduleReconnect();
    };
    this.socket.onerror = () => {
      setWsStatus("closed");
      this.socket?.close();
    };
  }

  private scheduleReconnect() {
    const delay = Math.min(5000, 500 * 2 ** this.attempt);
    this.attempt += 1;
    this.reconnectTimer = window.setTimeout(() => this.connect(), delay);
  }
}

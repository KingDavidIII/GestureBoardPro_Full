import { CameraController, CanvasFrameEncoder } from "./camera";
import { websocketUrl } from "./config/environment";
import "./dashboard/dashboard.css";
import { DiagnosticDashboard } from "./dashboard";
import { GestureWebSocketClient } from "./websocket";
import { FrameStreamController } from "./streaming";

const root = document.querySelector<HTMLElement>("#app");
if (!root) throw new Error("The dashboard root element is missing.");

const client = new GestureWebSocketClient(websocketUrl());
const camera = new CameraController();
const encoder = new CanvasFrameEncoder();
const stream = new FrameStreamController(camera, encoder, client);
new DiagnosticDashboard(root, client, {
  camera,
  stream,
  jpegQuality: encoder.jpegQuality,
  maximumFrameWidth: encoder.maximumWidth,
});

const shutdown = (): void => {
  stream.stop();
  camera.stop();
  client.disconnect();
};
addEventListener("pagehide", shutdown);
addEventListener("beforeunload", shutdown);

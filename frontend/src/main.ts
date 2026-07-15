import { CameraController, CanvasFrameEncoder } from "./camera";
import { websocketUrl } from "./config/environment";
import "./dashboard/dashboard.css";
import { DiagnosticDashboard } from "./dashboard";
import { createRecognitionEventComposition } from "./application/application-composition";
import { RecognitionStateStore } from "./recognition";
import { GestureWebSocketClient } from "./websocket";
import {
  AdaptiveQualityController,
  AdaptiveQualityCoordinator,
  AdaptiveResolutionController,
  AdaptiveResolutionCoordinator,
  AdaptiveStreamController,
  AdaptiveStreamCoordinator,
  BandwidthEstimator,
  FrameStreamController,
} from "./streaming";

const root = document.querySelector<HTMLElement>("#app");
if (!root) throw new Error("The dashboard root element is missing.");

const client = new GestureWebSocketClient(websocketUrl());
const recognition = new RecognitionStateStore();
const recognitionComposition = createRecognitionEventComposition(recognition);
const camera = new CameraController();
const encoder = new CanvasFrameEncoder();
const stream = new FrameStreamController(camera, encoder, client);
const adaptive = new AdaptiveStreamCoordinator(
  new AdaptiveStreamController({ maximumFps: stream.targetFps }),
  stream,
  client,
);
const adaptiveQuality = new AdaptiveQualityCoordinator(
  new AdaptiveQualityController({ initialQuality: stream.jpegQuality }),
  stream,
  client,
);
const adaptiveResolution = new AdaptiveResolutionCoordinator(
  new AdaptiveResolutionController(),
  new BandwidthEstimator(),
  stream,
  client,
  adaptiveQuality.controller.policy.minimumQuality,
);
const dashboard = new DiagnosticDashboard(root, client, {
  camera,
  stream,
  adaptive,
  adaptiveQuality,
  adaptiveResolution,
  recognition,
  jpegQuality: encoder.jpegQuality,
  maximumFrameWidth: encoder.maximumWidth,
});

const recognitionUnsubscribe = client.subscribe(
  recognitionComposition.handleSocketEvent,
);
const streamRecognitionUnsubscribe = stream.subscribe(
  recognitionComposition.handleStreamEvent,
);

const shutdown = (): void => {
  dashboard.destroy();
  recognitionUnsubscribe();
  streamRecognitionUnsubscribe();
  recognitionComposition.destroy();
  adaptive.destroy();
  adaptiveQuality.destroy();
  adaptiveResolution.destroy();
  stream.stop();
  camera.stop();
  client.destroy();
};
addEventListener("pagehide", shutdown);
addEventListener("beforeunload", shutdown);

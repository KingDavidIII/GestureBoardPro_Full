import { websocketUrl } from "./config/environment";
import "./dashboard/dashboard.css";
import { DiagnosticDashboard } from "./dashboard";
import { GestureWebSocketClient } from "./websocket";

const root = document.querySelector<HTMLElement>("#app");
if (!root) throw new Error("The dashboard root element is missing.");

new DiagnosticDashboard(root, new GestureWebSocketClient(websocketUrl()));

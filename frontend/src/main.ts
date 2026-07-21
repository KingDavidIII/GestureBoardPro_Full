import { createApplicationRuntime } from "./application/application-runtime";
import "./dashboard/dashboard.css";

const root = document.querySelector<HTMLElement>("#app");
if (!root) throw new Error("The dashboard root element is missing.");

createApplicationRuntime(root);

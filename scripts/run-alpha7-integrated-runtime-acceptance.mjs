import { createConnection, createServer } from "node:net";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const python =
  process.env.PYTHON ??
  (process.platform === "win32"
    ? path.join(root, ".venv", "Scripts", "python.exe")
    : "python3");
const npm = process.platform === "win32" ? "npm.cmd" : "npm";
const host = "127.0.0.1";
const timeoutMs = 15_000;

const once = (child, event) =>
  new Promise((resolve) => child.once(event, resolve));

const reservePort = () =>
  new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, host, () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        reject(new Error("Could not reserve a loopback port."));
        return;
      }
      server.close((error) => (error ? reject(error) : resolve(address.port)));
    });
  });

const waitForListening = (port) =>
  new Promise((resolve, reject) => {
    const deadline = setTimeout(
      () => reject(new Error(`Daphne did not start on ${host}:${port}.`)),
      timeoutMs,
    );
    const attempt = () => {
      const socket = createConnection({ host, port });
      socket.once("connect", () => {
        socket.destroy();
        clearTimeout(deadline);
        resolve();
      });
      socket.once("error", () => {
        socket.destroy();
        setTimeout(attempt, 100);
      });
    };
    attempt();
  });

const terminate = async (child) => {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill();
  await Promise.race([
    once(child, "exit"),
    new Promise((resolve) => setTimeout(resolve, 5_000)),
  ]);
  if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
};

const port = await reservePort();
const server = spawn(
  python,
  ["-m", "daphne", "-b", host, "-p", String(port), "config.asgi:application"],
  { cwd: path.join(root, "backend"), stdio: "inherit" },
);
server.once("error", (error) => {
  console.error("Could not start Daphne:", error);
});

let stopping = false;
const stop = async (exitCode) => {
  if (stopping) return;
  stopping = true;
  await terminate(server);
  process.exitCode = exitCode;
};
process.once("SIGINT", () => void stop(130));
process.once("SIGTERM", () => void stop(143));

try {
  await waitForListening(port);
  const test = spawn(
    npm,
    ["run", "test:run", "--", "tests/integrated-runtime.acceptance.test.ts"],
    {
      cwd: path.join(root, "frontend"),
      stdio: "inherit",
      shell: process.platform === "win32",
      env: {
        ...process.env,
        GESTUREBOARD_ACCEPTANCE_WS_URL: `ws://${host}:${port}/ws/`,
      },
    },
  );
  const result = await once(test, "exit");
  await stop(typeof result === "number" ? result : 1);
} catch (error) {
  console.error("Alpha 7 integrated runtime launcher failed:", error);
  await stop(1);
}

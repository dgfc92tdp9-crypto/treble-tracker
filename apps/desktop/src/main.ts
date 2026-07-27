// The desktop client's command loop (spec §5.2).
//
// It parses nothing and resolves nothing: a line goes to TAPI, a resolved
// buffer comes back, the shared renderer draws it. Every piece of screen
// semantics lives behind that boundary, which is why this file knows no
// mnemonics and has no data logic to get wrong.
import { renderHtml, type LayoutTree } from "../../../treble/render/web/src/renderer";

const TAPI = "http://127.0.0.1:8756";

interface CommandResponse {
  kind: string;
  status: string;
  buffer: LayoutTree | null;
}

const screen = document.getElementById("screen") as HTMLElement;
const statusLine = document.getElementById("status") as HTMLElement;
const input = document.getElementById("command-line") as HTMLInputElement;

function setStatus(text: string, isError = false): void {
  statusLine.textContent = text;
  statusLine.classList.toggle("error", isError);
}

/** Wait for the sidecar. The shell starts the server as it opens the
 *  window, so the first paint can legitimately precede a ready backend —
 *  that is a slow start, not an error, and must not be shown as one. */
async function waitForBackend(timeoutMs = 30000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${TAPI}/health`);
      if (response.ok) return true;
    } catch {
      // Not up yet.
    }
    setStatus("starting the data service…");
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  return false;
}

async function execute(line: string): Promise<void> {
  if (!line.trim()) return;
  try {
    const response = await fetch(`${TAPI}/command`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ line }),
    });
    if (!response.ok) {
      setStatus(`TAPI returned ${response.status}`, true);
      return;
    }
    const result: CommandResponse = await response.json();
    // §5.2/§20.2: a command that resolves nothing still gets an explanation,
    // and the previous screen stays up rather than being blanked.
    setStatus(result.status, result.buffer === null && result.status !== "");
    if (result.buffer !== null) {
      screen.innerHTML = renderHtml(result.buffer);
      bindLinks();
    }
  } catch (error) {
    setStatus(`cannot reach the data service: ${String(error)}`, true);
  }
}

/** §5.4: a linked cell is a command, so clicking it must do exactly what
 *  typing it would. */
function bindLinks(): void {
  for (const cell of screen.querySelectorAll<HTMLElement>(".cell[data-command]")) {
    cell.addEventListener("click", () => {
      const command = cell.dataset["command"];
      if (command) {
        input.value = command;
        void execute(command);
        input.value = "";
      }
    });
  }
}

input.addEventListener("keydown", (event: KeyboardEvent) => {
  // Enter is <GO>. Nothing happens until it is pressed — the command line
  // is editable state until submitted.
  if (event.key === "Enter") {
    const line = input.value;
    input.value = "";
    void execute(line);
  }
});

// Keep focus on the command line: this is a keyboard-first workstation.
document.addEventListener("click", () => input.focus());

void (async () => {
  const ready = await waitForBackend();
  if (!ready) {
    setStatus("the data service did not start — run `treble serve` and reopen", true);
    return;
  }
  setStatus("ready  ·  try: IBM US Equity DES");
})();

import { api, ApiError } from "../api";
export type { RadioTransmission as Transmission } from "../../../../contracts/generated/radio_transmission";
import type { RadioSessionSnapshot as Snapshot } from "../../../../contracts/generated/radio_session_snapshot";
export type { Snapshot };
const eventTypes = [
  "radio.accepted",
  "radio.extraction_started",
  "radio.extraction_retrying",
  "radio.extracted",
  "radio.location_resolved",
  "radio.location_unresolved",
  "radio.failed",
];
// Snapshot replacement makes repeated events harmless and keeps multi-observation
// reports in one card. Open the stream at the cursor of the same snapshot.
export function subscribeFeed(
  session: string,
  changed: (snapshot: Snapshot) => void,
  status: (value: string) => void,
) {
  let stopped = false,
    loading = false,
    dirty = false;
  let source: EventSource | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let failures = 0;
  const refresh = async () => {
    if (stopped) return;
    if (loading) {
      dirty = true;
      return;
    }
    loading = true;
    try {
      const snapshot = await api<Snapshot>(
        `/api/demo-sessions/${session}/snapshot`,
      );
      if (stopped) return;
      changed(snapshot);
      failures = 0;
      if (!source) {
        source = new EventSource(
          `/api/demo-sessions/${session}/events?cursor=${snapshot.event_cursor ?? "0"}`,
        );
        source.onopen = () => status("Live feed connected");
        for (const type of eventTypes)
          source.addEventListener(type, () => {
            void refresh();
          });
        source.onerror = () => {
          source?.close();
          source = undefined;
          status("Reconnecting · polling every 5 seconds");
        };
      }
    } catch (error) {
      if (stopped) return;
      failures++;
      if (error instanceof ApiError && [401, 403].includes(error.status)) {
        status(
          "Session expired or inaccessible. Create or join a session again.",
        );
        stopped = true;
        source?.close();
        return;
      }
      status("Connection unavailable · retrying");
    } finally {
      loading = false;
      if (!stopped) {
        clearTimeout(timer);
        if (dirty) {
          dirty = false;
          void refresh();
        } else
          timer = setTimeout(
            () => {
              void refresh();
            },
            Math.min(30_000, 5000 * Math.max(1, failures)),
          );
      }
    }
  };
  void refresh();
  return () => {
    stopped = true;
    source?.close();
    clearTimeout(timer);
  };
}

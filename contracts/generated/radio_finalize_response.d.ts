/* Generated from JSON Schema. Run npm run contracts:generate. Do not edit. */

/**
 * Stable acknowledgement returned after a finalized transmission and its extraction job are durably accepted.
 */
export interface RadioFinalizeResponse {
  schema_version: "2.0";
  input_id: string;
  transmission_id: string;
  processing_state:
    "accepted" | "extracting" | "retrying" | "extracted" | "resolving" | "resolved" | "unresolved" | "failed";
  accepted_at: string;
}

/* Generated from JSON Schema. Run npm run contracts:generate. Do not edit. */

/**
 * A complete, client-finalized radio transcript. Session and responder authority are derived from server state.
 */
export interface RadioFinalizeRequest {
  schema_version: "2.1";
  transmission_id: string;
  transcript: string;
  provider_session_reference?: string;
  capture_started_at: string;
  capture_ended_at: string;
  /**
   * @minItems 1
   * @maxItems 1000
   */
  segments: Segment[];
}
export interface Segment {
  sequence: number;
  text: string;
  started_at_ms: number;
  ended_at_ms: number;
  provider_segment_id?: string;
}

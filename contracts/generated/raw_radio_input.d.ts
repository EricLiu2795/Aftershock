/* Generated from JSON Schema. Run npm run contracts:generate. Do not edit. */

export interface RawRadioInput {
  schema_version: "2.1";
  input_id: string;
  timestamp: string;
  audio_reference?: string | null;
  transcript: string;
  channel?: string | null;
  speaker_id?: string | null;
  metadata?: {
    [k: string]: unknown;
  };
}

import type { AppConfig, RawRadioInput } from "./types.js";
import { ExtractionService } from "./extraction.js";
import { cachedRiversideObservation } from "./replay.js";
import { Storage, type JobRecord } from "./storage.js";

export class ExtractionWorker {
  private timer?: NodeJS.Timeout;
  private running = false;
  private stopped = false;

  constructor(
    private readonly storage: Storage,
    private readonly extraction: ExtractionService,
    private readonly config: AppConfig,
  ) {}

  start(): void {
    this.stopped = false;
    this.schedule(0);
  }

  stop(): void {
    this.stopped = true;
    if (this.timer) clearTimeout(this.timer);
  }

  async runOnce(): Promise<boolean> {
    if (this.running) return false;
    for (const session of this.storage.all<{ id: string }>(
      "SELECT id FROM sessions WHERE expires_at <= ?",
      new Date().toISOString(),
    )) {
      this.storage.deleteSession(session.id);
    }
    const job = this.storage.claimJob(this.config.extractionLeaseMs);
    if (!job) return false;
    this.running = true;
    try {
      await this.process(job);
    } finally {
      this.running = false;
    }
    return true;
  }

  private schedule(delay: number): void {
    if (this.stopped) return;
    this.timer = setTimeout(async () => {
      try {
        await this.runOnce();
      } finally {
        this.schedule(this.config.extractionPollMs);
      }
    }, delay);
    this.timer.unref();
  }

  private async process(job: JobRecord): Promise<void> {
    const row = this.storage.one<{
      payload_json: string;
      scenario_id: string;
      capture_mode: string;
    }>(
      `SELECT r.payload_json, s.scenario_id, t.capture_mode
       FROM raw_inputs r
       JOIN transmissions t ON t.id = r.transmission_id
       JOIN sessions s ON s.id = t.session_id
       WHERE r.input_id = ?`,
      job.input_id,
    );
    if (!row) return this.fail(job, new Error("RAW_INPUT_MISSING"));
    const raw = JSON.parse(row.payload_json) as RawRadioInput;

    this.storage.transaction(() => {
      this.storage.run(
        "UPDATE transmissions SET status = 'extracting', updated_at = ? WHERE id = ?",
        new Date().toISOString(),
        job.transmission_id,
      );
      this.storage.appendEvent(
        job.session_id,
        job.transmission_id,
        "radio.extraction_started",
        {
          extraction_id: job.id,
          processing_state: "extracting",
        },
      );
    });

    try {
      const result =
        row.capture_mode === "cached_observation"
          ? cachedRiversideObservation(raw.transcript)
          : await this.extraction.extract(raw);
      const observations = result.observations.map((item) =>
        this.extraction.buildObservation(item, raw, result),
      );
      const resolutions = observations.map((observation) =>
        this.extraction.resolve(observation, row.scenario_id),
      );
      const finalState =
        observations.length === 0
          ? "extracted"
          : resolutions.every((item) => item.resolution_status === "resolved")
            ? "resolved"
            : "unresolved";
      const now = new Date().toISOString();
      const extractionEnvelope = {
        schema_version: "2.0",
        extraction_id: job.id,
        input_id: job.input_id,
        transmission_id: job.transmission_id,
        result_status:
          result.result === "observations"
            ? "extracted"
            : "no_actionable_content",
        observations: result.observations,
        completed_at: now,
        prompt_version: result.prompt_version,
        model_reference: result.model,
      };

      if (
        !this.storage.one("SELECT 1 FROM extraction_jobs WHERE id = ?", job.id)
      )
        return;
      this.storage.transaction(() => {
        for (const observation of observations) {
          this.storage.run(
            `INSERT OR IGNORE INTO observations
             (observation_id, transmission_id, job_id, payload_json, created_at)
             VALUES(?, ?, ?, ?, ?)`,
            observation.observation_id,
            job.transmission_id,
            job.id,
            JSON.stringify(observation),
            now,
          );
        }
        for (const resolution of resolutions) {
          this.storage.run(
            `INSERT OR IGNORE INTO resolutions
             (observation_id, transmission_id, payload_json, created_at)
             VALUES(?, ?, ?, ?)`,
            resolution.observation_id,
            job.transmission_id,
            JSON.stringify(resolution),
            now,
          );
        }
        this.storage.run(
          `UPDATE extraction_jobs
           SET status = 'completed', lease_until = NULL, adapter = ?, model = ?,
               prompt_version = ?, extraction_result_json = ?, updated_at = ?
           WHERE id = ?`,
          result.adapter,
          result.model,
          result.prompt_version,
          JSON.stringify(extractionEnvelope),
          now,
          job.id,
        );
        this.storage.run(
          "UPDATE transmissions SET status = ?, error_code = NULL, updated_at = ? WHERE id = ?",
          finalState,
          now,
          job.transmission_id,
        );
        this.storage.appendEvent(
          job.session_id,
          job.transmission_id,
          "radio.extracted",
          {
            extraction_result: extractionEnvelope,
          },
        );
        for (const resolution of resolutions) {
          this.storage.appendEvent(
            job.session_id,
            job.transmission_id,
            resolution.resolution_status === "resolved"
              ? "radio.location_resolved"
              : "radio.location_unresolved",
            { observation_id: resolution.observation_id, resolution },
          );
        }
      });
    } catch (error) {
      this.fail(job, error);
    }
  }

  private fail(job: JobRecord, error: unknown): void {
    if (!this.storage.one("SELECT 1 FROM extraction_jobs WHERE id = ?", job.id))
      return;
    const message =
      error instanceof Error ? error.message : "UNKNOWN_EXTRACTION_ERROR";
    const code = message.split(":")[0]!.slice(0, 80);
    const retry = job.attempts < this.config.extractionMaxAttempts;
    const now = new Date().toISOString();
    const failure = {
      code,
      message: "Radio extraction failed",
      retryable: retry,
    };
    this.storage.transaction(() => {
      this.storage.run(
        `UPDATE extraction_jobs
         SET status = ?, lease_until = NULL, error_code = ?,
             extraction_result_json = ?, updated_at = ?
         WHERE id = ?`,
        retry ? "queued" : "failed",
        code,
        retry
          ? null
          : JSON.stringify({
              schema_version: "2.0",
              extraction_id: job.id,
              input_id: job.input_id,
              transmission_id: job.transmission_id,
              result_status: "failed",
              observations: [],
              completed_at: now,
              prompt_version: "radio-semantic-v1",
              model_reference: "unavailable",
              failure,
            }),
        now,
        job.id,
      );
      this.storage.run(
        "UPDATE transmissions SET status = ?, error_code = ?, updated_at = ? WHERE id = ?",
        retry ? "retrying" : "failed",
        code,
        now,
        job.transmission_id,
      );
      if (retry) {
        this.storage.appendEvent(
          job.session_id,
          job.transmission_id,
          "radio.extraction_retrying",
          {
            extraction_id: job.id,
            attempt: job.attempts,
            max_attempts: this.config.extractionMaxAttempts,
            processing_state: "retrying",
            failure,
          },
        );
      } else {
        this.storage.appendEvent(
          job.session_id,
          job.transmission_id,
          "radio.failed",
          {
            stage: "extraction",
            failure,
          },
        );
      }
    });
  }
}

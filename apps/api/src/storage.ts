import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { DatabaseSync, type SQLInputValue } from "node:sqlite";
import { randomUUID } from "node:crypto";

export interface EventRecord {
  sequence: number;
  event_id: string;
  session_id: string;
  transmission_id: string | null;
  occurred_at: string;
  type: string;
  payload: unknown;
}

export interface JobRecord {
  id: string;
  transmission_id: string;
  session_id: string;
  input_id: string;
  attempts: number;
}

export class Storage {
  readonly db: DatabaseSync;
  readonly path: string;

  constructor(path: string) {
    this.path = path;
    if (path !== ":memory:") mkdirSync(dirname(path), { recursive: true });
    this.db = new DatabaseSync(path);
    this.db.exec(
      "PRAGMA foreign_keys = ON; PRAGMA journal_mode = WAL; PRAGMA busy_timeout = 5000;",
    );
    this.migrate();
  }

  close(): void {
    this.db.close();
  }

  transaction<T>(work: () => T): T {
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const result = work();
      this.db.exec("COMMIT");
      return result;
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
  }

  one<T>(sql: string, ...params: SQLInputValue[]): T | undefined {
    return this.db.prepare(sql).get(...params) as T | undefined;
  }

  all<T>(sql: string, ...params: SQLInputValue[]): T[] {
    return this.db.prepare(sql).all(...params) as T[];
  }

  run(sql: string, ...params: SQLInputValue[]): void {
    this.db.prepare(sql).run(...params);
  }

  appendEvent(
    sessionId: string,
    transmissionId: string | null,
    type: string,
    payload: unknown,
  ): EventRecord {
    const eventId = randomUUID();
    const occurredAt = new Date().toISOString();
    const result = this.db
      .prepare(
        `INSERT INTO events(event_id, session_id, transmission_id, occurred_at, type, payload_json)
         VALUES(?, ?, ?, ?, ?, ?)`,
      )
      .run(
        eventId,
        sessionId,
        transmissionId,
        occurredAt,
        type,
        JSON.stringify(payload),
      );
    return {
      sequence: Number(result.lastInsertRowid),
      event_id: eventId,
      session_id: sessionId,
      transmission_id: transmissionId,
      occurred_at: occurredAt,
      type,
      payload,
    };
  }

  listEvents(sessionId: string, after: number, limit = 250): EventRecord[] {
    return this.all<{
      sequence: number;
      event_id: string;
      session_id: string;
      transmission_id: string | null;
      occurred_at: string;
      type: string;
      payload_json: string;
    }>(
      `SELECT sequence, event_id, session_id, transmission_id, occurred_at, type, payload_json
       FROM events WHERE session_id = ? AND sequence > ? ORDER BY sequence LIMIT ?`,
      sessionId,
      after,
      limit,
    ).map(({ payload_json, ...event }) => ({
      ...event,
      payload: JSON.parse(payload_json),
    }));
  }

  claimJob(leaseMs: number): JobRecord | undefined {
    return this.transaction(() => {
      const now = new Date().toISOString();
      const job = this.one<JobRecord>(
        `SELECT id, transmission_id, session_id, input_id, attempts
         FROM extraction_jobs
         WHERE status = 'queued' OR (status = 'leased' AND lease_until < ?)
         ORDER BY created_at LIMIT 1`,
        now,
      );
      if (!job) return undefined;
      const leaseUntil = new Date(Date.now() + leaseMs).toISOString();
      this.run(
        `UPDATE extraction_jobs
         SET status = 'leased', lease_until = ?, attempts = attempts + 1, updated_at = ?
         WHERE id = ?`,
        leaseUntil,
        now,
        job.id,
      );
      return { ...job, attempts: job.attempts + 1 };
    });
  }

  deleteSession(sessionId: string): boolean {
    return this.transaction(() => {
      if (!this.one("SELECT 1 FROM sessions WHERE id = ?", sessionId))
        return false;
      const transmissionIds =
        "SELECT id FROM transmissions WHERE session_id = ?";
      this.run(
        `DELETE FROM resolutions WHERE transmission_id IN (${transmissionIds})`,
        sessionId,
      );
      this.run(
        `DELETE FROM observations WHERE transmission_id IN (${transmissionIds})`,
        sessionId,
      );
      this.run("DELETE FROM token_issuances WHERE session_id = ?", sessionId);
      this.run("DELETE FROM events WHERE session_id = ?", sessionId);
      this.run("DELETE FROM extraction_jobs WHERE session_id = ?", sessionId);
      this.run(
        `DELETE FROM raw_inputs WHERE transmission_id IN (${transmissionIds})`,
        sessionId,
      );
      this.run("DELETE FROM transmissions WHERE session_id = ?", sessionId);
      this.run("DELETE FROM participants WHERE session_id = ?", sessionId);
      this.run("DELETE FROM external_reports WHERE session_id = ?", sessionId);
      this.run("DELETE FROM report_locations WHERE session_id = ?", sessionId);
      this.run(
        "DELETE FROM operational_belief_sessions WHERE session_id = ?",
        sessionId,
      );
      this.run("DELETE FROM sessions WHERE id = ?", sessionId);
      return true;
    });
  }

  private migrate(): void {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        scenario_id TEXT NOT NULL,
        name TEXT NOT NULL,
        join_code_hash TEXT NOT NULL UNIQUE,
        allowed_responders_json TEXT NOT NULL,
        presenter_auth_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        join_expires_at TEXT NOT NULL,
        ended_at TEXT,
        ended_by_role TEXT CHECK(ended_by_role IN ('presenter', 'participant'))
      );
      CREATE TABLE IF NOT EXISTS participants (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id),
        responder_id TEXT NOT NULL,
        auth_hash TEXT NOT NULL UNIQUE,
        role TEXT NOT NULL CHECK(role IN ('presenter', 'participant')),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS external_reports (
        session_id TEXT NOT NULL REFERENCES sessions(id),
        report_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        PRIMARY KEY (session_id, report_id)
      );
      CREATE TABLE IF NOT EXISTS report_locations (
        session_id TEXT NOT NULL REFERENCES sessions(id),
        report_id TEXT NOT NULL,
        site_id TEXT NOT NULL,
        located_at TEXT NOT NULL,
        PRIMARY KEY (session_id, report_id)
      );
      CREATE TABLE IF NOT EXISTS transmissions (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL REFERENCES sessions(id),
        participant_id TEXT NOT NULL REFERENCES participants(id),
        capture_mode TEXT NOT NULL,
        channel TEXT,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        finalized_at TEXT,
        payload_hash TEXT,
        input_id TEXT UNIQUE,
        error_code TEXT
      );
      CREATE INDEX IF NOT EXISTS transmissions_session_idx ON transmissions(session_id, created_at);
      CREATE TABLE IF NOT EXISTS raw_inputs (
        input_id TEXT PRIMARY KEY,
        transmission_id TEXT NOT NULL UNIQUE REFERENCES transmissions(id),
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS extraction_jobs (
        id TEXT PRIMARY KEY,
        transmission_id TEXT NOT NULL UNIQUE REFERENCES transmissions(id),
        session_id TEXT NOT NULL REFERENCES sessions(id),
        input_id TEXT NOT NULL REFERENCES raw_inputs(input_id),
        status TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        lease_until TEXT,
        error_code TEXT,
        adapter TEXT,
        model TEXT,
        prompt_version TEXT,
        extraction_result_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS extraction_jobs_claim_idx
        ON extraction_jobs(status, lease_until, created_at);
      CREATE TABLE IF NOT EXISTS observations (
        observation_id TEXT PRIMARY KEY,
        transmission_id TEXT NOT NULL REFERENCES transmissions(id),
        job_id TEXT NOT NULL REFERENCES extraction_jobs(id),
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS resolutions (
        observation_id TEXT PRIMARY KEY REFERENCES observations(observation_id),
        transmission_id TEXT NOT NULL REFERENCES transmissions(id),
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        session_id TEXT NOT NULL REFERENCES sessions(id),
        transmission_id TEXT REFERENCES transmissions(id),
        occurred_at TEXT NOT NULL,
        type TEXT NOT NULL,
        payload_json TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS events_session_cursor_idx ON events(session_id, sequence);
      CREATE TABLE IF NOT EXISTS token_issuances (
        id TEXT PRIMARY KEY,
        participant_id TEXT NOT NULL REFERENCES participants(id),
        session_id TEXT NOT NULL REFERENCES sessions(id),
        transmission_id TEXT NOT NULL REFERENCES transmissions(id),
        issued_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS token_issuances_rate_idx
        ON token_issuances(participant_id, issued_at);
      CREATE TABLE IF NOT EXISTS operational_belief_sessions (
        session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
        revision INTEGER NOT NULL,
        record_json TEXT NOT NULL,
        snapshot_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
    `);
    const sessionColumns = new Set(
      this.all<{ name: string }>("PRAGMA table_info(sessions)").map(
        (column) => column.name,
      ),
    );
    if (!sessionColumns.has("ended_at")) {
      this.db.exec("ALTER TABLE sessions ADD COLUMN ended_at TEXT");
    }
    if (!sessionColumns.has("ended_by_role")) {
      this.db.exec(
        "ALTER TABLE sessions ADD COLUMN ended_by_role TEXT CHECK(ended_by_role IN ('presenter', 'participant'))",
      );
    }
    const transmissionColumns = new Set(
      this.all<{ name: string }>("PRAGMA table_info(transmissions)").map(
        (column) => column.name,
      ),
    );
    if (!transmissionColumns.has("reservation_key")) {
      this.db.exec("ALTER TABLE transmissions ADD COLUMN reservation_key TEXT");
      this.db.exec(
        "CREATE UNIQUE INDEX IF NOT EXISTS transmissions_reservation_key ON transmissions(participant_id, reservation_key)",
      );
    }
    if (!transmissionColumns.has("updated_at")) {
      this.db.exec("ALTER TABLE transmissions ADD COLUMN updated_at TEXT");
      this.db.exec(
        "UPDATE transmissions SET updated_at = created_at WHERE updated_at IS NULL",
      );
    }
    const jobColumns = new Set(
      this.all<{ name: string }>("PRAGMA table_info(extraction_jobs)").map(
        (column) => column.name,
      ),
    );
    if (!jobColumns.has("extraction_result_json")) {
      this.db.exec(
        "ALTER TABLE extraction_jobs ADD COLUMN extraction_result_json TEXT",
      );
    }
  }
}

// A D1 stand-in over Node's built-in SQLite (node:sqlite, Node 22.5+): the
// Worker's own SQL and schema.sql run unchanged, D1 being SQLite. Only the
// D1 API the Worker uses is here: prepare/bind/run/first/all and batch.

import { DatabaseSync } from "node:sqlite";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
export const SCHEMA = readFileSync(join(here, "..", "schema.sql"), "utf8");

function statement(db, sql, params) {
  const run = () => {
    const r = db.prepare(sql).run(...params);
    return { success: true, meta: { changes: Number(r.changes), last_row_id: Number(r.lastInsertRowid) } };
  };
  return {
    sql,
    params,
    bind: (...p) => {
      for (const v of p) {
        if (v === undefined) throw new TypeError(`D1_TYPE_ERROR: undefined bound in: ${sql}`);
      }
      return statement(db, sql, p);
    },
    run: async () => run(),
    first: async (column) => {
      const row = db.prepare(sql).get(...params);
      if (!row) return null;
      return column ? row[column] ?? null : { ...row };
    },
    all: async () => ({ success: true, results: db.prepare(sql).all(...params).map((r) => ({ ...r })) }),
    _run: run,
  };
}

export function d1() {
  const db = new DatabaseSync(":memory:");
  db.exec(SCHEMA);
  return {
    raw: db,
    prepare: (sql) => statement(db, sql, []),
    batch: async (stmts) => {
      db.exec("BEGIN");
      try {
        const out = stmts.map((s) => s._run());
        db.exec("COMMIT");
        return out;
      } catch (exc) {
        db.exec("ROLLBACK");
        throw exc;
      }
    },
  };
}

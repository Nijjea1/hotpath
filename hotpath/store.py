"""SQLite persistence for runs and experiments. One file, no server, safe to inspect with sqlite3."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from hotpath.schema import Experiment, RunState

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, status TEXT, created_at TEXT, updated_at TEXT, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS experiments (
  id TEXT PRIMARY KEY, run_id TEXT, parent_id TEXT, iteration INTEGER, status TEXT,
  created_at TEXT, updated_at TEXT, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exp_run ON experiments(run_id, created_at);
"""


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        try:
            c.execute("PRAGMA journal_mode=WAL")
            with c:
                yield c
        finally:
            c.close()

    # -- runs ---------------------------------------------------------------
    def save_run(self, run: RunState) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?)",
                      (run.id, run.status, run.created_at.isoformat(), run.updated_at.isoformat(), run.model_dump_json()))

    def get_run(self, run_id: str) -> Optional[RunState]:
        with self._conn() as c:
            row = c.execute("SELECT data FROM runs WHERE id=?", (run_id,)).fetchone()
        return RunState.model_validate_json(row[0]) if row else None

    def list_runs(self) -> list[RunState]:
        with self._conn() as c:
            rows = c.execute("SELECT data FROM runs ORDER BY created_at DESC").fetchall()
        return [RunState.model_validate_json(r[0]) for r in rows]

    def latest_run(self) -> Optional[RunState]:
        runs = self.list_runs()
        return runs[0] if runs else None

    # -- experiments --------------------------------------------------------
    def save_experiment(self, exp: Experiment) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO experiments VALUES (?,?,?,?,?,?,?,?)",
                      (exp.id, exp.run_id, exp.parent_id, exp.iteration, exp.status.value,
                       exp.created_at.isoformat(), exp.updated_at.isoformat(), exp.model_dump_json()))

    def get_experiment(self, exp_id: str) -> Optional[Experiment]:
        with self._conn() as c:
            row = c.execute("SELECT data FROM experiments WHERE id=?", (exp_id,)).fetchone()
        return Experiment.model_validate_json(row[0]) if row else None

    def list_experiments(self, run_id: str) -> list[Experiment]:
        with self._conn() as c:
            rows = c.execute("SELECT data FROM experiments WHERE run_id=? ORDER BY created_at, id", (run_id,)).fetchall()
        return [Experiment.model_validate_json(r[0]) for r in rows]

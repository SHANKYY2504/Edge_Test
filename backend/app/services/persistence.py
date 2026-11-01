import csv
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.models.requests import ApiRequest
from app.models.responses import ApiResponse

SENSITIVE_KEY = re.compile(
    r"(authorization|cookie|token|secret|api[-_]?key|password|passwd|credential)",
    re.IGNORECASE,
)
REDACTED = "[REDACTED]"


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: REDACTED if SENSITIVE_KEY.search(str(key)) else _redact_json(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_json(child) for child in value]
    return value


def _redact_url(raw_url: str) -> str:
    parts = urlsplit(raw_url)
    query = [
        (key, REDACTED if SENSITIVE_KEY.search(key) else value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    host = parts.hostname or ""
    if ":" in host:
        host = "[{}]".format(host)
    if parts.port:
        host = "{}:{}".format(host, parts.port)
    return urlunsplit((parts.scheme, host, parts.path, urlencode(query), ""))


def sanitize_request(request: ApiRequest) -> Dict[str, Any]:
    request_data = request.model_dump(mode="json")
    request_data["url"] = _redact_url(str(request.url))
    request_data["headers"] = {
        name: REDACTED if SENSITIVE_KEY.search(name) else value
        for name, value in request.headers.items()
    }
    request_data["query_params"] = {
        name: REDACTED if SENSITIVE_KEY.search(name) else value
        for name, value in request.query_params.items()
    }
    request_data["body"] = _redact_json(request.body)
    return request_data


def _sanitize_response(response: ApiResponse, secrets: List[str]) -> Dict[str, Any]:
    data = response.model_dump(mode="json")
    data["headers"] = {
        name: REDACTED if SENSITIVE_KEY.search(name) else _replace_secrets(value, secrets)
        for name, value in response.headers.items()
    }
    data["body"] = _redact_json(_replace_secret_values(response.body, secrets))
    if data["error"]:
        data["error"] = {
            "code": data["error"]["code"],
            "message": "Execution failed.",
        }
    return data


def _replace_secrets(value: str, secrets: List[str]) -> str:
    for secret in secrets:
        if secret:
            value = value.replace(secret, REDACTED)
    return value


def _replace_secret_values(value: Any, secrets: List[str]) -> Any:
    if isinstance(value, str):
        return _replace_secrets(value, secrets)
    if isinstance(value, dict):
        return {key: _replace_secret_values(child, secrets) for key, child in value.items()}
    if isinstance(value, list):
        return [_replace_secret_values(child, secrets) for child in value]
    return value


def _secret_values(request: ApiRequest) -> List[str]:
    secrets = []
    for name, value in request.headers.items():
        if SENSITIVE_KEY.search(name):
            secrets.extend([value, *value.split()])
    for name, value in parse_qsl(urlsplit(str(request.url)).query, keep_blank_values=True):
        if SENSITIVE_KEY.search(name):
            secrets.append(value)

    def collect(value: Any, sensitive: bool = False) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                collect(child, sensitive or bool(SENSITIVE_KEY.search(str(key))))
        elif isinstance(value, list):
            for child in value:
                collect(child, sensitive)
        elif sensitive and isinstance(value, str):
            secrets.extend([value, *value.split()])

    collect(request.body)
    return sorted({secret for secret in secrets if len(secret) >= 3}, key=len, reverse=True)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlatformRepository:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    def initialize(self) -> None:
        if self.database_path != ":memory:":
            path = Path(self.database_path)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if path.exists():
                os.chmod(path, 0o600)
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS collections (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS saved_tests (
                    id TEXT PRIMARY KEY,
                    collection_id TEXT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    test_cases_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS executions (
                    id TEXT PRIMARY KEY,
                    collection_id TEXT REFERENCES collections(id) ON DELETE SET NULL,
                    saved_test_id TEXT REFERENCES saved_tests(id) ON DELETE SET NULL,
                    replay_of TEXT REFERENCES executions(id) ON DELETE SET NULL,
                    method TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    status_code INTEGER,
                    outcome TEXT NOT NULL,
                    error_category TEXT,
                    duration_ms REAL,
                    test_case_json TEXT,
                    request_json TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_executions_outcome_time
                    ON executions(outcome, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_executions_endpoint_method
                    ON executions(endpoint, method);
                """
            )
            execution_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(executions)").fetchall()
            }
            if "test_case_json" not in execution_columns:
                connection.execute("ALTER TABLE executions ADD COLUMN test_case_json TEXT")
    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @contextmanager
    def _connection(self) -> Generator[sqlite3.Connection, None, None]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
            if self.database_path != ":memory:":
                for suffix in ("", "-wal", "-shm"):
                    database_file = Path(self.database_path + suffix)
                    if database_file.exists():
                        os.chmod(database_file, 0o600)

    def create_collection(self, name: str) -> Dict[str, Any]:
        collection_id, timestamp = str(uuid.uuid4()), _now()
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO collections VALUES (?, ?, ?, ?)",
                (collection_id, name.strip(), timestamp, timestamp),
            )
        return {"id": collection_id, "name": name.strip(), "created_at": timestamp}

    def list_collections(self) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT c.id, c.name, c.created_at, c.updated_at,
                          COUNT(s.id) AS saved_test_count
                   FROM collections c LEFT JOIN saved_tests s ON s.collection_id=c.id
                   GROUP BY c.id ORDER BY c.updated_at DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def rename_collection(self, collection_id: str, name: str) -> Optional[Dict[str, Any]]:
        with self._connection() as connection:
            cursor = connection.execute(
                "UPDATE collections SET name=?, updated_at=? WHERE id=?",
                (name.strip(), _now(), collection_id),
            )
            if cursor.rowcount == 0:
                return None
            row = connection.execute(
                "SELECT id, name, created_at, updated_at FROM collections WHERE id=?",
                (collection_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_collection(self, collection_id: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM collections WHERE id=?", (collection_id,))
        return cursor.rowcount > 0

    def save_test(
        self, collection_id: str, name: str, request: ApiRequest, test_cases: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        test_id, timestamp = str(uuid.uuid4()), _now()
        with self._connection() as connection:
            exists = connection.execute(
                "SELECT 1 FROM collections WHERE id=?", (collection_id,)
            ).fetchone()
            if not exists:
                return None
            connection.execute(
                "INSERT INTO saved_tests VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    test_id,
                    collection_id,
                    name.strip(),
                    json.dumps(sanitize_request(request)),
                    json.dumps(_redact_json(test_cases)),
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                "UPDATE collections SET updated_at=? WHERE id=?",
                (timestamp, collection_id),
            )
        return self.get_saved_test(test_id)

    def get_saved_test(self, test_id: str) -> Optional[Dict[str, Any]]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM saved_tests WHERE id=?", (test_id,)).fetchone()
        return self._saved_test_row(row) if row else None

    def list_saved_tests(self, collection_id: str) -> List[Dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM saved_tests WHERE collection_id=? ORDER BY created_at DESC",
                (collection_id,),
            ).fetchall()
        return [self._saved_test_row(row) for row in rows]

    @staticmethod
    def _saved_test_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"],
            "collection_id": row["collection_id"],
            "name": row["name"],
            "request": json.loads(row["request_json"]),
            "test_cases": json.loads(row["test_cases_json"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def delete_saved_test(self, test_id: str) -> bool:
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM saved_tests WHERE id=?", (test_id,))
        return cursor.rowcount > 0

    def record_execution(
        self,
        request: ApiRequest,
        response: ApiResponse,
        *,
        collection_id: Optional[str] = None,
        saved_test_id: Optional[str] = None,
        replay_of: Optional[str] = None,
        test_case: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        execution_id, timestamp = str(uuid.uuid4()), _now()
        if response.error is not None:
            outcome, category = "errored", response.error.code
        elif response.status_code is not None and response.status_code >= 400:
            outcome, category = "failed", "http_error"
        else:
            outcome, category = "passed", None
        stored_request = sanitize_request(request)
        endpoint_parts = urlsplit(stored_request["url"])
        endpoint = urlunsplit((endpoint_parts.scheme, endpoint_parts.netloc, endpoint_parts.path, "", ""))
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO executions
                (id, collection_id, saved_test_id, replay_of, method, endpoint,
                 status_code, outcome, error_category, duration_ms, test_case_json,
                 request_json, response_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    execution_id, collection_id, saved_test_id, replay_of, request.method.value,
                    endpoint, response.status_code, outcome, category,
                    response.execution_time_ms,
                    json.dumps(_redact_json(test_case)) if test_case else None,
                    json.dumps(stored_request),
                    json.dumps(_sanitize_response(response, _secret_values(request))), timestamp,
                ),
            )
        return self.get_execution(execution_id) or {}

    def get_execution(self, execution_id: str) -> Optional[Dict[str, Any]]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM executions WHERE id=?", (execution_id,)).fetchone()
        return self._execution_row(row) if row else None

    def list_executions(
        self, *, failures_only: bool = False, endpoint: Optional[str] = None,
        method: Optional[str] = None, status_code: Optional[int] = None,
        error_type: Optional[str] = None, limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses, values = [], []
        if failures_only:
            clauses.append("outcome != 'passed'")
        if endpoint:
            clauses.append("endpoint LIKE ?")
            values.append("%{}%".format(endpoint))
        if method:
            clauses.append("method=?")
            values.append(method.upper())
        if status_code is not None:
            clauses.append("status_code=?")
            values.append(status_code)
        if error_type:
            clauses.append("error_category=?")
            values.append(error_type)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM executions{} ORDER BY created_at DESC LIMIT ?".format(where),
                (*values, max(1, min(limit, 500))),
            ).fetchall()
        return [self._execution_row(row) for row in rows]

    @staticmethod
    def _execution_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "id": row["id"], "collection_id": row["collection_id"],
            "saved_test_id": row["saved_test_id"], "replay_of": row["replay_of"],
            "method": row["method"], "endpoint": row["endpoint"],
            "status_code": row["status_code"], "outcome": row["outcome"],
            "error_category": row["error_category"], "duration_ms": row["duration_ms"],
            "test_case": json.loads(row["test_case_json"]) if row["test_case_json"] else None,
            "request": json.loads(row["request_json"]),
            "response": json.loads(row["response_json"]), "created_at": row["created_at"],
        }

    def dashboard(self) -> Dict[str, Any]:
        with self._connection() as connection:
            totals = connection.execute(
                """SELECT COUNT(*) AS total,
                          SUM(outcome='passed') AS passed,
                          SUM(outcome='failed') AS failed,
                          SUM(outcome='errored') AS errored
                   FROM executions"""
            ).fetchone()
            test_totals = connection.execute(
                   """SELECT COUNT(*) AS total,
                             SUM(outcome='passed') AS passed,
                             SUM(outcome='failed') AS failed,
                             SUM(outcome='errored') AS errored
                      FROM executions WHERE test_case_json IS NOT NULL"""
            ).fetchone()
            saved_case_rows = connection.execute(
                "SELECT test_cases_json FROM saved_tests"
            ).fetchall()
            failures = connection.execute(
                "SELECT * FROM executions WHERE outcome!='passed' ORDER BY created_at DESC LIMIT 10"
            ).fetchall()
            frequent = connection.execute(
                """SELECT method, endpoint, COUNT(*) AS failures
                   FROM executions WHERE outcome!='passed'
                   GROUP BY method, endpoint ORDER BY failures DESC LIMIT 10"""
            ).fetchall()
            trend_rows = connection.execute(
                """SELECT substr(created_at, 1, 10) AS date,
                          COUNT(*) AS total,
                          SUM(outcome='passed') AS passed,
                          SUM(outcome!='passed') AS failed
                   FROM executions GROUP BY date ORDER BY date DESC LIMIT 14"""
            ).fetchall()
        return {
            "total_requests": totals["total"] or 0,
            "total_test_cases": test_totals["total"] or 0,
            "saved_test_case_definitions": sum(
                len(json.loads(row[0])) for row in saved_case_rows
            ),
            "passed_tests": test_totals["passed"] or 0,
            "failed_tests": test_totals["failed"] or 0,
            "errored_tests": test_totals["errored"] or 0,
            "recent_failures": [self._execution_row(row) for row in failures],
            "failing_endpoints": [dict(row) for row in frequent],
            "trends": list(reversed([dict(row) for row in trend_rows])),
        }

    def export(self, *, as_csv: bool) -> str:
        rows = self.list_executions(limit=500)
        if not as_csv:
            return json.dumps(rows, indent=2)
        output = StringIO()
        fields = ["id", "created_at", "method", "endpoint", "status_code",
                  "outcome", "error_category", "duration_ms", "request", "response"]
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "id": row["id"],
                    "created_at": row["created_at"],
                    "method": row["method"],
                    "endpoint": row["endpoint"],
                    "status_code": row["status_code"],
                    "outcome": row["outcome"],
                    "error_category": row["error_category"],
                    "duration_ms": row["duration_ms"],
                    "request": json.dumps(row["request"]),
                    "response": json.dumps(row["response"]),
                }
            )
        return output.getvalue()

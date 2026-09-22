"""研究库：MySQL 为部署目标，SQLite 用于无服务依赖的本地验证。"""
from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import time
from urllib.parse import parse_qs, unquote, urlsplit


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class ResearchStore:
    def __init__(self, url):
        if url.startswith("sqlite:///"):
            self.mysql = False
            path = url[len("sqlite:///"):]
            if not path:
                raise ValueError("SQLite 路径不能为空")
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(path)
            self.conn.execute("PRAGMA foreign_keys = ON")
        elif url.startswith("mysql://"):
            self.mysql = True
            parsed = urlsplit(url)
            query = parse_qs(parsed.query, strict_parsing=True)
            if set(query) - {"ssl_ca"} or parsed.fragment:
                raise ValueError("MySQL URL 仅支持 ssl_ca 参数，不支持 fragment")
            if not parsed.hostname or not parsed.username or not parsed.path.strip("/"):
                raise ValueError("MySQL URL 必须包含主机、用户和数据库名")
            try:
                import pymysql
            except ImportError:
                raise RuntimeError("MySQL 模式需要安装 requirements-quant.txt") from None
            tls = {}
            if "ssl_ca" in query:
                tls = {"ssl_ca": query["ssl_ca"][0], "ssl_verify_cert": True, "ssl_verify_identity": True}
            # 不输出连接字符串，异常由 CLI 显示无凭据的错误类型。
            self.conn = pymysql.connect(host=parsed.hostname, port=parsed.port or 3306,
                                        user=unquote(parsed.username), password=unquote(parsed.password or ""),
                                        database=unquote(parsed.path.lstrip("/")), charset="utf8mb4",
                                        autocommit=False, connect_timeout=10, read_timeout=30,
                                        write_timeout=30, **tls)
        else:
            raise ValueError("DATABASE_URL 只支持 mysql:// 或 sqlite:///")

    def close(self):
        self.conn.close()

    @contextmanager
    def transaction(self):
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def execute(self, sql, params=()):
        if self.mysql:
            sql = sql.replace("?", "%s")
            cursor = self.conn.cursor()
            try:
                cursor.execute(sql, params)
                # 统一返回已读取的数据，游标在此关闭，避免逐根回放积累游标。
                rows = cursor.fetchall() if cursor.description else ()
                return Rows(rows)
            finally:
                cursor.close()
        return self.conn.execute(sql, params)

    def migrate(self):
        lock_name = None
        try:
            if self.mysql:
                db = self.execute("SELECT DATABASE()").fetchone()[0]
                lock_name = "chan_quant_" + sha256(db.encode()).hexdigest()[:40]
                if self.execute("SELECT GET_LOCK(?, 10)", (lock_name,)).fetchone()[0] != 1:
                    lock_name = None
                    raise RuntimeError("无法取得数据库迁移锁")
            with self.transaction():
                self._migrate()
        finally:
            if lock_name:
                self.execute("SELECT RELEASE_LOCK(?)", (lock_name,))
                self.conn.commit()

    def _migrate(self):
        if not self.mysql:
            self.execute("BEGIN IMMEDIATE")
        self.create_table("""CREATE TABLE IF NOT EXISTS quant_schema_migrations (
            version VARCHAR(128) PRIMARY KEY, checksum VARCHAR(64) NOT NULL)""")
        for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            checksum = sha256(sql.encode()).hexdigest()
            old = self.execute("SELECT checksum FROM quant_schema_migrations WHERE version = ?",
                               (path.name,)).fetchone()
            if old:
                if old[0] != checksum:
                    raise RuntimeError("已应用迁移被修改，请新增迁移文件")
                continue
            for statement in sql.split(";"):
                if statement.strip():
                    self.execute_migration_statement(statement)
            self.execute("INSERT INTO quant_schema_migrations VALUES (?, ?)",
                         (path.name, checksum))

    def create_table(self, statement):
        # MySQL DDL 隐式提交；001 各语句可重试，全部成功后才记录迁移版本。
        suffix = " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin" if self.mysql else ""
        self.execute(statement.strip() + suffix)

    def execute_migration_statement(self, statement):
        if statement.strip().upper().startswith("CREATE TABLE"):
            self.create_table(statement)
        else:
            self.execute(statement)

    def start_run(self, run_id, manifest, inst, series):
        with self.transaction():
            self.execute("INSERT INTO quant_runs VALUES (?, 'running', ?, NULL, ?, NULL)",
                         (run_id, int(time.time() * 1000), encode(manifest)))
            for bar, rows in series.items():
                for row in rows:
                    self.execute("INSERT INTO quant_candles VALUES (?, ?, ?, ?, ?)",
                                 (run_id, inst, bar, row["ts"], encode(row)))

    def record_step(self, run_id, observed_at, observations, evaluation):
        # 信号观察与该时刻决策必须一起提交，不留下半个收盘事件。
        with self.transaction():
            for key, signal in observations:
                self.execute("INSERT INTO quant_signal_observations VALUES (?, ?, ?, ?, ?, ?)",
                             (run_id, key, observed_at, signal["ts"],
                              int(signal.get("locked") is True), encode(signal)))
            self.execute("INSERT INTO quant_evaluations VALUES (?, ?, ?, ?, ?, ?)",
                         (run_id, observed_at, evaluation.get("signal_key"), evaluation["action"],
                          evaluation["reason"], encode(evaluation)))

    def finish_run(self, run_id, summary, failed=False):
        with self.transaction():
            self.execute("UPDATE quant_runs SET status = ?, finished_at = ?, summary_json = ? WHERE run_id = ?",
                         ("failed" if failed else "completed", int(time.time() * 1000), encode(summary), run_id))

    def report(self, run_id):
        with self.transaction():
            row = self.execute("SELECT status, manifest_json, summary_json FROM quant_runs WHERE run_id = ?",
                               (run_id,)).fetchone()
            if row is None:
                raise ValueError("回放任务不存在")
            first_step = self.execute("SELECT MIN(observed_at) FROM quant_evaluations WHERE run_id = ?",
                                      (run_id,)).fetchone()[0]
            timings = self.execute("""SELECT signal_key, MIN(extreme_at), MIN(observed_at),
                MIN(CASE WHEN locked = 1 THEN observed_at ELSE NULL END)
                FROM quant_signal_observations WHERE run_id = ? GROUP BY signal_key ORDER BY MIN(observed_at), signal_key""",
                                  (run_id,)).fetchall()
        return {"run_id": run_id, "status": row[0], "manifest": json.loads(row[1]),
                "summary": json.loads(row[2]) if row[2] else None,
                "signal_timings": [{"signal_key": key, "extreme_at": extreme,
                                    "first_observed_at": first, "first_locked_observed_at": locked,
                                    "first_observation_left_censored": first == first_step}
                                   for key, extreme, first, locked in timings]}


class Rows:
    """统一只读查询结果。"""
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows

"""Opt-in MySQL/MongoDB read-role qualification against disposable databases."""
from __future__ import annotations

import os
import uuid
from urllib.parse import urlsplit

import pytest

from kazma_core.config_store import get_config_store
from kazma_core.tenant_context import tenant_scope
from kazma_skills.native.database_client.connections import CONNECTIONS_KEY
from kazma_skills.native.database_client import tools


@pytest.fixture
def mysql_reader():
    dsn = os.environ.get("KAZMA_AUDIT_MYSQL_DSN")
    if not dsn:
        pytest.skip("needs a disposable KAZMA_AUDIT_MYSQL_DSN")
    import pymysql

    url = urlsplit(dsn)
    suffix = uuid.uuid4().hex[:12]
    database, role = "kazma_read_" + suffix, "kazma_reader_" + suffix
    conn = pymysql.connect(host=url.hostname, port=url.port, user=url.username,
                           password=url.password, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE `{database}`")
            cur.execute(f"CREATE USER '{role}'@'%' IDENTIFIED BY 'synthetic-reader-only'")
            cur.execute(f"CREATE TABLE `{database}`.items(id int, name text) ENGINE=InnoDB")
            cur.execute(f"INSERT INTO `{database}`.items VALUES (1, 'item')")
            cur.execute(f"GRANT SELECT ON `{database}`.items TO '{role}'@'%'")
        entry = {"enabled": True, "dsn": f"mysql://{role}:synthetic-reader-only@{url.hostname}:{url.port}/{database}",
                 "role": role, "tenants": ["audit"], "tables": [f"{database}.items"]}
        get_config_store().set(CONNECTIONS_KEY, {"proof": entry})
        yield conn, database, role, entry
    finally:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{database}`")
            cur.execute(f"DROP USER IF EXISTS '{role}'@'%'")
        conn.close()


@pytest.mark.asyncio
async def test_real_mysql_reader_and_privilege_drift(mysql_reader):
    conn, database, role, entry = mysql_reader
    with tenant_scope("audit"):
        answer = await tools.execute_db_query("connection:proof", f"SELECT id FROM {database}.items WHERE id=%s", [1])
        assert '"id": 1' in answer, answer
        literal = await tools.execute_db_query("connection:proof",
            f"SELECT '%s 100%' AS literal FROM {database}.items WHERE id=%s", [1])
        assert '"literal": "%s 100%"' in literal, literal
        for query in ("SELECT LOAD_FILE('/etc/passwd')", "SELECT SLEEP(100)", "SELECT sys.sys_exec('evil')"):
            assert (await tools.execute_db_query("connection:proof", query)).startswith("Error:")
        with conn.cursor() as cur:
            cur.execute(f"CREATE VIEW `{database}`.summary AS SELECT id FROM `{database}`.items")
            cur.execute(f"GRANT SELECT ON `{database}`.summary TO '{role}'@'%'")
            entry["tables"].append(f"{database}.summary")
            get_config_store().set(CONNECTIONS_KEY, {"proof": entry})
        assert (await tools.execute_db_query("connection:proof", f"SELECT id FROM {database}.summary")).startswith("Error:")
        with conn.cursor() as cur:
            cur.execute(f"GRANT INSERT ON `{database}`.items TO '{role}'@'%'")
        assert (await tools.execute_db_query("connection:proof", f"SELECT id FROM {database}.items")).startswith("Error:")
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM `{database}`.items")
        assert cur.fetchone() == (1,)
        cur.execute("SELECT count(*) FROM information_schema.PROCESSLIST WHERE USER=%s", (role,))
        assert cur.fetchone() == (0,)


@pytest.fixture
def mongo_reader():
    dsn = os.environ.get("KAZMA_AUDIT_MONGO_DSN")
    if not dsn:
        pytest.skip("needs a disposable KAZMA_AUDIT_MONGO_DSN")
    from pymongo import MongoClient

    url = urlsplit(dsn)
    suffix = uuid.uuid4().hex[:12]
    database, role = "kazma_read_" + suffix, "kazma_reader_" + suffix
    with MongoClient(dsn, serverSelectionTimeoutMS=5000) as admin:
        db = admin[database]
        db.items.insert_one({"id": 1})
        db.command("createRole", role, privileges=[{"resource": {"db": database, "collection": "items"},
                                                  "actions": ["find"]}], roles=[])
        db.command("createUser", role, pwd="synthetic-reader-only", roles=[{"role": role, "db": database}])
        entry = {"enabled": True,
                 "dsn": f"mongodb://{role}:synthetic-reader-only@{url.hostname}:{url.port}/{database}",
                 "role": role, "tenants": ["audit"], "tables": ["items"]}
        get_config_store().set(CONNECTIONS_KEY, {"proof": entry})
        try:
            yield db, role, entry
        finally:
            db.command("dropUser", role)
            db.command("dropRole", role)
            admin.drop_database(database)


@pytest.mark.asyncio
async def test_real_mongo_find_only_reader_and_privilege_drift(mongo_reader):
    db, role, entry = mongo_reader
    with tenant_scope("audit"):
        answer = await tools.execute_db_query("connection:proof", '{"id":1}', ["items"])
        assert '"id": 1' in answer, answer
        for query in ('{"$where":"evil()"}', '{"x":{"$function":{}}}'):
            assert (await tools.execute_db_query("connection:proof", query, ["items"])).startswith("Error:")
        db.command("create", "summary", viewOn="items", pipeline=[])
        db.command("grantPrivilegesToRole", role, privileges=[
            {"resource": {"db": db.name, "collection": "summary"}, "actions": ["find"]}])
        entry["tables"].append("summary")
        get_config_store().set(CONNECTIONS_KEY, {"proof": entry})
        assert (await tools.execute_db_query("connection:proof", "{}", ["summary"])).startswith("Error:")
        db.command("grantPrivilegesToRole", role, privileges=[
            {"resource": {"db": db.name, "collection": "items"}, "actions": ["insert"]}])
        assert (await tools.execute_db_query("connection:proof", "{}", ["items"])).startswith("Error:")
    assert db.items.count_documents({}) == 1

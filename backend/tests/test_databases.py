"""Bring-your-own-database: uploads, isolation from the sample database, and safety."""

import base64
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient

from app import databases, db, graph, llm, main
from app.validator import validate_sql
from tests.test_graph import FakeLLM, draft, intent

LIBRARY_SQL = """
CREATE TABLE Authors (AuthorID INTEGER PRIMARY KEY, Name TEXT NOT NULL, Country TEXT);
CREATE TABLE Books (
    BookID INTEGER PRIMARY KEY, Title TEXT NOT NULL, Genre TEXT,
    AuthorID INTEGER REFERENCES Authors(AuthorID), Published TEXT
);
INSERT INTO Authors VALUES (1, 'Toni Morrison', 'USA'), (2, 'R. K. Narayan', 'India');
INSERT INTO Books VALUES (1, 'Beloved', 'Fiction', 1, '1987-09-01'),
                         (2, 'Swami and Friends', 'Fiction', 2, '1935-01-01'),
                         (3, 'The Guide', 'Fiction', 2, '1958-01-01');
"""


@pytest.fixture
def library():
    return databases.create_from_sql(LIBRARY_SQL, "library.sql")


def test_sql_script_becomes_a_database(library):
    s = db.get_schema(databases.path_for(library.id))
    assert set(s.tables) == {"authors", "books"}
    assert s.table("Books").foreign_keys[0].ref_table == "Authors"
    assert s.notes == {}  # sample-only business notes must not leak into uploads
    assert library.tables == 2


def test_validator_uses_the_uploaded_schema(library):
    path = databases.path_for(library.id)
    assert validate_sql("SELECT Title FROM Books", db_path=path).valid
    bad = validate_sql("SELECT * FROM Customers", db_path=path)  # a sample-DB table
    assert not bad.valid and "Customers" in bad.errors[0]


def test_uploaded_database_is_read_only(library):
    conn = db.get_readonly_connection(databases.path_for(library.id))
    with pytest.raises(sqlite3.Error):
        conn.execute("DELETE FROM Books")
    conn.close()


def test_sqlite_file_upload(tmp_path):
    f = tmp_path / "x.db"
    c = sqlite3.connect(f)
    c.executescript("CREATE TABLE T (id INTEGER PRIMARY KEY, v TEXT); INSERT INTO T VALUES (1, 'a');")
    c.close()
    meta = databases.create_from_sqlite(f.read_bytes(), "x.db")
    assert meta.kind == "sqlite" and meta.tables == 1


@pytest.mark.parametrize("script,fragment", [
    ("", "empty"),
    ("SELECT 1;", "No tables"),
    ("CREATE TABLE (oops;", "couldn't be loaded"),
    ("ATTACH DATABASE '/etc/passwd' AS x; CREATE TABLE t (a);", "couldn't be loaded"),
])
def test_bad_sql_scripts_rejected(script, fragment):
    with pytest.raises(databases.UploadError) as e:
        databases.create_from_sql(script)
    assert fragment in str(e.value)


def test_non_sqlite_file_rejected():
    with pytest.raises(databases.UploadError):
        databases.create_from_sqlite(b"not a database at all", "fake.db")


def test_unknown_or_malformed_ids_rejected():
    for bad in ("../../etc/passwd", "db_0000000000000000"):
        with pytest.raises(databases.UploadError):
            databases.path_for(bad)
    assert databases.path_for(None) == databases.path_for("sample")


def test_agent_runs_against_uploaded_database(library):
    graph._graph = None
    llm.set_client(FakeLLM(intents=[intent("generate")],
                           drafts=[draft("SELECT a.Name, COUNT(*) AS n FROM Books b JOIN Authors a "
                                         "ON a.AuthorID = b.AuthorID GROUP BY a.Name ORDER BY n DESC")]))
    r = graph.run_turn("byo-" + uuid.uuid4().hex[:6], "books per author", library.id)
    assert r["type"] == "sql"
    assert r["results"]["rows"][0] == ["R. K. Narayan", 2]


def test_api_upload_then_chat():
    main._hits.clear()
    graph._graph = None
    client = TestClient(main.app)
    up = client.post("/api/databases", json={"name": "library.sql", "kind": "sql", "content": LIBRARY_SQL})
    assert up.status_code == 200, up.text
    body = up.json()
    db_id = body["database"]["id"]
    assert "Books" in body["tables"] and body["examples"]
    assert client.get(f"/api/schema?db_id={db_id}").json()["database"]["name"] == "library.sql"

    llm.set_client(FakeLLM(intents=[intent("generate")], drafts=[draft("SELECT COUNT(*) FROM Books")]))
    r = client.post("/api/chat", json={"session_id": "byo-api", "message": "how many books", "db_id": db_id})
    assert r.json()["results"]["rows"][0][0] == 3

    sqlite_bad = client.post("/api/databases", json={"kind": "sqlite", "content": base64.b64encode(b"nope").decode()})
    assert sqlite_bad.status_code == 400
    assert client.post("/api/chat", json={"session_id": "x", "message": "hi", "db_id": "db_ffffffffffffffff"}).status_code == 400

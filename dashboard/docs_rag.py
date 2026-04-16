from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


DOCS_DIR_NAME = "audit_docs"
MEMORY_DIR_NAME = "memory"
DB_FILE_NAME = "vulnflow.db"
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
CHUNK_SIZE = 700
CHUNK_OVERLAP = 150

_embedder_lock = threading.Lock()
_embedder: Any | None = None


def _get_embedder() -> Any:
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:
                    raise RuntimeError(
                        "sentence-transformers is not installed. Run `pip install sentence-transformers`."
                    ) from exc
                _embedder = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _embedder


def _to_vec_literal(values: list[float]) -> str:
    return json.dumps([float(v) for v in values], ensure_ascii=False)


def _iter_markdown_files(docs_root: Path) -> list[Path]:
    if not docs_root.exists() or not docs_root.is_dir():
        return []
    return sorted(
        [path for path in docs_root.rglob("*.md") if path.is_file()],
        key=lambda item: str(item).lower(),
    )


def _split_text_into_chunks(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    source = text.strip()
    if not source:
        return []
    if chunk_overlap >= chunk_size:
        chunk_overlap = max(0, chunk_size - 1)
    step = max(1, chunk_size - chunk_overlap)
    chunks: list[str] = []
    start = 0
    text_len = len(source)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunk = source[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= text_len:
            break
        start += step
    return chunks


def _db_path(workspace_root: Path) -> Path:
    return workspace_root / DB_FILE_NAME


def clear_docs_index_database(workspace_root: str | Path) -> None:
    """
    Remove the vector index SQLite file (and WAL/SHM sidecars if present).
    Used e.g. on builder startup for a clean RAG state.
    """
    root = Path(workspace_root).resolve()
    names = (
        DB_FILE_NAME,
        f"{DB_FILE_NAME}-wal",
        f"{DB_FILE_NAME}-shm",
    )
    for name in names:
        path = root / name
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass


def _open_db(workspace_root: Path) -> sqlite3.Connection:
    db_path = _db_path(workspace_root)
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        import sqlite_vec
    except ImportError as exc:
        raise RuntimeError("sqlite-vec is not installed. Run `pip install sqlite-vec`.") from exc
    try:
        connection.enable_load_extension(True)
    except Exception as exc:
        raise RuntimeError(
            "This Python sqlite build does not allow loading SQLite extensions required by sqlite-vec."
        ) from exc
    try:
        sqlite_vec.load(connection)
    finally:
        try:
            connection.enable_load_extension(False)
        except Exception:
            pass
    return connection


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS docs_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL,
            rel_path TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS docs_index_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS docs_chunks_vec USING vec0(
            embedding float[384]
        );
        """
    )


def _read_meta(connection: sqlite3.Connection) -> dict[str, str]:
    rows = connection.execute("SELECT key, value FROM docs_index_meta").fetchall()
    return {str(row["key"]): str(row["value"]) for row in rows}


def _build_chunk_rows(
    *,
    file_path: Path,
    rel_path: str,
    text: str,
    start_chunk_index: int = 0,
) -> list[tuple[str, str, int, str]]:
    chunks = _split_text_into_chunks(text, CHUNK_SIZE, CHUNK_OVERLAP)
    return [
        (str(file_path), rel_path, start_chunk_index + chunk_index, chunk_text)
        for chunk_index, chunk_text in enumerate(chunks)
    ]


def _insert_chunk_rows(
    connection: sqlite3.Connection,
    chunks: list[tuple[str, str, int, str]],
) -> None:
    if not chunks:
        return

    embedder = _get_embedder()
    vectors = embedder.encode(
        [chunk_text for _, _, _, chunk_text in chunks],
        normalize_embeddings=True,
        convert_to_numpy=True,
    )

    for index, (file_path, rel_path, chunk_index, chunk_text) in enumerate(chunks):
        cur = connection.execute(
            """
            INSERT INTO docs_chunks(file_path, rel_path, chunk_index, chunk_text)
            VALUES (?, ?, ?, ?)
            """,
            (file_path, rel_path, chunk_index, chunk_text),
        )
        row_id = int(cur.lastrowid)
        connection.execute(
            "INSERT INTO docs_chunks_vec(rowid, embedding) VALUES (?, ?)",
            (row_id, _to_vec_literal(vectors[index].tolist())),
        )


def _delete_chunks_for_dir(connection: sqlite3.Connection, dir_name: str) -> None:
    rows = connection.execute(
        "SELECT id FROM docs_chunks WHERE rel_path LIKE ?",
        (f"{dir_name}/%",),
    ).fetchall()
    row_ids = [(int(row["id"]),) for row in rows]
    if not row_ids:
        return
    connection.executemany("DELETE FROM docs_chunks_vec WHERE rowid = ?", row_ids)
    connection.executemany("DELETE FROM docs_chunks WHERE id = ?", row_ids)


def _next_chunk_index_for_path(connection: sqlite3.Connection, rel_path: str) -> int:
    row = connection.execute(
        "SELECT MAX(chunk_index) AS max_chunk_index FROM docs_chunks WHERE rel_path = ?",
        (rel_path,),
    ).fetchone()
    if not row:
        return 0
    value = row["max_chunk_index"]
    return 0 if value is None else int(value) + 1


def get_docs_status(workspace_root: str | Path) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    docs_root = root / DOCS_DIR_NAME
    markdown_files = _iter_markdown_files(docs_root)
    chunk_count = 0
    processed_at = ""
    prepared = False

    try:
        with _open_db(root) as conn:
            _ensure_schema(conn)
            meta = _read_meta(conn)
            chunk_count = int(meta.get("chunk_count", "0") or "0")
            processed_at = meta.get("processed_at", "")
            prepared = chunk_count > 0
    except Exception:
        prepared = False
        chunk_count = 0
        processed_at = ""

    return {
        "docs_path": str(docs_root),
        "md_file_count": len(markdown_files),
        "prepared": prepared,
        "chunk_count": chunk_count,
        "model": EMBEDDING_MODEL_NAME,
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "processed_at": processed_at,
    }


def prepare_docs_index(workspace_root: str | Path) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    docs_root = root / DOCS_DIR_NAME
    markdown_files = _iter_markdown_files(docs_root)
    processed_at = datetime.now(timezone.utc).isoformat()

    chunks: list[tuple[str, str, int, str]] = []
    for file_path in markdown_files:
        rel_path = file_path.relative_to(root).as_posix()
        text = file_path.read_text(encoding="utf-8", errors="replace")
        chunks.extend(
            _build_chunk_rows(
                file_path=file_path,
                rel_path=rel_path,
                text=text,
            )
        )

    with _open_db(root) as conn:
        _ensure_schema(conn)
        conn.execute("BEGIN")
        _delete_chunks_for_dir(conn, DOCS_DIR_NAME)

        _insert_chunk_rows(conn, chunks)

        conn.execute("DELETE FROM docs_index_meta")
        meta_rows = [
            ("processed_at", processed_at),
            ("model", EMBEDDING_MODEL_NAME),
            ("chunk_size", str(CHUNK_SIZE)),
            ("chunk_overlap", str(CHUNK_OVERLAP)),
            ("md_file_count", str(len(markdown_files))),
            ("chunk_count", str(len(chunks))),
        ]
        conn.executemany("INSERT INTO docs_index_meta(key, value) VALUES (?, ?)", meta_rows)
        conn.commit()

    return {
        "docs": {
            "docs_path": str(docs_root),
            "md_file_count": len(markdown_files),
            "prepared": len(chunks) > 0,
            "chunk_count": len(chunks),
            "model": EMBEDDING_MODEL_NAME,
            "chunk_size": CHUNK_SIZE,
            "chunk_overlap": CHUNK_OVERLAP,
            "processed_at": processed_at,
        }
    }


def index_memory_content(
    workspace_root: str | Path,
    memory_file: str | Path,
    content: str,
) -> dict[str, Any]:
    text = (content or "").strip()
    if not text:
        return {"indexed": False, "chunk_count": 0}

    root = Path(workspace_root).resolve()
    file_path = Path(memory_file).expanduser().resolve()
    rel_path = file_path.relative_to(root).as_posix()
    if not rel_path.startswith(f"{MEMORY_DIR_NAME}/"):
        raise RuntimeError("Memory file must be located inside the memory directory.")

    with _open_db(root) as conn:
        _ensure_schema(conn)
        next_chunk_index = _next_chunk_index_for_path(conn, rel_path)
        chunks = _build_chunk_rows(
            file_path=file_path,
            rel_path=rel_path,
            text=text,
            start_chunk_index=next_chunk_index,
        )
        conn.execute("BEGIN")
        _insert_chunk_rows(conn, chunks)
        conn.commit()

    return {
        "indexed": bool(chunks),
        "chunk_count": len(chunks),
        "rel_path": rel_path,
    }


def search_relevant_chunks(
    workspace_root: str | Path,
    queries: list[str],
    *,
    top_k_per_query: int = 3,
    min_similarity: float = 0.85,
) -> list[dict[str, Any]]:
    clean_queries = [q.strip() for q in queries if isinstance(q, str) and q.strip()]
    if not clean_queries:
        return []

    root = Path(workspace_root).resolve()
    with _open_db(root) as conn:
        _ensure_schema(conn)
        docs_count = int(
            conn.execute("SELECT COUNT(*) AS c FROM docs_chunks").fetchone()["c"]
        )
        if docs_count == 0:
            return []

        embedder = _get_embedder()
        query_vectors = embedder.encode(
            clean_queries,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        gathered: list[dict[str, Any]] = []
        top_k = max(1, int(top_k_per_query))

        for idx, query in enumerate(clean_queries):
            query_literal = _to_vec_literal(query_vectors[idx].tolist())

            rows = conn.execute(
                f"""
                SELECT
                    c.id,
                    c.file_path,
                    c.rel_path,
                    c.chunk_index,
                    c.chunk_text,
                    v.distance AS distance
                FROM docs_chunks_vec v
                JOIN docs_chunks c ON c.id = v.rowid
                WHERE v.embedding MATCH ?
                AND k = {top_k}
                ORDER BY v.distance ASC
                """,
                (query_literal,),
            ).fetchall()

            for row in rows:
                similarity = 1.0 - float(row["distance"] or 0.0)
                if similarity <= min_similarity:
                    continue
                gathered.append(
                    {
                        "query": query,
                        "id": int(row["id"]),
                        "file_path": str(row["file_path"]),
                        "rel_path": str(row["rel_path"]),
                        "chunk_index": int(row["chunk_index"]),
                        "chunk_text": str(row["chunk_text"]),
                        "similarity": similarity,
                    }
                )

    return gathered

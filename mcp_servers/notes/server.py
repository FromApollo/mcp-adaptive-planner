import os
import sqlite3
from datetime import datetime
from mcp.server.fastmcp.server import FastMCP

DB_PATH = os.getenv("NOTES_DB", "data/notes.db")

mcp = FastMCP("notes")


def _db():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT UNIQUE NOT NULL,
        content TEXT DEFAULT '',
        tags TEXT DEFAULT '',
        created_at TEXT,
        updated_at TEXT
    )""")
    conn.commit()
    return conn


@mcp.tool()
def create_note(title: str, content: str, tags: str = "") -> str:
    """Create or update a note with a title, content and optional comma-separated tags."""
    now = datetime.now().isoformat(timespec="seconds")
    with _db() as conn:
        conn.execute(
            """INSERT INTO notes (title, content, tags, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(title) DO UPDATE SET
                   content=excluded.content,
                   tags=excluded.tags,
                   updated_at=excluded.updated_at""",
            (title, content, tags, now, now),
        )
    return f"Note '{title}' saved."


@mcp.tool()
def get_note(title: str) -> str:
    """Get the full content of a note by its title."""
    with _db() as conn:
        row = conn.execute(
            "SELECT content, tags, updated_at FROM notes WHERE title=?", (title,)
        ).fetchone()
    if row:
        return f"Title: {title}\nTags: {row[1]}\nUpdated: {row[2]}\n\n{row[0]}"
    return f"Note '{title}' not found."


@mcp.tool()
def search_notes(query: str) -> str:
    """Search notes by keyword in title, content or tags."""
    like = f"%{query}%"
    with _db() as conn:
        rows = conn.execute(
            "SELECT title, tags, updated_at FROM notes "
            "WHERE title LIKE ? OR content LIKE ? OR tags LIKE ?",
            (like, like, like),
        ).fetchall()
    if not rows:
        return "No notes found."
    return "\n".join(f"- {r[0]}  [tags: {r[1]}]  (updated: {r[2]})" for r in rows)


@mcp.tool()
def list_notes() -> str:
    """List all saved notes (title, tags, last updated)."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT title, tags, updated_at FROM notes ORDER BY updated_at DESC"
        ).fetchall()
    if not rows:
        return "No notes yet."
    return "\n".join(f"- {r[0]}  [tags: {r[1]}]  (updated: {r[2]})" for r in rows)


if __name__ == "__main__":
    mcp.run()

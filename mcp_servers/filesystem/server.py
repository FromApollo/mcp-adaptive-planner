import os
from mcp.server.fastmcp.server import FastMCP

mcp = FastMCP("filesystem")


@mcp.tool()
def read_file(path: str) -> str:
    """Read the contents of a file and return as text."""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Write text content to a file. Creates parent directories if needed."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Successfully written to {path}"


@mcp.tool()
def list_dir(path: str = ".") -> str:
    """List files and folders in a directory."""
    entries = os.listdir(path)
    return "\n".join(entries)


if __name__ == "__main__":
    mcp.run()

#!/usr/bin/env python3
"""
MCP Server for Apple Developer Documentation Offline Archive.

Provides tools to list frameworks, search documents, read document contents,
browse symbols, and extract metadata from the markdown documentation tree.

Run with: python mcp_server.py
Configure host/port via APPLE_DOCS_MCP_HOST / APPLE_DOCS_MCP_PORT env vars.
"""

import logging
import os
import re
import subprocess
from pathlib import Path

import yaml
from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("apple-docs-mcp")

SCRIPT_DIR = Path(__file__).resolve().parent
MARKDOWN_DIR = SCRIPT_DIR / "markdown"

HOST = os.environ.get("APPLE_DOCS_MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("APPLE_DOCS_MCP_PORT", "9111"))

mcp = FastMCP("apple-docs", host=HOST, port=PORT)


def _validate_framework(framework: str) -> tuple[Path, str | None]:
    """Validate a framework name and return (path, error_message)."""
    framework_dir = MARKDOWN_DIR / framework.lower()
    if not framework_dir.exists() or not framework_dir.is_dir():
        available = sorted(
            d.name for d in MARKDOWN_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")
        )
        return framework_dir, f"Framework '{framework}' not found. Available: {', '.join(available)}"
    return framework_dir, None


def _validate_doc_path(path: str) -> tuple[Path, str | None]:
    """Validate a document path and return (resolved_path, error_message)."""
    if ".." in path or path.startswith("/"):
        return Path(), "Invalid path. Use paths relative to the markdown directory."
    doc_path = MARKDOWN_DIR / path
    if not doc_path.exists() and (MARKDOWN_DIR / path.lower()).exists():
        doc_path = MARKDOWN_DIR / path.lower()  # archive paths are lowercase
    try:
        doc_path.resolve().relative_to(MARKDOWN_DIR.resolve())
    except ValueError:
        return doc_path, "Path resolves outside the documentation directory."
    if not doc_path.exists():
        return doc_path, f"Document not found: {path}"
    if not doc_path.is_file():
        return doc_path, f"Path is a directory, not a file: {path}. Use list_symbols to browse directories."
    return doc_path, None


def _parse_frontmatter(text: str) -> dict | None:
    """Extract YAML frontmatter from markdown text."""
    if not text.startswith("---"):
        return None
    end = text.find("---", 3)
    if end == -1:
        return None
    try:
        return yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return None


# ---------------------------------------------------------------------------
# Tool 1: List frameworks
# ---------------------------------------------------------------------------
@mcp.tool()
def list_frameworks() -> str:
    """List all Apple frameworks that have documentation available.

    Returns framework names that can be used with search_documents,
    list_symbols, and other tools.
    """
    if not MARKDOWN_DIR.exists():
        return "Error: Markdown directory not found. Documentation may not be downloaded yet."

    frameworks = sorted(
        d.name for d in MARKDOWN_DIR.iterdir() if d.is_dir() and not d.name.startswith(".")
    )
    if not frameworks:
        return "No frameworks found."

    lines = [f"Available frameworks ({len(frameworks)}):"]
    for fw in frameworks:
        fw_path = MARKDOWN_DIR / fw
        doc_count = sum(1 for _ in fw_path.rglob("*.md"))
        lines.append(f"  {fw} ({doc_count:,} documents)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 2: Search documents in a framework
# ---------------------------------------------------------------------------
@mcp.tool()
def search_documents(
    framework: str,
    query: str,
    case_insensitive: bool = True,
    max_results: int = 50,
) -> str:
    """Search for text within documents of a specific Apple framework.

    Returns document paths and line numbers where matches are found.
    Paths returned can be passed directly to read_document.

    Args:
        framework: Framework name (e.g. 'swiftui', 'foundation'). Use list_frameworks to see options.
        query: Text or regex pattern to search for.
        case_insensitive: Ignore case when searching (default True).
        max_results: Maximum matching lines to return (default 50, max 200).
    """
    framework_dir, err = _validate_framework(framework)
    if err:
        return f"Error: {err}"
    if not query or not query.strip():
        return "Error: Search query cannot be empty."

    max_results = max(1, min(max_results, 200))

    cmd = ["grep", "-rn", "--include=*.md", "-m", "5"]
    if case_insensitive:
        cmd.append("-i")
    cmd.extend(["--", query, str(framework_dir)])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return "Error: Search timed out. Try a more specific query."

    if result.returncode == 1:
        return f"No matches found for '{query}' in {framework}."
    if result.returncode not in (0, 1):
        return f"Error: Search failed (code {result.returncode})."

    raw_lines = result.stdout.strip().split("\n")
    if not raw_lines or raw_lines == [""]:
        return f"No matches found for '{query}' in {framework}."

    output = []
    for line in raw_lines:
        if len(output) >= max_results:
            break
        parts = line.split(":", 2)
        if len(parts) >= 3:
            rel = os.path.relpath(parts[0], MARKDOWN_DIR)
            text = parts[2][:200] + "..." if len(parts[2]) > 200 else parts[2]
            output.append(f"{rel}:{parts[1]}: {text}")
        else:
            output.append(line)

    total = len(raw_lines)
    header = f"Results for '{query}' in {framework} ({len(output)}"
    if total > len(output):
        header += f" of {total}+"
    header += " matches):\n"
    return header + "\n".join(output)


# ---------------------------------------------------------------------------
# Tool 3: Read a document
# ---------------------------------------------------------------------------
@mcp.tool()
def read_document(
    path: str,
    offset: int = 1,
    limit: int = 0,
) -> str:
    """Read the contents of a documentation file with line numbers.

    Args:
        path: Document path relative to markdown/ (e.g. 'swiftui/AccessibilityActionCategory.md').
        offset: Line number to start from (1-based, default 1).
        limit: Number of lines to return (0 = entire file up to 500 lines, default 0).
    """
    doc_path, err = _validate_doc_path(path)
    if err:
        return f"Error: {err}"

    try:
        all_lines = doc_path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        return f"Error reading file: {e}"

    total = len(all_lines)
    if total == 0:
        return f"Document '{path}' is empty."

    offset = max(1, offset)
    if offset > total:
        return f"Error: Offset {offset} exceeds document length ({total} lines)."

    if limit <= 0:
        limit = 500
    limit = min(limit, 500)

    start = offset - 1
    end = min(start + limit, total)
    selected = all_lines[start:end]

    numbered = [f"{i:>5} | {line}" for i, line in enumerate(selected, start=offset)]

    header = f"Document: {path} (lines {offset}-{end} of {total})\n"
    if end < total:
        header += f"(Use offset={end + 1} to continue reading.)\n"
    return header + "\n".join(numbered)


# ---------------------------------------------------------------------------
# Tool 4: List symbols in a framework (browse the doc tree)
# ---------------------------------------------------------------------------
@mcp.tool()
def list_symbols(
    framework: str,
    path: str = "",
) -> str:
    """Browse the documentation tree for a framework.

    Lists types, classes, structs, protocols, and their members.
    Call with just a framework to see top-level symbols, then drill
    into subdirectories to see members.

    Args:
        framework: Framework name (e.g. 'swiftui').
        path: Subdirectory path within the framework to list (default: top level).
              e.g. 'NavigationStack' to list its members.
    """
    framework_dir, err = _validate_framework(framework)
    if err:
        return f"Error: {err}"

    target = framework_dir / path if path else framework_dir
    if path and not target.exists():
        target = framework_dir / path.lower()  # archive paths are lowercase
    if not target.exists() or not target.is_dir():
        return f"Error: Path '{path}' not found in {framework}."

    dirs = sorted(d.name for d in target.iterdir() if d.is_dir() and not d.name.startswith("."))
    files = sorted(
        f.name for f in target.iterdir() if f.is_file() and f.suffix == ".md"
    )

    rel_base = f"{framework}/{path}" if path else framework
    lines = [f"Contents of {rel_base}/ ({len(dirs)} subdirectories, {len(files)} documents):"]

    if dirs:
        lines.append("\nSubdirectories (types with members):")
        for d in dirs:
            sub_count = sum(1 for _ in (target / d).rglob("*.md"))
            lines.append(f"  {d}/ ({sub_count} docs)")

    if files:
        lines.append("\nDocuments:")
        for f in files:
            lines.append(f"  {f}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 5: Get symbol metadata from frontmatter
# ---------------------------------------------------------------------------
@mcp.tool()
def get_symbol_info(path: str) -> str:
    """Extract structured metadata from a documentation file's frontmatter.

    Returns the symbol's title, type (struct/class/protocol/etc), module,
    platform availability, and deprecation status. Much faster than
    reading the full document when you just need metadata.

    Args:
        path: Document path relative to markdown/ (e.g. 'swiftui/AccessibilityActionCategory.md').
    """
    doc_path, err = _validate_doc_path(path)
    if err:
        return f"Error: {err}"

    try:
        text = doc_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"

    meta = _parse_frontmatter(text)
    if not meta:
        return f"No metadata found in {path}."

    lines = [f"Symbol: {meta.get('title', 'Unknown')}"]
    if meta.get("roleHeading"):
        lines.append(f"Type: {meta['roleHeading']}")
    if meta.get("role"):
        lines.append(f"Role: {meta['role']}")

    modules = meta.get("modules", [])
    if modules:
        lines.append(f"Module: {', '.join(m.get('name', '') for m in modules)}")

    platforms = meta.get("platforms", [])
    if platforms:
        lines.append("\nPlatform availability:")
        for p in platforms:
            name = p.get("name", "Unknown")
            introduced = p.get("introducedAt", "?")
            status = []
            if p.get("deprecated"):
                status.append("DEPRECATED")
            if p.get("beta"):
                status.append("beta")
            if p.get("unavailable"):
                status.append("unavailable")
            suffix = f" ({', '.join(status)})" if status else ""
            lines.append(f"  {name} {introduced}+{suffix}")

    # Extract the declaration from the markdown body
    decl_match = re.search(r"```swift\n(.+?)```", text, re.DOTALL)
    if decl_match:
        decl = decl_match.group(1).strip()
        lines.append(f"\nDeclaration:\n  {decl}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 6: Search across all frameworks
# ---------------------------------------------------------------------------
@mcp.tool()
def search_all_frameworks(
    query: str,
    case_insensitive: bool = True,
    max_results: int = 30,
) -> str:
    """Search for text across ALL frameworks at once.

    Useful when you don't know which framework contains a symbol.
    For targeted searches, prefer search_documents with a specific framework.

    Args:
        query: Text or regex pattern to search for.
        case_insensitive: Ignore case (default True).
        max_results: Maximum matching lines to return (default 30, max 100).
    """
    if not MARKDOWN_DIR.exists():
        return "Error: Markdown directory not found."
    if not query or not query.strip():
        return "Error: Search query cannot be empty."

    max_results = max(1, min(max_results, 100))

    cmd = ["grep", "-rn", "--include=*.md", "-m", "3"]
    if case_insensitive:
        cmd.append("-i")
    cmd.extend(["--", query, str(MARKDOWN_DIR)])

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return "Error: Search timed out. Try a more specific query."

    if result.returncode == 1:
        return f"No matches found for '{query}' across any framework."
    if result.returncode not in (0, 1):
        return f"Error: Search failed (code {result.returncode})."

    raw_lines = result.stdout.strip().split("\n")
    if not raw_lines or raw_lines == [""]:
        return f"No matches found for '{query}'."

    output = []
    for line in raw_lines:
        if len(output) >= max_results:
            break
        parts = line.split(":", 2)
        if len(parts) >= 3:
            rel = os.path.relpath(parts[0], MARKDOWN_DIR)
            text = parts[2][:200] + "..." if len(parts[2]) > 200 else parts[2]
            output.append(f"{rel}:{parts[1]}: {text}")
        else:
            output.append(line)

    total = len(raw_lines)
    header = f"Results for '{query}' across all frameworks ({len(output)}"
    if total > len(output):
        header += f" of {total}+"
    header += " matches):\n"
    return header + "\n".join(output)


# ---------------------------------------------------------------------------
# Tool 7: Get related symbols (See Also / Topics links)
# ---------------------------------------------------------------------------
@mcp.tool()
def get_related_symbols(path: str) -> str:
    """Extract related symbols and topic links from a documentation file.

    Parses the 'Topics' and 'See Also' sections to find related APIs,
    useful for discovering related functionality.

    Args:
        path: Document path relative to markdown/ (e.g. 'swiftui/AccessibilityActionCategory.md').
    """
    doc_path, err = _validate_doc_path(path)
    if err:
        return f"Error: {err}"

    try:
        text = doc_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"Error reading file: {e}"

    # Strip frontmatter
    body = text
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            body = text[end + 3:]

    lines = []

    # Parse Topics sections
    topics_match = re.search(r"^## Topics\s*$(.*?)(?=^## |\Z)", body, re.MULTILINE | re.DOTALL)
    if topics_match:
        topics_text = topics_match.group(1)
        current_heading = None
        for line in topics_text.strip().splitlines():
            line = line.strip()
            heading_match = re.match(r"^### (.+)$", line)
            if heading_match:
                current_heading = heading_match.group(1)
                lines.append(f"\n{current_heading}:")
            elif line.startswith("- ["):
                link_match = re.match(r"- \[(.+?)\]\((.+?)\)", line)
                if link_match:
                    name = link_match.group(1)
                    href = link_match.group(2)
                    # Convert relative link to a usable doc path
                    try:
                        resolved = (doc_path.parent / href).resolve()
                        rel = os.path.relpath(resolved, MARKDOWN_DIR)
                        lines.append(f"  {name} -> {rel}")
                    except (ValueError, OSError):
                        lines.append(f"  {name}")

    # Parse See Also section
    see_also_match = re.search(r"^## See Also\s*$(.*?)(?=^## |\Z)", body, re.MULTILINE | re.DOTALL)
    if see_also_match:
        lines.append("\nSee Also:")
        for line in see_also_match.group(1).strip().splitlines():
            line = line.strip()
            if line.startswith("- ["):
                link_match = re.match(r"- \[(.+?)\]\((.+?)\)", line)
                if link_match:
                    name = link_match.group(1)
                    href = link_match.group(2)
                    try:
                        resolved = (doc_path.parent / href).resolve()
                        rel = os.path.relpath(resolved, MARKDOWN_DIR)
                        lines.append(f"  {name} -> {rel}")
                    except (ValueError, OSError):
                        lines.append(f"  {name}")

    if not lines:
        return f"No related symbols found in {path}."

    meta = _parse_frontmatter(text)
    title = meta.get("title", path) if meta else path
    header = f"Related symbols for {title}:"
    return header + "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 8: Lookup a symbol by name across frameworks
# ---------------------------------------------------------------------------
@mcp.tool()
def lookup_symbol(
    name: str,
    framework: str = "",
) -> str:
    """Find a symbol (type, method, property) by name.

    Searches file and directory names for matching symbols. Faster than
    full-text search when you know the symbol name.

    Args:
        name: Symbol name to look for (e.g. 'NavigationStack', 'onChange', 'URLSession').
        framework: Limit search to a specific framework (optional).
    """
    if not MARKDOWN_DIR.exists():
        return "Error: Markdown directory not found."

    search_root = MARKDOWN_DIR
    if framework:
        fw_dir, err = _validate_framework(framework)
        if err:
            return f"Error: {err}"
        search_root = fw_dir

    # Use find via pathlib to match filenames and directory names
    name_lower = name.lower()
    matches_exact = []
    matches_partial = []

    for item in search_root.rglob("*"):
        if item.name.startswith("."):
            continue
        item_name = item.stem.lower() if item.is_file() else item.name.lower()
        if item_name == name_lower:
            rel = os.path.relpath(item, MARKDOWN_DIR)
            kind = "dir" if item.is_dir() else "file"
            matches_exact.append((rel, kind))
        elif name_lower in item_name and item.suffix in (".md", ""):
            rel = os.path.relpath(item, MARKDOWN_DIR)
            kind = "dir" if item.is_dir() else "file"
            matches_partial.append((rel, kind))

        if len(matches_exact) + len(matches_partial) > 100:
            break

    if not matches_exact and not matches_partial:
        return f"No symbols matching '{name}' found."

    lines = [f"Symbols matching '{name}':"]

    if matches_exact:
        lines.append("\nExact matches:")
        for rel, kind in sorted(matches_exact):
            marker = "/" if kind == "dir" else ""
            lines.append(f"  {rel}{marker}")

    if matches_partial:
        shown = matches_partial[:30]
        lines.append(f"\nPartial matches ({len(matches_partial)} total):")
        for rel, kind in sorted(shown):
            marker = "/" if kind == "dir" else ""
            lines.append(f"  {rel}{marker}")
        if len(matches_partial) > 30:
            lines.append(f"  ... and {len(matches_partial) - 30} more")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if not MARKDOWN_DIR.exists():
        logger.warning("Markdown directory not found at %s", MARKDOWN_DIR)
        logger.warning("Tools will return errors until documentation is downloaded.")
    else:
        fw_count = sum(1 for d in MARKDOWN_DIR.iterdir() if d.is_dir() and not d.name.startswith("."))
        logger.info("Starting Apple Docs MCP server with %d frameworks from %s", fw_count, MARKDOWN_DIR)

    logger.info("Listening on %s:%d", HOST, PORT)
    mcp.run(transport="streamable-http")

#!/usr/bin/env python3
"""
MCP Server for Apple Developer Documentation Offline Archive.

Exposes the markdown/ tree: ranked symbol search, exact symbol lookup,
full-text search, document reading (with availability, deprecation and links
rewritten to archive paths), metadata, related symbols and relationships.

Run with: python mcp_server.py
Configuration (environment variables):
  APPLE_DOCS_MCP_HOST / APPLE_DOCS_MCP_PORT   listen address (default 0.0.0.0:9111)
  APPLE_DOCS_MARKDOWN_DIR                     archive to serve (default ./markdown)
  APPLE_DOCS_INDEX                            search index file (default .mcp-index.sqlite
                                              next to the markdown directory)
  APPLE_DOCS_INDEX_REFRESH                    seconds between index refreshes (default 3600)
"""

import functools
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import anyio
import yaml
from mcp.server.fastmcp import FastMCP

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("apple-docs-mcp")

SCRIPT_DIR = Path(__file__).resolve().parent
MARKDOWN_DIR = Path(os.environ.get("APPLE_DOCS_MARKDOWN_DIR", SCRIPT_DIR / "markdown")).resolve()
INDEX_PATH = Path(os.environ.get("APPLE_DOCS_INDEX", MARKDOWN_DIR.parent / ".mcp-index.sqlite"))
INDEX_REFRESH = int(os.environ.get("APPLE_DOCS_INDEX_REFRESH", "3600"))

HOST = os.environ.get("APPLE_DOCS_MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("APPLE_DOCS_MCP_PORT", "9111"))

DOCS_SITE = "https://developer.apple.com/documentation/"
YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)  # libyaml is ~10x faster

mcp = FastMCP("apple-docs", host=HOST, port=PORT)


def tool(fn):
    """Register fn as an MCP tool that runs in a worker thread, so a slow call
    (a full-text search) doesn't block other clients."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
    return mcp.tool()(wrapper)


# ---------------------------------------------------------------------------
# Paths: every tool accepts archive paths ('appkit/nsview' or 'appkit/nsview.md'),
# doc:// identifiers, /documentation/ URLs and developer.apple.com URLs.
# Tools return archive paths without the .md suffix.
# ---------------------------------------------------------------------------
def to_doc_path(ref: str) -> str:
    """Normalize any reference to a page into a lowercase archive doc path."""
    s = ref.strip().strip("<>").strip()
    if s.startswith("doc://"):
        s = s.split("/documentation/", 1)[1] if "/documentation/" in s else ""
    elif "://" in s:
        m = re.match(r"[a-z]+://[^/]+/(.*)", s)
        s = m.group(1) if m else ""
        if s.startswith("tutorials/data/"):
            s = s[len("tutorials/data/"):]
        s = s.split("?", 1)[0].split("#", 1)[0]
        if s.endswith(".json"):
            s = s[:-5]
    s = s.split("#", 1)[0].lstrip("/")
    for prefix in ("documentation/", "markdown/"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    if s.endswith(".md"):
        s = s[:-3]
    return s.strip("/").lower()


def doc_file(doc_path: str) -> Path | None:
    """The .md file for a doc path, or None if it isn't in the archive (or escapes it)."""
    if not doc_path or any(part in ("", ".", "..") for part in doc_path.split("/")):
        return None
    f = MARKDOWN_DIR / f"{doc_path}.md"
    try:
        f.resolve().relative_to(MARKDOWN_DIR)
    except ValueError:
        return None
    return f if f.is_file() else None


def resolve_doc(ref: str) -> tuple[str, Path | None, str | None]:
    """Resolve a reference to (doc_path, file, error)."""
    doc_path = to_doc_path(ref)
    if not doc_path or any(part in ("", ".", "..") for part in doc_path.split("/")):
        return doc_path, None, f"Not a documentation path: {ref!r}"
    f = doc_file(doc_path)
    if f:
        return doc_path, f, None
    if (MARKDOWN_DIR / doc_path).is_dir():
        return doc_path, None, f"'{doc_path}' has no page of its own. Use list_symbols to browse it."
    msg = f"Document not found: {doc_path}"
    suggestions = index.suggest(doc_path)
    if suggestions:
        msg += "\nDid you mean:\n" + "\n".join(f"  {p}" for p in suggestions)
    return doc_path, None, msg


def link_target(dest: str, base_doc_path: str) -> str:
    """Rewrite a link destination found in a document: archive pages become doc
    paths (usable with read_document); pages that aren't in the archive become
    developer.apple.com URLs; other external links are unchanged."""
    d = dest.strip()
    if d.startswith("<") and d.endswith(">"):
        d = d[1:-1]
    if d.startswith("#"):
        return dest
    if d.startswith(("http://", "https://")) and "developer.apple.com/documentation/" not in d:
        return d
    if d.startswith(("doc://", "http://", "https://", "/documentation/")):
        target = to_doc_path(d)
        if d.startswith("doc://") and "/documentation/" not in d:
            return d
    elif d.endswith(".md") or ".md#" in d:
        rel = d.split("#", 1)[0]
        target = os.path.normpath(os.path.join(os.path.dirname(base_doc_path), rel))
        target = to_doc_path(target)
    else:
        return d
    if target.startswith("..") or not target:
        return d
    return target if doc_file(target) else DOCS_SITE + target


def _find_link_end(text: str, start: int) -> int:
    """Given the index just after '](' return the index of the closing ')' (or -1)."""
    if start < len(text) and text[start] == "<":
        end = text.find(">", start)
        return text.find(")", end) if end != -1 else -1
    depth = 0
    for i in range(start, len(text)):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            if depth == 0:
                return i
            depth -= 1
        elif c in " \n":
            return -1
    return -1


def iter_links(line: str):
    """Yield (text, dest, start, end, is_image) for each Markdown link in a line,
    handling balanced parentheses in destinations like init(frame:).md."""
    pos = 0
    while True:
        mid = line.find("](", pos)
        if mid == -1:
            return
        # find the matching '[' for this link text (allow nested brackets)
        depth, open_i = 0, -1
        for i in range(mid - 1, -1, -1):
            if line[i] == "]":
                depth += 1
            elif line[i] == "[":
                if depth == 0:
                    open_i = i
                    break
                depth -= 1
        end = _find_link_end(line, mid + 2)
        if open_i == -1 or end == -1:
            pos = mid + 2
            continue
        is_image = open_i > 0 and line[open_i - 1] == "!"
        yield line[open_i + 1:mid], line[mid + 2:end], open_i, end + 1, is_image
        pos = end + 1


def rewrite_links(line: str, base_doc_path: str) -> str:
    out, pos = [], 0
    for text, dest, start, end, is_image in iter_links(line):
        if is_image:
            continue
        out.append(line[pos:start])
        if text.startswith("doc://"):  # older Markdown used the raw identifier as link text
            ident, _, anchor = text.partition("#")
            text = ident.rstrip("/").rsplit("/", 1)[-1]
            if anchor:
                text += f" ({anchor.replace('-', ' ')})"
        out.append(f"[{text}]({link_target(dest, base_doc_path)})")
        pos = end
    out.append(line[pos:])
    return "".join(out)


def rewrite_doc_lines(lines: list[str], base_doc_path: str, start_in_fence: bool = False) -> list[str]:
    """Rewrite links in document lines, leaving fenced code blocks alone."""
    result, fence = [], "```" if start_in_fence else None
    for line in lines:
        stripped = line.lstrip()
        if fence:
            if stripped.startswith(fence):
                fence = None
            result.append(line)
        elif stripped.startswith("```"):
            fence = stripped[:len(stripped) - len(stripped.lstrip("`"))]
            result.append(line)
        elif "](" in line:
            result.append(rewrite_links(line, base_doc_path))
        else:
            result.append(line)
    return result


def in_fence_at(lines: list[str], index: int) -> bool:
    fence = None
    for line in lines[:index]:
        s = line.lstrip()
        if fence:
            if s.startswith(fence):
                fence = None
        elif s.startswith("```"):
            fence = s[:len(s) - len(s.lstrip("`"))]
    return fence is not None


# ---------------------------------------------------------------------------
# Document parsing
# ---------------------------------------------------------------------------
def split_frontmatter(text: str) -> tuple[dict, int]:
    """Return (frontmatter dict, number of lines it occupies including delimiters)."""
    if not text.startswith("---\n"):
        return {}, 0
    end = text.find("\n---\n", 3)
    if end == -1:
        return {}, 0
    try:
        meta = yaml.load(text[4:end], Loader=YAML_LOADER) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), text[:end + 5].count("\n")


def first_paragraph(body: str) -> str:
    """Fallback abstract for older Markdown without an 'abstract' field."""
    seen_title = False
    for para in body.split("\n\n"):
        p = para.strip()
        if not p:
            continue
        if p.startswith("# "):
            seen_title = True
            continue
        if seen_title and not p.startswith(("#", "```", "**Platforms", ">", "- ", "|")):
            return " ".join(p.split())[:400]
        if p.startswith("## "):
            break
    return ""


def is_deprecated(meta: dict) -> bool:
    if meta.get("deprecated"):
        return True
    available = [p for p in meta.get("platforms") or [] if isinstance(p, dict) and not p.get("unavailable")]
    return bool(available) and all(p.get("deprecated") or "deprecatedAt" in p for p in available)


def format_availability(meta: dict) -> list[str]:
    lines = []
    for p in meta.get("platforms") or []:
        if not isinstance(p, dict):
            continue
        s = p.get("name", "Unknown")
        if p.get("introducedAt"):
            s += f" {p['introducedAt']}+"
        notes = []
        if p.get("deprecatedAt"):
            notes.append(f"deprecated in {p['deprecatedAt']}")
        elif p.get("deprecated"):
            notes.append("deprecated")
        if p.get("beta"):
            notes.append("beta")
        if p.get("unavailable"):
            notes.append("unavailable")
        lines.append(s + (f" ({'; '.join(notes)})" if notes else ""))
    return lines


def availability_messages(meta: dict) -> list[str]:
    """Distinct per-platform deprecation messages (usually all the same)."""
    seen = []
    for p in meta.get("platforms") or []:
        if isinstance(p, dict) and p.get("message") and p["message"] not in seen:
            seen.append(p["message"])
    return seen


def sections(body: str) -> dict[str, list[str]]:
    """Split a document body into '## ' sections -> lines (fence-aware)."""
    result: dict[str, list[str]] = {}
    current, fence = None, None
    for line in body.split("\n"):
        s = line.lstrip()
        if fence:
            if s.startswith(fence):
                fence = None
        elif s.startswith("```"):
            fence = s[:len(s) - len(s.lstrip("`"))]
        elif line.startswith("## "):
            current = line[3:].strip()
            result.setdefault(current, [])
            continue
        if current is not None:
            result[current].append(line)
    return result


def link_groups(section_lines: list[str], base_doc_path: str) -> list[tuple[str, list[str]]]:
    """Parse '### group' headings and '- [title](dest): abstract' items into
    [(group, ["title -> target (deprecated): abstract", ...])]."""
    groups: list[tuple[str, list[str]]] = []
    for line in section_lines:
        if line.startswith("### "):
            groups.append((line[4:].strip(), []))
        elif line.startswith("- "):
            links = list(iter_links(line))
            if not links:
                continue
            text, dest, _, end, _ = links[0]
            entry = f"{text.strip('`')} -> {link_target(dest, base_doc_path)}"
            rest = line[end:].strip()
            if rest.startswith("*(deprecated)*"):
                entry += " (deprecated)"
                rest = rest[len("*(deprecated)*"):].strip()
            if rest.startswith(":"):
                abstract = rest[1:].strip()
                entry += ": " + (abstract[:160] + "…" if len(abstract) > 160 else abstract)
            if not groups:
                groups.append(("", []))
            groups[-1][1].append(entry)
    return [g for g in groups if g[1]]


def format_groups(groups: list[tuple[str, list[str]]]) -> list[str]:
    out = []
    for title, entries in groups:
        if title:
            out.append(f"\n{title}:")
        out.extend(f"  {e}" for e in entries)
    return out


def load_doc(f: Path) -> tuple[str, dict, int]:
    text = f.read_text(encoding="utf-8")
    meta, fm_lines = split_frontmatter(text)
    return text, meta, fm_lines


# ---------------------------------------------------------------------------
# Search index (SQLite FTS5), built and refreshed in a background thread
# ---------------------------------------------------------------------------
SCHEMA_VERSION = 2


def camel_words(s: str) -> str:
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    return " ".join(re.findall(r"[A-Za-z0-9]+", s)).lower()


class SearchIndex:
    def __init__(self, path: Path):
        self.path = path
        self.ready = False
        self.status = "not started"
        self.lock = threading.Lock()

    # -- connections -------------------------------------------------------
    def _connect(self, readonly: bool = True) -> sqlite3.Connection:
        if readonly:
            con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=30)
        else:
            con = sqlite3.connect(self.path, timeout=30)
            con.execute("PRAGMA journal_mode=WAL")
        con.row_factory = sqlite3.Row
        return con

    def query(self, sql: str, params=()) -> list[sqlite3.Row]:
        with self._connect() as con:
            return con.execute(sql, params).fetchall()

    # -- building ----------------------------------------------------------
    def _create(self, con: sqlite3.Connection):
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            con.executescript("DROP TABLE IF EXISTS docs; DROP TABLE IF EXISTS docs_fts;")
        con.executescript(f"""
            CREATE TABLE IF NOT EXISTS docs (
                id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, parent TEXT NOT NULL,
                framework TEXT NOT NULL, name TEXT NOT NULL, stem TEXT NOT NULL,
                title TEXT, kind TEXT, role TEXT, abstract TEXT, deprecated INTEGER,
                mtime REAL);
            CREATE INDEX IF NOT EXISTS docs_name ON docs(name);
            CREATE INDEX IF NOT EXISTS docs_stem ON docs(stem);
            CREATE INDEX IF NOT EXISTS docs_parent ON docs(parent);
            CREATE INDEX IF NOT EXISTS docs_framework ON docs(framework);
            CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
                title, words, abstract, path, tokenize='unicode61 remove_diacritics 2');
            PRAGMA user_version = {SCHEMA_VERSION};
        """)

    @staticmethod
    def _parse(f: Path, doc_path: str) -> dict:
        with open(f, encoding="utf-8", errors="replace") as fh:
            head = fh.read(32768)
        meta, fm_lines = split_frontmatter(head)
        title = str(meta.get("title") or doc_path.rsplit("/", 1)[-1])
        abstract = meta.get("abstract")
        if not abstract:
            body = "\n".join(head.split("\n")[fm_lines:])
            abstract = first_paragraph(body)
        stem = doc_path.rsplit("/", 1)[-1]
        return {
            "path": doc_path,
            "parent": doc_path.rsplit("/", 1)[0] if "/" in doc_path else "",
            "framework": doc_path.split("/", 1)[0],
            "name": title.split("(", 1)[0].strip().lower(),
            "stem": stem.split("(", 1)[0],
            "title": title,
            "kind": meta.get("roleHeading") or meta.get("role") or "",
            "role": meta.get("role") or "",
            "abstract": str(abstract or ""),
            "deprecated": int(is_deprecated(meta)),
        }

    def refresh(self):
        """Bring the index up to date with the files on disk (incremental by mtime)."""
        if not MARKDOWN_DIR.is_dir():
            self.status = f"markdown directory not found: {MARKDOWN_DIR}"
            return
        with self.lock:
            t0 = time.time()
            con = self._connect(readonly=False)
            try:
                self._create(con)
                known = {r["path"]: (r["id"], r["mtime"]) for r in
                         con.execute("SELECT id, path, mtime FROM docs")}
                if known:
                    self.ready = True
                on_disk: dict[str, tuple[Path, float]] = {}
                for root, dirs, files in os.walk(MARKDOWN_DIR):
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                    for name in files:
                        if name.endswith(".md"):
                            p = Path(root, name)
                            rel = os.path.relpath(p, MARKDOWN_DIR)[:-3].replace(os.sep, "/")
                            on_disk[rel] = (p, p.stat().st_mtime)
                changed = [k for k, (_, m) in on_disk.items() if k not in known or known[k][1] != m]
                removed = [k for k in known if k not in on_disk]
                total = len(changed)
                for i, doc_path in enumerate(changed, 1):
                    f, mtime = on_disk[doc_path]
                    try:
                        row = self._parse(f, doc_path)
                    except OSError:
                        continue
                    if doc_path in known:
                        old_id = known[doc_path][0]
                        con.execute("DELETE FROM docs_fts WHERE rowid=?", (old_id,))
                        con.execute("DELETE FROM docs WHERE id=?", (old_id,))
                    cur = con.execute(
                        "INSERT INTO docs(path,parent,framework,name,stem,title,kind,role,abstract,deprecated,mtime)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (row["path"], row["parent"], row["framework"], row["name"], row["stem"],
                         row["title"], row["kind"], row["role"], row["abstract"], row["deprecated"], mtime))
                    words = camel_words(row["title"]) + " " + " ".join(re.findall(r"[a-z0-9]+", doc_path))
                    con.execute("INSERT INTO docs_fts(rowid,title,words,abstract,path) VALUES(?,?,?,?,?)",
                                (cur.lastrowid, row["title"], words, row["abstract"], doc_path))
                    if i % 5000 == 0:
                        con.commit()
                        self.status = f"indexing: {i:,} of {total:,} changed pages"
                        logger.info("Index: %s", self.status)
                for doc_path in removed:
                    con.execute("DELETE FROM docs_fts WHERE rowid=?", (known[doc_path][0],))
                    con.execute("DELETE FROM docs WHERE id=?", (known[doc_path][0],))
                con.commit()
                self.ready = True
                self.status = (f"ready: {len(on_disk):,} pages ({total:,} updated, {len(removed):,} removed "
                               f"in {time.time() - t0:.0f}s)")
                logger.info("Index %s", self.status)
            finally:
                con.close()

    def run_forever(self):
        while True:
            try:
                self.refresh()
            except Exception:
                logger.exception("Index refresh failed")
                self.status = "refresh failed; see server log"
            time.sleep(INDEX_REFRESH)

    def start(self):
        if self.path.exists():
            try:
                self.ready = bool(self.query("SELECT 1 FROM docs LIMIT 1"))
            except sqlite3.Error:
                self.ready = False
        threading.Thread(target=self.run_forever, name="index", daemon=True).start()

    def not_ready_message(self) -> str:
        return f"Search index is not ready yet ({self.status}). Try again shortly, or use search_documents."

    # -- queries -----------------------------------------------------------
    def suggest(self, doc_path: str, limit: int = 5) -> list[str]:
        """Pages a mistyped or moved doc path probably meant: same name elsewhere,
        then close spellings within the same framework."""
        if not self.ready or not doc_path:
            return []
        name = doc_path.rsplit("/", 1)[-1].split("(", 1)[0]
        framework = doc_path.split("/", 1)[0]
        try:
            parent = doc_path.rsplit("/", 1)[0] if "/" in doc_path else ""
            rows = self.query("SELECT path FROM docs WHERE stem=? OR name=? ORDER BY parent != ?, framework != ?,"
                              " deprecated, length(path) LIMIT ?", (name, name, parent, framework, limit))
            found = [r["path"] for r in rows]
            if len(found) < limit and len(name) >= 3:
                import difflib
                candidates = [r["path"] for r in self.query(
                    "SELECT path FROM docs WHERE framework=? AND substr(stem,1,2)=? LIMIT 20000",
                    (framework, name[:2]))]
                for p in difflib.get_close_matches(doc_path, candidates, n=limit, cutoff=0.75):
                    if p not in found:
                        found.append(p)
        except sqlite3.Error:
            return []
        return found[:limit]


index = SearchIndex(INDEX_PATH)


def framework_names() -> list[str]:
    return sorted(d.name for d in MARKDOWN_DIR.iterdir() if d.is_dir() and not d.name.startswith("."))


def check_framework(framework: str) -> tuple[str, str | None]:
    fw = to_doc_path(framework).split("/", 1)[0]
    if fw and (MARKDOWN_DIR / fw).is_dir():
        return fw, None
    import difflib
    names = framework_names()
    close = difflib.get_close_matches(fw, names, n=5, cutoff=0.5)
    squashed = fw.replace(" ", "").replace("-", "")
    close = [n for n in names if n == squashed] + [c for c in close if c != squashed]
    hint = f" Did you mean: {', '.join(close)}?" if close else ""
    return fw, f"Framework '{framework}' not found.{hint} Use list_frameworks to see all {len(names)}."


def format_row(r: sqlite3.Row) -> str:
    s = f"{r['title']} — {r['kind'] or 'page'} [{r['path']}]"
    if r["deprecated"]:
        s += " (DEPRECATED)"
    if r["abstract"]:
        a = r["abstract"]
        s += f"\n    {a[:200] + '…' if len(a) > 200 else a}"
    return s


# ---------------------------------------------------------------------------
# Tool: list frameworks
# ---------------------------------------------------------------------------
@tool
def list_frameworks() -> str:
    """List every Apple framework/technology in the archive with its display name
    and page count. The short names (e.g. 'swiftui', 'coredata') are what other
    tools take as `framework`."""
    if not MARKDOWN_DIR.exists():
        return "Error: Markdown directory not found. Documentation may not be downloaded yet."
    names = framework_names()
    if not names:
        return "No frameworks found."
    counts, titles = {}, {}
    if index.ready:
        counts = {r["framework"]: r["n"] for r in
                  index.query("SELECT framework, count(*) AS n FROM docs GROUP BY framework")}
        titles = {r["path"]: r["title"] for r in index.query("SELECT path, title FROM docs WHERE parent=''")}
    lines = [f"Available frameworks ({len(names)}):"]
    for fw in names:
        title = titles.get(fw)
        line = f"  {fw}"
        if title and title.lower().replace(" ", "") != fw:
            line += f" — {title}"
        if fw in counts:
            line += f" ({counts[fw]:,} pages)"
        lines.append(line)
    if not index.ready:
        lines.append(f"\n(Page counts unavailable: {index.status})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: ranked symbol / topic search
# ---------------------------------------------------------------------------
@tool
def search_symbols(
    query: str,
    framework: str = "",
    kind: str = "",
    include_deprecated: bool = True,
    max_results: int = 20,
) -> str:
    """Ranked search over page titles, symbol names and summaries. Start here
    when looking for an API or article. Exact name matches rank first and
    deprecated APIs rank last. Matches whole words and word prefixes, and
    CamelCase is split, so 'navigation stack' finds NavigationStack.

    Args:
        query: Words to look for (e.g. 'NavigationStack', 'background task', 'URLSession upload').
        framework: Limit to one framework (e.g. 'swiftui'). Optional.
        kind: Limit to a kind such as 'Structure', 'Class', 'Protocol', 'Instance Method', 'Article'. Optional.
        include_deprecated: Include deprecated APIs (default True; they're ranked last).
        max_results: Maximum results (default 20, max 100).
    """
    if not index.ready:
        return "Error: " + index.not_ready_message()
    terms = re.findall(r"\w+", query.lower())
    if not terms:
        return "Error: Search query cannot be empty."
    max_results = max(1, min(max_results, 100))
    where, params = ["docs_fts MATCH ?"], [" ".join(f'"{t}"*' for t in terms)]
    if framework:
        fw, err = check_framework(framework)
        if err:
            return f"Error: {err}"
        where.append("d.framework = ?")
        params.append(fw)
    if kind:
        where.append("lower(d.kind) = lower(?)")
        params.append(kind)
    if not include_deprecated:
        where.append("d.deprecated = 0")
    rows = index.query(
        "SELECT d.*, bm25(docs_fts, 10.0, 6.0, 2.0, 1.0) AS score FROM docs_fts JOIN docs d ON d.id = docs_fts.rowid"
        f" WHERE {' AND '.join(where)} ORDER BY score LIMIT 400", params)
    if not rows:
        return f"No pages match '{query}'." + (" Try fewer words, or search_documents for full text." if len(terms) > 1 else "")
    q = query.strip().lower()
    q_base = q.split("(", 1)[0]

    def key(r):
        exact = r["title"].lower() == q or r["name"] == q_base or r["stem"] == q_base.replace(" ", "")
        if exact:  # among exact matches: same case first, then top-level pages
            return (0, r["deprecated"], r["title"] != query.strip(), r["path"].count("/"), r["score"])
        return (1, r["deprecated"], False, 0, r["score"])

    rows = sorted(rows, key=key)[:max_results]
    lines = [f"Results for '{query}' ({len(rows)} shown; paths work with read_document):"]
    lines += [f"{i}. {format_row(r)}" for i, r in enumerate(rows, 1)]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: lookup a symbol by name
# ---------------------------------------------------------------------------
@tool
def lookup_symbol(
    name: str,
    framework: str = "",
) -> str:
    """Find a symbol (type, method, property) by exact name across frameworks.
    'addSubview' finds addSubview(_:) and addSubview(_:positioned:relativeTo:);
    'frame' finds every property named frame. Also lists names starting with it.

    Args:
        name: Symbol name (e.g. 'NavigationStack', 'onChange', 'URLSession', 'init(frame:)').
        framework: Limit to a specific framework (optional).
    """
    base = name.strip().split("(", 1)[0].lower()
    if not base:
        return "Error: name cannot be empty."
    fw = ""
    if framework:
        fw, err = check_framework(framework)
        if err:
            return f"Error: {err}"
    if not index.ready:
        return _lookup_symbol_scan(name, fw)

    fw_sql, fw_params = (" AND framework = ?", [fw]) if fw else ("", [])
    exact = index.query(
        f"SELECT * FROM docs WHERE (name = ? OR stem = ?){fw_sql} ORDER BY deprecated, lower(title) != ?, framework, length(path), path LIMIT 200",
        [base, base.replace(" ", "")] + fw_params + [base])
    full = name.strip().lower()
    if "(" in full:  # a full signature was given: put that overload first
        exact = sorted(exact, key=lambda r: r["title"].lower() != full)
    prefix = index.query(
        f"SELECT * FROM docs WHERE name LIKE ? ESCAPE '\\' AND name != ?{fw_sql}"
        " ORDER BY deprecated, length(name), length(path) LIMIT 30",
        [base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%", base] + fw_params)
    if not exact and not prefix:
        return f"No symbols named '{name}'. Try search_symbols for a ranked search."
    lines = [f"Symbols matching '{name}':"]
    if exact:
        lines.append(f"\nExact matches ({len(exact)}):")
        lines += [f"  {format_row(r)}" for r in exact]
    if prefix:
        lines.append(f"\nNames starting with '{base}'{' (first 30)' if len(prefix) == 30 else ''}:")
        lines += [f"  {r['title']} — {r['kind'] or 'page'} [{r['path']}]{' (DEPRECATED)' if r['deprecated'] else ''}"
                  for r in prefix]
    return "\n".join(lines)


def _lookup_symbol_scan(name: str, fw: str) -> str:
    """Filename scan used until the index is ready."""
    root = MARKDOWN_DIR / fw if fw else MARKDOWN_DIR
    base = name.strip().split("(", 1)[0].lower()
    exact, partial = [], []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if not f.endswith(".md"):
                continue
            stem = f[:-3].split("(", 1)[0]
            rel = os.path.relpath(os.path.join(dirpath, f), MARKDOWN_DIR)[:-3]
            if stem == base:
                exact.append(rel)
            elif stem.startswith(base):
                partial.append(rel)
    if not exact and not partial:
        return f"No symbols named '{name}'."
    lines = [f"Symbols matching '{name}' (index not ready; filename match only):"]
    if exact:
        lines.append(f"\nExact matches ({len(exact)}):")
        lines += [f"  {p}" for p in sorted(exact, key=lambda p: (len(p), p))[:200]]
    if partial:
        lines.append(f"\nNames starting with '{base}' ({len(partial)} total, first 30):")
        lines += [f"  {p}" for p in sorted(partial, key=lambda p: (len(p), p))[:30]]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tools: full-text search
# ---------------------------------------------------------------------------
def _grep(query: str, root: Path, regex: bool, case_insensitive: bool,
          max_results: int, per_file: int, label: str) -> str:
    if not query or not query.strip():
        return "Error: Search query cannot be empty."
    cmd = ["grep", "-rn", "--include=*.md", "-m", str(per_file + 30)]
    cmd.append("-E" if regex else "-F")
    if case_insensitive:
        cmd.append("-i")
    cmd.extend(["--", query, str(root)])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return "Error: Search timed out. Try a more specific query or limit it to one framework."
    if result.returncode == 2:
        return f"Error: Search failed: {result.stderr.strip()[:300]}"
    if result.returncode == 1 or not result.stdout.strip():
        return f"No matches for '{query}' in {label}."

    by_file: dict[str, list[tuple[int, str]]] = {}
    for line in result.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[1].isdigit():
            by_file.setdefault(parts[0], []).append((int(parts[1]), parts[2]))

    output, files_with_hits = [], 0
    for path in sorted(by_file, key=lambda p: (p.count("/"), p)):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                _, fm_lines = split_frontmatter(fh.read(32768))
        except OSError:
            continue
        hits = [(n, t) for n, t in by_file[path] if n > fm_lines][:per_file]
        if not hits:
            continue
        files_with_hits += 1
        doc_path = os.path.relpath(path, MARKDOWN_DIR)[:-3]
        for n, text in hits:
            if len(output) >= max_results:
                break
            text = rewrite_links(text, doc_path) if "](" in text else text
            text = text.strip()
            output.append(f"{doc_path}:{n}: {text[:240] + '…' if len(text) > 240 else text}")
    if not output:
        return f"No matches for '{query}' in {label} (outside page metadata)."
    header = f"Matches for '{query}' in {label} ({len(output)} lines shown"
    if files_with_hits > len({o.split(':', 1)[0] for o in output}) or len(output) >= max_results:
        header += "; more exist — narrow the query"
    header += "). Use read_document with path and offset=line to see context:\n"
    return header + "\n".join(output)


@tool
def search_documents(
    framework: str,
    query: str,
    regex: bool = False,
    case_insensitive: bool = True,
    max_results: int = 50,
) -> str:
    """Full-text search of the page text within one framework. Returns
    path:line: text. Use search_symbols instead to find APIs by name.

    Args:
        framework: Framework name (e.g. 'swiftui', 'foundation').
        query: Text to find. Plain text by default (characters like [ ] ( ) . are literal).
        regex: Treat query as an extended regular expression (default False).
        case_insensitive: Ignore case (default True).
        max_results: Maximum matching lines (default 50, max 200).
    """
    fw, err = check_framework(framework)
    if err:
        return f"Error: {err}"
    return _grep(query, MARKDOWN_DIR / fw, regex, case_insensitive,
                 max(1, min(max_results, 200)), 5, fw)


@tool
def search_all_frameworks(
    query: str,
    regex: bool = False,
    case_insensitive: bool = True,
    max_results: int = 30,
) -> str:
    """Full-text search of the page text across ALL frameworks. Slower than
    search_documents; use search_symbols to find APIs by name.

    Args:
        query: Text to find. Plain text by default (characters like [ ] ( ) . are literal).
        regex: Treat query as an extended regular expression (default False).
        case_insensitive: Ignore case (default True).
        max_results: Maximum matching lines (default 30, max 100).
    """
    if not MARKDOWN_DIR.exists():
        return "Error: Markdown directory not found."
    return _grep(query, MARKDOWN_DIR, regex, case_insensitive,
                 max(1, min(max_results, 100)), 3, "all frameworks")


# ---------------------------------------------------------------------------
# Tool: read a document
# ---------------------------------------------------------------------------
def doc_header(doc_path: str, meta: dict) -> list[str]:
    title = meta.get("title") or doc_path
    kind = meta.get("roleHeading") or meta.get("role") or ""
    modules = ", ".join(m.get("name", "") for m in meta.get("modules") or [] if isinstance(m, dict))
    desc = ", ".join(x for x in (kind, modules) if x)
    lines = [f"Document: {doc_path} — {title}" + (f" ({desc})" if desc else "")]
    src = meta.get("url") or DOCS_SITE + doc_path
    lines.append(f"Source: {src}" + (f" (fetched {meta['fetched']})" if meta.get("fetched") else ""))
    avail = format_availability(meta)
    if avail:
        lines.append("Availability: " + "; ".join(avail))
    for msg in availability_messages(meta):
        lines.append(f"Platform note: {msg}")
    if is_deprecated(meta):
        lines.append("*** DEPRECATED — check the Deprecated note and platform messages for the replacement. ***")
    elif any(isinstance(p, dict) and ("deprecatedAt" in p or p.get("deprecated")) for p in meta.get("platforms") or []):
        lines.append("Note: deprecated on some platforms (see Availability).")
    return lines


@tool
def read_document(
    path: str,
    offset: int = 0,
    limit: int = 0,
    include_frontmatter: bool = False,
) -> str:
    """Read a documentation page. The reply starts with title, kind, source URL,
    availability and deprecation, then the page with line numbers. Links in the
    page are rewritten: archive pages appear as paths you can pass straight back
    to read_document; https:// links are pages that aren't in the archive.

    Args:
        path: The page: an archive path ('appkit/nsview' or 'appkit/nsview.md'), a doc:// identifier,
              or a developer.apple.com/documentation URL. Case doesn't matter.
        offset: Line to start from (1-based). Default: start of the page body.
        limit: Number of lines (default and max 500).
        include_frontmatter: Also show the raw YAML frontmatter (default False; the header summarizes it).
    """
    doc_path, f, err = resolve_doc(path)
    if err:
        return f"Error: {err}"
    try:
        text, meta, fm_lines = load_doc(f)
    except Exception as e:
        return f"Error reading file: {e}"
    all_lines = text.splitlines()
    total = len(all_lines)
    if total == 0:
        return f"Document '{doc_path}' is empty."
    first = 1 if include_frontmatter else fm_lines + 1
    offset = max(offset, first) if offset > 0 else first
    if offset > total:
        return f"Error: Offset {offset} exceeds document length ({total} lines)."
    limit = 500 if limit <= 0 else min(limit, 500)
    start, end = offset - 1, min(offset - 1 + limit, total)
    selected = rewrite_doc_lines(all_lines[start:end], doc_path, in_fence_at(all_lines, start))
    numbered = [f"{i:>5} | {line}" for i, line in enumerate(selected, start=offset)]
    header = doc_header(doc_path, meta)
    header.append(f"Lines {offset}-{end} of {total}" + (f" (use offset={end + 1} to continue)" if end < total else ""))
    return "\n".join(header) + "\n\n" + "\n".join(numbered)


# ---------------------------------------------------------------------------
# Tool: browse the documentation tree
# ---------------------------------------------------------------------------
@tool
def list_symbols(
    framework: str,
    path: str = "",
) -> str:
    """Browse a framework's documentation tree: the pages under a framework or
    type, grouped by kind (Structure, Instance Method, Article, ...). Call with
    just a framework for top-level pages, then pass a type to see its members.

    Args:
        framework: Framework name (e.g. 'swiftui').
        path: Page within the framework whose children to list (e.g. 'NavigationStack'
              or 'swiftui/navigationstack'). Default: the framework's top level.
    """
    fw, err = check_framework(framework)
    if err:
        return f"Error: {err}"
    sub = to_doc_path(path) if path else ""
    if sub.startswith(fw + "/"):
        sub = sub[len(fw) + 1:]
    elif sub == fw:
        sub = ""
    parent = f"{fw}/{sub}" if sub else fw
    target = MARKDOWN_DIR / parent
    if not target.is_dir():
        if doc_file(parent):
            return f"'{parent}' has no child pages. Use read_document('{parent}') to read it."
        return f"Error: '{parent}' not found."

    if index.ready:
        rows = index.query("SELECT * FROM docs WHERE parent = ? ORDER BY kind, title", (parent,))
        has_children = {r["parent"] for r in index.query(
            "SELECT DISTINCT parent FROM docs WHERE parent LIKE ? ESCAPE '\\'",
            (parent.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%",))}
        by_kind: dict[str, list] = {}
        for r in rows:
            by_kind.setdefault(r["kind"] or "Other", []).append(r)
        lines = [f"Children of {parent} ({len(rows)} pages):"]
        for kind in sorted(by_kind):
            lines.append(f"\n{kind}:")
            for r in by_kind[kind]:
                s = f"  {r['title']} [{r['path']}]"
                if r["path"] in has_children:
                    s += " (has members)"
                if r["deprecated"]:
                    s += " (DEPRECATED)"
                lines.append(s)
        return "\n".join(lines)

    dirs = sorted(d.name for d in target.iterdir() if d.is_dir() and not d.name.startswith("."))
    files = sorted(f.name[:-3] for f in target.iterdir() if f.is_file() and f.suffix == ".md")
    lines = [f"Children of {parent} ({len(files)} pages; index not ready, showing file names):"]
    lines += [f"  {parent}/{name}" + (" (has members)" if name in dirs else "") for name in files]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: symbol metadata
# ---------------------------------------------------------------------------
@tool
def get_symbol_info(path: str) -> str:
    """Summary of a page without its full text: title, kind, framework, summary,
    platform availability (introduced/deprecated versions), deprecation notice,
    declarations (Swift and Objective-C) and the source URL.

    Args:
        path: The page: archive path, doc:// identifier or developer.apple.com URL.
    """
    doc_path, f, err = resolve_doc(path)
    if err:
        return f"Error: {err}"
    try:
        text, meta, fm_lines = load_doc(f)
    except Exception as e:
        return f"Error reading file: {e}"
    body = "\n".join(text.splitlines()[fm_lines:])
    lines = doc_header(doc_path, meta)
    abstract = meta.get("abstract") or first_paragraph(body)
    if abstract:
        lines.append(f"Summary: {abstract}")
    secs = sections(body)
    if "Declaration" in secs:
        decl = "\n".join(secs["Declaration"]).strip()
        lines.append("\nDeclaration:\n" + decl)
    # The deprecation note sits before the first '## ' section
    m = re.search(r"^> \*\*Deprecated:\*\*\n((?:>.*\n?)+)", body, re.MULTILINE)
    if m:
        note = " ".join(l.lstrip(">").strip() for l in m.group(1).splitlines()).strip()
        lines.append("\nDeprecation note: " + rewrite_links(note, doc_path))
    if "Relationships" in secs:
        lines.append("\nRelationships:")
        lines += format_groups(link_groups(secs["Relationships"], doc_path))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: related symbols
# ---------------------------------------------------------------------------
@tool
def get_related_symbols(path: str) -> str:
    """The pages a page links to in its Topics (its members and child articles),
    Default Implementations, Mentioned In and See Also sections, with summaries
    and deprecation flags. Targets are paths for read_document (https:// targets
    aren't in the archive).

    Args:
        path: The page: archive path, doc:// identifier or developer.apple.com URL.
    """
    doc_path, f, err = resolve_doc(path)
    if err:
        return f"Error: {err}"
    try:
        text, meta, fm_lines = load_doc(f)
    except Exception as e:
        return f"Error reading file: {e}"
    secs = sections("\n".join(text.splitlines()[fm_lines:]))
    lines = []
    for name in ("Topics", "Default Implementations", "Mentioned In", "See Also"):
        if name in secs:
            groups = link_groups(secs[name], doc_path)
            if groups:
                lines.append(f"\n== {name} ==")
                lines += format_groups(groups)
    if not lines:
        return f"No related pages listed in {doc_path}."
    return f"Related pages for {meta.get('title', doc_path)} [{doc_path}]:" + "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool: relationships
# ---------------------------------------------------------------------------
@tool
def get_relationships(path: str) -> str:
    """A type's relationships: what it inherits from, the protocols it conforms
    to, and its subclasses / conforming types. Use it to answer 'does X conform
    to Sendable?' or 'what implements this protocol?'.

    Args:
        path: The page: archive path, doc:// identifier or developer.apple.com URL.
    """
    doc_path, f, err = resolve_doc(path)
    if err:
        return f"Error: {err}"
    try:
        text, meta, fm_lines = load_doc(f)
    except Exception as e:
        return f"Error reading file: {e}"
    secs = sections("\n".join(text.splitlines()[fm_lines:]))
    if "Relationships" not in secs:
        return f"No relationships listed for {meta.get('title', doc_path)} [{doc_path}]."
    return (f"Relationships for {meta.get('title', doc_path)} [{doc_path}]:"
            + "\n".join(format_groups(link_groups(secs["Relationships"], doc_path))))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if not MARKDOWN_DIR.exists():
        logger.warning("Markdown directory not found at %s", MARKDOWN_DIR)
        logger.warning("Tools will return errors until documentation is downloaded.")
    else:
        logger.info("Serving %d frameworks from %s", len(framework_names()), MARKDOWN_DIR)
    logger.info("Search index: %s (refresh every %ds)", INDEX_PATH, INDEX_REFRESH)
    index.start()
    logger.info("Listening on %s:%d", HOST, PORT)
    mcp.run(transport="streamable-http")

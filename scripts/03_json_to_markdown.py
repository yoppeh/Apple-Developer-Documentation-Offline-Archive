#!/usr/bin/env python3
"""
Apple Documentation JSON to Markdown Converter
Converts Apple's JSON documentation format to AI-readable Markdown
"""

import json
import yaml
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple
from tqdm import tqdm
import argparse
import os

DOCS_SITE = 'https://developer.apple.com'

# Inline node types; some pages also put these directly in block lists.
INLINE_TYPES = {
    'text', 'codeVoice', 'reference', 'emphasis', 'strong', 'image', 'link',
    'newTerm', 'inlineHead', 'superscript', 'subscript', 'strikethrough',
}

# Declaration language ids -> code fence languages
DECL_LANGUAGES = {'swift': 'swift', 'occ': 'objc', 'objc': 'objc', 'data': 'json'}


def normalize_doc_path(identifier: str) -> str:
    """Map a doc:// identifier or /documentation/ URL to the archive's lowercase doc path.

    doc://com.apple.Swift/documentation/Swift/String -> swift/string
    """
    path = identifier
    if path.startswith('doc://'):
        path = path.split('/documentation/', 1)[1] if '/documentation/' in path else ''
    elif path.startswith('/documentation/'):
        path = path[len('/documentation/'):]
    return path.split('#', 1)[0].strip('/').lower()


def md_dest(href: str) -> str:
    """A Markdown link destination; wrap in <> when parentheses are unbalanced or it has spaces."""
    depth = 0
    for ch in href:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
            if depth < 0:
                break
    if depth != 0 or ' ' in href or '<' in href:
        return '<' + href.replace('>', '%3E') + '>'
    return href


def indent_continuation(text: str, prefix: str) -> str:
    """Indent every line after the first (for list items with multiple blocks)."""
    lines = text.split('\n')
    return '\n'.join([lines[0]] + [(prefix + l) if l.strip() else '' for l in lines[1:]])


class AppleDocConverter:
    """Converts Apple's JSON documentation to Markdown"""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.raw_json_dir = self.base_dir / 'raw-json'
        self.markdown_dir = self.base_dir / 'markdown'
        self.docsync_dir = self.base_dir / '.docsync'

        # Create markdown directory
        self.markdown_dir.mkdir(parents=True, exist_ok=True)

        # Per-page state: doc path (for relative links), the page's references
        # (titles, URLs and abstracts of everything it links to), manifest entry
        self.current_doc_path = ''
        self.references: Dict[str, Dict] = {}
        self.current_meta: Dict = {}

        self.stats = {
            'converted': 0,
            'failed': 0,
            'skipped': 0,
        }

    def load_manifest(self) -> Dict:
        """Load the manifest"""
        manifest_file = self.docsync_dir / 'manifest.json'
        with open(manifest_file, 'r') as f:
            return json.load(f)

    # ------------------------------------------------------------------
    # References and links
    # ------------------------------------------------------------------
    def doc_link(self, identifier: str) -> str:
        """Relative Markdown link from the current page to the page for identifier"""
        target = normalize_doc_path(identifier) + '.md'
        if not self.current_doc_path:
            return target
        start = os.path.dirname(self.current_doc_path) or '.'
        return os.path.relpath(target, start)

    def ref_title(self, identifier: str) -> str:
        ref = self.references.get(identifier, {})
        title = ref.get('title')
        if title:
            return title
        name = identifier.split('#', 1)[0].rstrip('/').rsplit('/', 1)[-1]
        return name or identifier

    def ref_href(self, identifier: str) -> Optional[str]:
        """Where a reference points: a relative .md path for documentation pages,
        an absolute URL for anything else, or None if it can't be linked."""
        ref = self.references.get(identifier, {})
        url = ref.get('url', '') or ''
        if ref.get('type') == 'link' or url.startswith(('http://', 'https://')):
            return url or None
        # Prefer the reference's url: discovery stores pages under it, and for
        # operators it differs from the identifier (url .../'.==(_:_:) vs id .../.==(_:_:))
        target = url if url.startswith('/documentation/') else (
            identifier if identifier.startswith('doc://') and '/documentation/' in identifier else '')
        if target:
            if '#' in target and normalize_doc_path(target) == self.current_doc_path:
                return None  # a section of this page
            return self.doc_link(target)
        if url.startswith('/'):
            return DOCS_SITE + url
        return None

    def ref_is_deprecated(self, identifier: str) -> bool:
        return bool(self.references.get(identifier, {}).get('deprecated'))

    def ref_abstract(self, identifier: str) -> str:
        return self.convert_inline_content(self.references.get(identifier, {}).get('abstract', [])).strip()

    def link_markdown(self, identifier: str, title: Optional[str] = None) -> str:
        ref = self.references.get(identifier, {})
        title = title or self.ref_title(identifier)
        if ref.get('kind') == 'symbol' and '`' not in title:
            title = f'`{title}`'
        href = self.ref_href(identifier)
        return f'[{title}]({md_dest(href)})' if href else title

    def link_list(self, identifiers: List[str], with_abstracts: bool = False) -> str:
        lines = []
        for identifier in identifiers:
            line = '- ' + self.link_markdown(identifier)
            if self.ref_is_deprecated(identifier):
                line += ' *(deprecated)*'
            if with_abstracts:
                abstract = self.ref_abstract(identifier)
                if abstract:
                    line += f': {abstract}'
            lines.append(line + '\n')
        return ''.join(lines) + '\n' if lines else ''

    # ------------------------------------------------------------------
    # Inline content
    # ------------------------------------------------------------------
    def convert_inline_content(self, content: Any, plain: bool = False) -> str:
        """Convert inline content to Markdown (plain=True: text only, no markup or links)"""
        if isinstance(content, str):
            return content

        if not isinstance(content, list):
            return str(content) if content is not None else ''

        result = []
        for item in content:
            if isinstance(item, str):
                result.append(item)
                continue
            if not isinstance(item, dict):
                continue
            t = item.get('type')
            inner = lambda: self.convert_inline_content(item.get('inlineContent', []), plain)
            if t == 'text':
                result.append(item.get('text', ''))
            elif t == 'codeVoice':
                code = item.get('code', '')
                result.append(code if plain else f"`{code}`")
            elif t in ('emphasis', 'newTerm'):
                result.append(inner() if plain else f"*{inner()}*")
            elif t in ('strong', 'inlineHead'):
                result.append(inner() if plain else f"**{inner()}**")
            elif t == 'strikethrough':
                result.append(inner() if plain else f"~~{inner()}~~")
            elif t == 'reference':
                identifier = item.get('identifier', '')
                title = None
                if item.get('overridingTitleInlineContent'):
                    title = self.convert_inline_content(item['overridingTitleInlineContent'], plain=True)
                elif item.get('overridingTitle'):
                    title = item['overridingTitle']
                if plain:
                    result.append(title or self.ref_title(identifier))
                else:
                    result.append(self.link_markdown(identifier, title))
            elif t == 'link':
                title = item.get('title') or self.convert_inline_content(item.get('inlineContent', []), True)
                dest = item.get('destination', '')
                title = title or dest
                result.append(title if plain or not dest else f'[{title}]({md_dest(dest)})')
            elif t == 'image':
                ref = self.references.get(item.get('identifier', ''), {})
                alt = ref.get('alt') or ''
                variants = ref.get('variants') or []
                url = variants[0].get('url', '') if variants else ''
                if plain:
                    result.append(alt)
                elif url.startswith(('http://', 'https://')):
                    result.append(f'![{alt}]({md_dest(url)})')
                elif alt:
                    result.append(f'*[Image: {alt}]*')
            else:
                # superscript, subscript and anything unknown: keep the text
                result.append(inner())
        return ''.join(result)

    def plain_text(self, content: Any) -> str:
        return ' '.join(self.convert_inline_content(content, plain=True).split())

    # ------------------------------------------------------------------
    # Block content
    # ------------------------------------------------------------------
    def convert_paragraph(self, para: Dict) -> str:
        """Convert a paragraph to Markdown"""
        return self.convert_inline_content(para.get('inlineContent', [])) + '\n\n'

    def convert_heading(self, heading: Dict, level: int = 2) -> str:
        """Convert a heading to Markdown (Apple puts heading text in 'text')"""
        text = heading.get('text') or self.convert_inline_content(heading.get('inlineContent', []))
        return f"{'#' * level} {text}\n\n"

    def convert_code_listing(self, code: Dict) -> str:
        """Convert a code listing to Markdown"""
        syntax = code.get('syntax') or ''
        code_lines = code.get('code', [])
        code_text = '\n'.join(code_lines) if isinstance(code_lines, list) else str(code_lines)
        fence = '````' if '```' in code_text else '```'
        return f"{fence}{syntax}\n{code_text}\n{fence}\n\n"

    def convert_list(self, list_data: Dict, ordered: bool) -> str:
        result = []
        for i, item in enumerate(list_data.get('items', []), list_data.get('start', 1)):
            marker = f'{i}. ' if ordered else '- '
            text = self.convert_content_sections(item.get('content', [])).strip()
            result.append(marker + indent_continuation(text, ' ' * len(marker)) + '\n')
        return ''.join(result) + '\n'

    def convert_unordered_list(self, list_data: Dict) -> str:
        return self.convert_list(list_data, ordered=False)

    def convert_ordered_list(self, list_data: Dict) -> str:
        return self.convert_list(list_data, ordered=True)

    def convert_term_list(self, term_list: Dict) -> str:
        result = []
        for item in term_list.get('items', []):
            term = self.convert_inline_content(item.get('term', {}).get('inlineContent', []))
            definition = self.convert_content_sections(item.get('definition', {}).get('content', [])).strip()
            result.append(f"- **{term}**: " + indent_continuation(definition, '  ') + '\n')
        return ''.join(result) + '\n'

    def convert_aside(self, aside: Dict) -> str:
        """Convert an aside (Note, Important, Warning, Deprecated, ...) to a blockquote"""
        label = aside.get('name') or aside.get('style', 'note').title()
        text = self.convert_content_sections(aside.get('content', [])).strip()
        quoted = '\n'.join(('> ' + l) if l else '>' for l in text.split('\n'))
        return f"> **{label}:**\n{quoted}\n\n"

    def convert_table(self, table: Dict) -> str:
        """Convert a table to Markdown. Rows are lists of cells; cells are lists of blocks.
        'header' is 'row' (first row is the header), 'column', 'both' or 'none'."""
        rows = table.get('rows', [])
        if not rows:
            return ''

        def cell_text(cell: Any) -> str:
            blocks = cell if isinstance(cell, list) else cell.get('content', []) if isinstance(cell, dict) else []
            text = self.convert_content_sections(blocks).strip()
            return ' '.join(text.split('\n')).replace('|', '\\|') if text else ''

        rendered = [[cell_text(c) for c in row] for row in rows]
        width = max(len(r) for r in rendered)
        rendered = [r + [''] * (width - len(r)) for r in rendered]
        if table.get('header') in ('row', 'both'):
            header, body = rendered[0], rendered[1:]
        else:
            header, body = [''] * width, rendered
        result = [f"| {' | '.join(header)} |\n", f"| {' | '.join(['---'] * width)} |\n"]
        result += [f"| {' | '.join(r)} |\n" for r in body]
        return ''.join(result) + '\n'

    def convert_tab_navigator(self, nav: Dict) -> str:
        result = []
        for tab in nav.get('tabs', []):
            result.append(f"**{tab.get('title', '')}**\n\n")
            result.append(self.convert_content_sections(tab.get('content', [])))
        return ''.join(result)

    def convert_media(self, block: Dict, kind: str) -> str:
        ref = self.references.get(block.get('identifier', ''), {})
        alt = ref.get('alt') or ''
        variants = ref.get('variants') or []
        url = variants[0].get('url', '') if variants else ''
        if url.startswith(('http://', 'https://')):
            return f"[{kind}: {alt or url.rsplit('/', 1)[-1]}]({md_dest(url)})\n\n"
        if url and not alt:
            alt = url.rsplit('/', 1)[-1]
        return f"*[{kind}: {alt}]*\n\n" if alt else ''

    def convert_content_sections(self, sections: List[Dict], inline: bool = False) -> str:
        """Convert block content to Markdown"""
        result = []
        run: List[Dict] = []  # consecutive inline nodes appearing at block level

        def flush():
            if run:
                result.append(self.convert_inline_content(run) + '\n\n')
                run.clear()

        for section in sections or []:
            if not isinstance(section, dict):
                continue
            t = section.get('type')
            if t in INLINE_TYPES and t != 'image':
                run.append(section)
                continue
            flush()
            if t == 'paragraph':
                result.append(self.convert_paragraph(section))
            elif t == 'heading':
                result.append(self.convert_heading(section, section.get('level', 2)))
            elif t == 'codeListing':
                result.append(self.convert_code_listing(section))
            elif t == 'unorderedList':
                result.append(self.convert_unordered_list(section))
            elif t == 'orderedList':
                result.append(self.convert_ordered_list(section))
            elif t == 'termList':
                result.append(self.convert_term_list(section))
            elif t == 'aside':
                result.append(self.convert_aside(section))
            elif t == 'table':
                result.append(self.convert_table(section))
            elif t == 'tabNavigator':
                result.append(self.convert_tab_navigator(section))
            elif t == 'row':
                for column in section.get('columns', []):
                    result.append(self.convert_content_sections(column.get('content', [])))
            elif t == 'links':
                result.append(self.link_list(section.get('items', []), with_abstracts=True))
            elif t == 'image':
                result.append(self.convert_media(section, 'Image'))
            elif t == 'video':
                result.append(self.convert_media(section, 'Video'))
            elif 'inlineContent' in section:
                result.append(self.convert_inline_content(section['inlineContent']) + '\n\n')
            elif isinstance(section.get('content'), list):
                result.append(self.convert_content_sections(section['content']))
        flush()

        text = ''.join(result)
        if inline:
            return text.replace('\n\n', ' ').strip()
        return text

    # ------------------------------------------------------------------
    # Page sections
    # ------------------------------------------------------------------
    def convert_topics(self, topic_sections: List[Dict], heading: str = 'Topics',
                       with_abstracts: bool = True) -> str:
        """Convert grouped link sections (Topics, See Also, ...) to Markdown"""
        groups = [s for s in topic_sections or [] if s.get('identifiers')]
        if not groups:
            return ''
        result = [f'## {heading}\n\n']
        for section in groups:
            title = section.get('title')
            if title:
                result.append(f"### {title}\n\n")
            result.append(self.link_list(section['identifiers'], with_abstracts))
        return ''.join(result)

    def convert_declarations(self, section: Dict) -> str:
        out = "## Declaration\n\n"
        for decl in section.get('declarations', []):
            languages = decl.get('languages') or ['swift']
            fence = DECL_LANGUAGES.get(languages[0], languages[0])
            platforms = decl.get('platforms', [])
            if platforms:
                out += f"**Platforms:** {', '.join(platforms)}\n\n"
            code = ''.join(t.get('text', '') for t in decl.get('tokens', []))
            out += f"```{fence}\n{code}\n```\n\n"
        return out

    def generate_frontmatter(self, data: Dict) -> str:
        """Generate YAML frontmatter"""
        metadata = data.get('metadata', {})
        platforms = metadata.get('platforms', [])
        frontmatter = {
            'title': metadata.get('title', ''),
            'role': metadata.get('role', ''),
            'platforms': platforms,
            'modules': metadata.get('modules', []),
        }

        # Add optional fields
        if 'required' in metadata:
            frontmatter['required'] = metadata['required']
        if 'roleHeading' in metadata:
            frontmatter['roleHeading'] = metadata['roleHeading']
        if metadata.get('symbolKind'):
            frontmatter['symbolKind'] = metadata['symbolKind']
        abstract = self.plain_text(data.get('abstract', []))
        if abstract:
            frontmatter['abstract'] = abstract
        # Deprecated everywhere it's available (Apple marks platforms with deprecatedAt)
        available = [p for p in platforms if not p.get('unavailable')]
        deprecated = bool(data.get('deprecationSummary')) or (
            bool(available) and all(p.get('deprecated') or 'deprecatedAt' in p for p in available))
        if deprecated:
            frontmatter['deprecated'] = True
        if self.current_doc_path:
            frontmatter['path'] = self.current_doc_path
            frontmatter['url'] = f'{DOCS_SITE}/documentation/{self.current_doc_path}'
        if self.current_meta.get('downloaded_at'):
            frontmatter['fetched'] = self.current_meta['downloaded_at'][:10]

        yaml_str = yaml.dump(frontmatter, default_flow_style=False, allow_unicode=True,
                             sort_keys=False, width=1000)
        return f"---\n{yaml_str}---\n\n"

    def convert_json_to_markdown(self, json_path: Path) -> str:
        """Convert a JSON documentation file to Markdown"""
        with open(json_path, 'r') as f:
            data = json.load(f)
        self.references = data.get('references', {}) or {}

        metadata = data.get('metadata', {})
        markdown = self.generate_frontmatter(data)

        title = metadata.get('title', '')
        if title:
            markdown += f"# {title}\n\n"

        # The abstract is a list of inline nodes, not blocks
        abstract = data.get('abstract', [])
        if abstract:
            markdown += self.convert_inline_content(abstract).strip() + '\n\n'

        deprecation = data.get('deprecationSummary')
        if deprecation:
            text = self.convert_content_sections(deprecation).strip()
            markdown += '> **Deprecated:**\n' + '\n'.join(('> ' + l) if l else '>' for l in text.split('\n')) + '\n\n'

        download = data.get('sampleCodeDownload')
        if download:
            action = download.get('action', {})
            href = self.ref_href(action.get('identifier', ''))
            if href:
                markdown += f"[{action.get('overridingTitle') or 'Download'}]({md_dest(href)})\n\n"

        for section in data.get('primaryContentSections', []):
            kind = section.get('kind')
            if kind == 'content':
                markdown += self.convert_content_sections(section.get('content', []))
            elif kind == 'declarations':
                markdown += self.convert_declarations(section)
            elif kind == 'parameters':
                markdown += "## Parameters\n\n"
                for param in section.get('parameters', []):
                    text = self.convert_content_sections(param.get('content', [])).strip()
                    markdown += f"- **{param.get('name', '')}**: " + indent_continuation(text, '  ') + '\n'
                markdown += '\n'
            elif kind == 'returns':
                markdown += "## Return Value\n\n"
                markdown += self.convert_content_sections(section.get('content', []))
            elif kind == 'mentions':
                mentions = section.get('mentions', [])
                if mentions:
                    markdown += "## Mentioned In\n\n" + self.link_list(mentions)

        markdown += self.convert_topics(data.get('topicSections', []), 'Topics')
        markdown += self.convert_topics(data.get('defaultImplementationsSections', []),
                                        'Default Implementations')
        markdown += self.convert_topics(data.get('relationshipsSections', []),
                                        'Relationships', with_abstracts=False)
        markdown += self.convert_topics(data.get('seeAlsoSections', []), 'See Also')

        return markdown

    def convert_all(self, frameworks: List[str] = None, pages: List[str] = None):
        """Convert all JSON files (or just the given frameworks / doc paths) to Markdown"""
        manifest = self.load_manifest()

        if pages is not None:
            pages_to_convert = {p: manifest[p] for p in pages if p in manifest}
        elif frameworks:
            pages_to_convert = {
                k: v for k, v in manifest.items()
                if any(k == fw or k.startswith(f'{fw}/') for fw in frameworks)
            }
        else:
            pages_to_convert = manifest

        print(f"\nConverting {len(pages_to_convert)} pages to Markdown...")

        with tqdm(total=len(pages_to_convert), desc="Converting") as pbar:
            for doc_path, metadata in pages_to_convert.items():
                try:
                    json_path = self.base_dir / metadata['local_path']

                    if not json_path.exists():
                        print(f"\nWarning: JSON file not found: {json_path}")
                        self.stats['skipped'] += 1
                        pbar.update(1)
                        continue

                    # Convert to Markdown
                    self.current_doc_path = doc_path
                    self.current_meta = metadata
                    markdown = self.convert_json_to_markdown(json_path)

                    # Save Markdown file
                    md_path = self.markdown_dir / f'{doc_path}.md'
                    md_path.parent.mkdir(parents=True, exist_ok=True)

                    with open(md_path, 'w') as f:
                        f.write(markdown)

                    self.stats['converted'] += 1

                except Exception as e:
                    print(f"\nError converting {doc_path}: {e!r}")
                    self.stats['failed'] += 1

                pbar.update(1)

        # Print stats
        print("\n" + "="*60)
        print("Conversion Statistics:")
        print(f"  Successfully converted: {self.stats['converted']}")
        print(f"  Failed: {self.stats['failed']}")
        print(f"  Skipped: {self.stats['skipped']}")
        print("="*60)


def main():
    parser = argparse.ArgumentParser(description='Convert Apple documentation JSON to Markdown')
    parser.add_argument('--frameworks', nargs='+', help='Frameworks to convert (default: all)')
    parser.add_argument('--base-dir', default='.', help='Base directory (default: project directory)')
    parser.add_argument('--pages-file', help='Only convert the doc paths listed in this file (one per line)')

    args = parser.parse_args()

    base_dir = Path(__file__).parent.parent / args.base_dir if args.base_dir == '.' else Path(args.base_dir)

    # Check if manifest exists
    manifest_file = base_dir / '.docsync' / 'manifest.json'
    if not manifest_file.exists():
        print(f"Error: Manifest file not found: {manifest_file}")
        print("Please run 02_download_json.py first to download the documentation")
        return

    converter = AppleDocConverter(base_dir=base_dir)
    pages = None
    if args.pages_file:
        with open(args.pages_file) as f:
            pages = [line.strip() for line in f if line.strip()]
    converter.convert_all(frameworks=args.frameworks, pages=pages)

    print("\nConversion complete!")
    print(f"Markdown files saved to: {converter.markdown_dir}")
    print("\nNext steps:")
    print("  1. Browse the Markdown files in the 'markdown/' directory")
    print("  2. Optionally run 04_markdown_to_pdf.py to generate PDFs")
    print("  3. Use update_check.py to check for documentation updates")


if __name__ == '__main__':
    main()

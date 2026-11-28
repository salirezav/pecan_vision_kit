<<<<<<< HEAD
#!/usr/bin/env python3
"""Build the GitHub Pages site from the README and the kit guides."""

from __future__ import annotations

import html
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import markdown

ROOT = Path(__file__).resolve().parents[1]
SITE = Path(__file__).resolve().parent
OUT = SITE / "_site"
DOCS = ROOT / "docs"
REPO_BLOB = "https://github.com/salirezav/pecan_vision_kit/blob/main/"
REPO_TREE = "https://github.com/salirezav/pecan_vision_kit/tree/main/"
REPO_HOME = "https://github.com/salirezav/pecan_vision_kit"
CUSTOM_DOMAIN = "vision.alirezavaezi.com"


@dataclass(frozen=True)
class Page:
    output: str
    nav: str
    source: Path
    title: str


PAGES: tuple[Page, ...] = (
    Page("index.html", "Overview", ROOT / "README.md", "Pecan Vision Kit"),
    Page(
        "open-the-dashboard.html",
        "Connect",
        DOCS / "open-the-dashboard.md",
        "Open the dashboard · Pecan Vision Kit",
    ),
    Page("user-guide.html", "Quick start", DOCS / "USER-GUIDE.md", "Quick start · Pecan Vision Kit"),
    Page("admin.html", "Admin", DOCS / "ADMIN.md", "Admin · Pecan Vision Kit"),
)


def published_by_source() -> dict[Path, Page]:
    found: dict[Path, Page] = {}
    for page in PAGES:
        if not page.source.is_file():
            raise SystemExit(f"Missing {page.source}")
        found[page.source.resolve()] = page
    return found


def rewrite_links(md_text: str, source: Path, published: dict[Path, Page]) -> str:
    """Send guide links to this site. Other repo paths stay on GitHub."""

    def repl(match: re.Match[str]) -> str:
        bang, label, target = match.group(1), match.group(2), match.group(3)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            return match.group(0)
        path_part, _, frag = target.partition("#")
        suffix = f"#{frag}" if frag else ""
        if not path_part:
            return match.group(0)
        resolved = (source.parent / unquote(path_part)).resolve()
        page = published.get(resolved)
        if page is not None:
            return f"{bang}[{label}]({page.output}{suffix})"
        try:
            rel = resolved.relative_to(ROOT).as_posix()
        except ValueError:
            return match.group(0)
        if path_part.endswith("/") or resolved.is_dir():
            url = REPO_TREE + rel
        else:
            url = REPO_BLOB + rel
        return f"{bang}[{label}]({url}{suffix})"

    return re.sub(r"(!?)\[([^\]]+)\]\(([^)]+)\)", repl, md_text)


def render_body(md_text: str) -> str:
    return markdown.markdown(
        md_text,
        extensions=["fenced_code", "tables", "toc", "sane_lists", "smarty"],
        extension_configs={"toc": {"permalink": False}},
    )


def nav_html(current: Page) -> str:
    links: list[str] = []
    for page in PAGES:
        current_attr = ' aria-current="page"' if page.output == current.output else ""
        links.append(
            f'<a href="{html.escape(page.output)}"{current_attr}>{html.escape(page.nav)}</a>'
        )
    return "\n        ".join(links)


def wrap_html(body: str, page: Page) -> str:
    title = html.escape(page.title)
    source_name = html.escape(page.source.relative_to(ROOT).as_posix())
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="description" content="Setup, quick starts, and update instructions for the Pecan Vision Kit (Raspberry Pi 5).">
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
  <header class="top">
    <div class="wrap bar">
      <a class="brand" href="index.html">Pecan Vision Kit</a>
      <nav class="ext">
        <a href="{REPO_HOME}">GitHub</a>
        <a href="{REPO_HOME}/releases">Releases</a>
      </nav>
    </div>
    <div class="wrap docs-nav">
        {nav_html(page)}
    </div>
  </header>
  <main class="wrap prose">
{body}
  </main>
  <footer class="wrap foot">
    <p>Source: <a href="{REPO_HOME}">salirezav/pecan_vision_kit</a> · <code>{source_name}</code></p>
  </footer>
</body>
</html>
"""


def main() -> None:
    published = published_by_source()
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "assets").mkdir(parents=True)
    shutil.copy2(SITE / "assets" / "style.css", OUT / "assets" / "style.css")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    (OUT / "CNAME").write_text(CUSTOM_DOMAIN + "\n", encoding="utf-8")

    for page in PAGES:
        raw = page.source.read_text(encoding="utf-8")
        body = render_body(rewrite_links(raw, page.source, published))
        (OUT / page.output).write_text(wrap_html(body, page), encoding="utf-8")
        print(f"Wrote {OUT / page.output}")


if __name__ == "__main__":
    main()
=======
#!/usr/bin/env python3
"""Build the GitHub Pages site from the README and the kit guides."""

from __future__ import annotations

import html
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import markdown

ROOT = Path(__file__).resolve().parents[1]
SITE = Path(__file__).resolve().parent
OUT = SITE / "_site"
DOCS = ROOT / "docs"
REPO_BLOB = "https://github.com/salirezav/pecan_vision_kit/blob/main/"
REPO_TREE = "https://github.com/salirezav/pecan_vision_kit/tree/main/"
REPO_HOME = "https://github.com/salirezav/pecan_vision_kit"
CUSTOM_DOMAIN = "vision.alirezavaezi.com"


@dataclass(frozen=True)
class Page:
    output: str
    nav: str
    source: Path
    title: str


PAGES: tuple[Page, ...] = (
    Page("index.html", "Overview", ROOT / "README.md", "Pecan Vision Kit"),
    Page(
        "quick-start.html",
        "Quick start",
        DOCS / "open-the-dashboard.md",
        "Quick start · Pecan Vision Kit",
    ),
    Page("admin.html", "Admin", DOCS / "ADMIN.md", "Admin · Pecan Vision Kit"),
)


def published_by_source() -> dict[Path, Page]:
    found: dict[Path, Page] = {}
    for page in PAGES:
        if not page.source.is_file():
            raise SystemExit(f"Missing {page.source}")
        found[page.source.resolve()] = page
    return found


def rewrite_links(md_text: str, source: Path, published: dict[Path, Page]) -> str:
    """Send guide links to this site. Other repo paths stay on GitHub."""

    def repl(match: re.Match[str]) -> str:
        bang, label, target = match.group(1), match.group(2), match.group(3)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            return match.group(0)
        path_part, _, frag = target.partition("#")
        suffix = f"#{frag}" if frag else ""
        if not path_part:
            return match.group(0)
        resolved = (source.parent / unquote(path_part)).resolve()
        page = published.get(resolved)
        if page is not None:
            return f"{bang}[{label}]({page.output}{suffix})"
        try:
            rel = resolved.relative_to(ROOT).as_posix()
        except ValueError:
            return match.group(0)
        if path_part.endswith("/") or resolved.is_dir():
            url = REPO_TREE + rel
        else:
            url = REPO_BLOB + rel
        return f"{bang}[{label}]({url}{suffix})"

    return re.sub(r"(!?)\[([^\]]+)\]\(([^)]+)\)", repl, md_text)


def render_body(md_text: str) -> str:
    return markdown.markdown(
        md_text,
        extensions=["fenced_code", "tables", "toc", "sane_lists", "smarty"],
        extension_configs={"toc": {"permalink": False}},
    )


def nav_html(current: Page) -> str:
    links: list[str] = []
    for page in PAGES:
        current_attr = ' aria-current="page"' if page.output == current.output else ""
        links.append(
            f'<a href="{html.escape(page.output)}"{current_attr}>{html.escape(page.nav)}</a>'
        )
    return "\n        ".join(links)


def wrap_html(body: str, page: Page) -> str:
    title = html.escape(page.title)
    source_name = html.escape(page.source.relative_to(ROOT).as_posix())
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="description" content="Setup, quick starts, and update instructions for the Pecan Vision Kit (Raspberry Pi 5).">
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
  <header class="top">
    <div class="wrap bar">
      <a class="brand" href="index.html">Pecan Vision Kit</a>
      <nav class="ext">
        <a href="{REPO_HOME}">GitHub</a>
        <a href="{REPO_HOME}/releases">Releases</a>
      </nav>
    </div>
    <div class="wrap docs-nav">
        {nav_html(page)}
    </div>
  </header>
  <main class="wrap prose">
{body}
  </main>
  <footer class="wrap foot">
    <p>Source: <a href="{REPO_HOME}">salirezav/pecan_vision_kit</a> · <code>{source_name}</code></p>
  </footer>
</body>
</html>
"""


def main() -> None:
    published = published_by_source()
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "assets").mkdir(parents=True)
    shutil.copy2(SITE / "assets" / "style.css", OUT / "assets" / "style.css")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    (OUT / "CNAME").write_text(CUSTOM_DOMAIN + "\n", encoding="utf-8")

    for page in PAGES:
        raw = page.source.read_text(encoding="utf-8")
        body = render_body(rewrite_links(raw, page.source, published))
        (OUT / page.output).write_text(wrap_html(body, page), encoding="utf-8")
        print(f"Wrote {OUT / page.output}")


if __name__ == "__main__":
    main()
>>>>>>> 871aa392064a97663a2fda25a18dfd0b5b9317f7

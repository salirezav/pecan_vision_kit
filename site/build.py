#!/usr/bin/env python3
"""Build a static GitHub Pages site from the repo README."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
SITE = Path(__file__).resolve().parent
OUT = SITE / "_site"
README = ROOT / "README.md"
REPO_BLOB = "https://github.com/salirezav/pecan_vision_kit/blob/main/"
REPO_TREE = "https://github.com/salirezav/pecan_vision_kit/tree/main/"
REPO_HOME = "https://github.com/salirezav/pecan_vision_kit"


def rewrite_relative_links(md_text: str) -> str:
    """Point in-repo markdown/file links at GitHub so they work on Pages."""

    def repl(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        if target.startswith(("http://", "https://", "mailto:", "#")):
            return match.group(0)
        clean = target.split("#", 1)[0]
        frag = ""
        if "#" in target:
            frag = "#" + target.split("#", 1)[1]
        if clean.endswith("/") or "." not in Path(clean).name:
            url = REPO_TREE + clean.lstrip("./") + frag
        else:
            url = REPO_BLOB + clean.lstrip("./") + frag
        return f"[{label}]({url})"

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl, md_text)


def render_body(md_text: str) -> str:
    return markdown.markdown(
        md_text,
        extensions=["fenced_code", "tables", "toc", "sane_lists", "smarty"],
        extension_configs={"toc": {"permalink": False}},
    )


def wrap_html(body: str, title: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="description" content="Setup and update instructions for the Pecan Vision Kit (Raspberry Pi 5).">
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
  <header class="top">
    <div class="wrap">
      <a class="brand" href="./">Pecan Vision Kit</a>
      <nav>
        <a href="{REPO_HOME}">GitHub</a>
        <a href="{REPO_HOME}/releases">Releases</a>
      </nav>
    </div>
  </header>
  <main class="wrap prose">
{body}
  </main>
  <footer class="wrap foot">
    <p>Source: <a href="{REPO_HOME}">salirezav/pecan_vision_kit</a> · Built from <code>README.md</code></p>
  </footer>
</body>
</html>
"""


def main() -> None:
    if not README.is_file():
        raise SystemExit(f"Missing {README}")

    raw = README.read_text(encoding="utf-8")
    body = render_body(rewrite_relative_links(raw))

    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "assets").mkdir(parents=True)
    shutil.copy2(SITE / "assets" / "style.css", OUT / "assets" / "style.css")
    (OUT / "index.html").write_text(wrap_html(body, "Pecan Vision Kit"), encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Wrote {OUT / 'index.html'}")


if __name__ == "__main__":
    main()

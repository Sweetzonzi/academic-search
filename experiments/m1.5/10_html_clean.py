"""M1.5 spike / step 11: how much of the HTML route's bulk is removable?

The HTML route produced 2.4x the Markdown of the LaTeX route.  Before deciding
which is primary we need to know how much of that is LaTeXML plumbing that can
simply be thrown away: class/style attributes, span wrappers, the table of
contents, footnote back-links.

Measured by re-running the HTML route with a cleaning step in front of pandoc.
"""
import os
import re
import sys

from lxml import html as LH

BASE = os.path.dirname(os.path.abspath(__file__))

# elements that exist only for web navigation, never for reading
DROP_XPATH = [
    ".//nav",
    ".//*[contains(@class,'ltx_TOC')]",
    ".//*[contains(@class,'ltx_page_logo')]",
    ".//*[contains(@class,'ltx_tag_note')]",
    ".//*[contains(@class,'ltx_note_mark')]",
    ".//a[contains(@class,'ltx_note_backref')]",
    ".//*[contains(@class,'ltx_authors')]//*[contains(@class,'ltx_personname')]",
    ".//*[contains(@class,'ltx_ERROR')]",
]
# attributes worth keeping; everything else is LaTeXML/CSS plumbing
KEEP_ATTR = {"id", "href", "src", "data", "alt", "title", "type",
             "colspan", "rowspan", "datetime"}
# MathML carries semantics in attributes (display, accent, mathvariant, ...).
# Stripping those silently turns display maths inline and drops accents, so the
# whole <math> subtree is left untouched.
MATH_TAGS = {"math", "semantics", "annotation", "annotation-xml"}


def _in_math(el, art) -> bool:
    p = el
    while p is not None and p is not art:
        if isinstance(p.tag, str) and p.tag.lower() in MATH_TAGS:
            return True
        p = p.getparent()
    return False


def clean_article(art) -> dict:
    """Strip web-only elements and layout attributes in place. Returns counts."""
    st = {"dropped_elems": 0, "attrs_removed": 0, "spans_unwrapped": 0,
          "math_untouched": 0}
    for xp in DROP_XPATH:
        for el in art.xpath(xp):
            el.getparent().remove(el)
            st["dropped_elems"] += 1

    for el in art.iter():
        if not isinstance(el.tag, str):
            continue
        if _in_math(el, art):
            st["math_untouched"] += 1
            continue
        for a in list(el.attrib):
            if a not in KEEP_ATTR:
                del el.attrib[a]
                st["attrs_removed"] += 1

    # <span> carries no meaning once class/style are gone
    for sp in art.xpath(".//span"):
        if not _in_math(sp, art):
            sp.drop_tag()
            st["spans_unwrapped"] += 1

    # collapse whitespace-only text nodes leaf-by-leaf
    for el in art.iter():
        if isinstance(el.tag, str) and el.text and not el.text.strip():
            el.text = None
    return st


def main() -> None:
    src = sys.argv[1]
    out = sys.argv[2]
    doc = LH.parse(src).getroot()
    art = doc.xpath("//article")[0]
    before = len(LH.tostring(art))
    st = clean_article(art)
    after = len(LH.tostring(art))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "wb").write(LH.tostring(art, encoding="utf-8"))
    pct = 100 * (1 - after / before)
    print(f"{os.path.basename(os.path.dirname(src)):15s} "
          f"{before/1024:7.1f} KB -> {after/1024:7.1f} KB  (-{pct:4.1f}%)  "
          f"dropped={st['dropped_elems']:3d} attrs={st['attrs_removed']:5d} "
          f"spans={st['spans_unwrapped']:5d}")


if __name__ == "__main__":
    main()

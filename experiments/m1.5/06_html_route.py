"""M1.5 spike / step 7: the HTML fallback route, for papers with no usable LaTeX.

arXiv's LaTeXML HTML has two properties the LaTeX route lacks:
  * display math already sanitised -- `$$ ... $$` with no \\begin{equation} wrapper
  * figures referenced by relative URL, so they must be fetched explicitly
and one it lacks:
  * a clean document -- the page carries arXiv chrome (banner, modals, footer),
    so <article class="ltx_document"> has to be cut out first.

Reuses the same math/figure/anchor machinery as the LaTeX route so the results
are directly comparable.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

from lxml import html as LH

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import pypandoc  # noqa: E402

PANDOC = os.path.join(os.path.dirname(pypandoc.__file__), "files",
                      "pandoc.exe" if os.name == "nt" else "pandoc")
WRITER = "commonmark_x+tex_math_dollars+pipe_tables+footnotes+implicit_figures"
# SPIKE_HTML_CLEAN=1 -> strip LaTeXML plumbing before pandoc and write to a
# separate output root, so the two variants can be compared side by side.
CLEAN = os.environ.get("SPIKE_HTML_CLEAN") == "1"
OUTROOT = os.path.join(BASE, "textified-html-clean" if CLEAN else "textified-html")
UA = {"User-Agent": "academic-search-spike/0.1 (M1.5 textification test)"}


def _clean_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "spike_html_clean", os.path.join(BASE, "10_html_clean.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fetch(url: str, tries: int = 3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                        timeout=90) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    print(f"    ! fetch failed {url}: {last!r}")
    return None


def textify_html(paper_dir: str, tag: str) -> dict:
    src = os.path.join(paper_dir, "arxiv.html")
    if not os.path.exists(src):
        return {"tag": tag, "error": "no arxiv.html"}
    outdir = os.path.join(OUTROOT, tag)
    figdir = os.path.join(outdir, "figures")
    os.makedirs(figdir, exist_ok=True)

    doc = LH.parse(src).getroot()
    art = doc.xpath("//article")
    if not art:
        return {"tag": tag, "error": "no <article> container"}
    art = art[0]
    stats = {"page_chars": len(" ".join(doc.text_content().split())),
             "article_chars": len(" ".join(art.text_content().split())),
             "imgs_fetched": 0, "imgs_failed": 0, "figures": 0}

    # localise every image asset: rewrite to a local copy so pandoc can embed it
    # NB: arXiv's LaTeXML emits <object type="image/svg+xml" data="..."> for PDF
    # figures (converted to SVG) and <img> only for raster originals.
    for el in art.xpath(".//img | .//object[@data] | .//embed[@src]"):
        s = el.get("src") or el.get("data") or ""
        if not s:
            continue
        if s.startswith("http") and "arxiv.org" not in s:
            continue
        url = s if s.startswith("http") else "https://arxiv.org/html/" + s.lstrip("/")
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(s))
        dst = os.path.join(figdir, name)
        if not os.path.exists(dst):
            blob = fetch(url)
            if blob is None:
                stats["imgs_failed"] += 1
                continue
            open(dst, "wb").write(blob)
            stats["imgs_fetched"] += 1
        if el.tag == "object":
            el.set("data", dst)
        else:
            el.set("src", dst)
        stats["figures"] += 1

    # pandoc's HTML reader does not treat <object data=...> as an image, so turn
    # every localised <object> into an <img> (alt text = its figure caption)
    for ob in art.xpath(".//object[@data]"):
        img = LH.Element("img")
        img.set("src", ob.get("data"))
        fig = ob.getparent()
        while fig is not None and fig.tag != "figure":
            fig = fig.getparent()
        if fig is not None:
            caps = fig.xpath("./figcaption")
            if caps:
                img.set("alt", " ".join(caps[0].text_content().split()))
        ob.getparent().replace(ob, img)
        stats["objects_to_img"] = stats.get("objects_to_img", 0) + 1

    if CLEAN:
        stats["clean"] = _clean_module().clean_article(art)

    clean = os.path.join(outdir, "_article.html")
    open(clean, "wb").write(LH.tostring(art, encoding="utf-8",
                                        doctype="<!DOCTYPE html>"))
    t0 = time.time()
    proc = subprocess.run([PANDOC, clean, "-f", "html", "-t", WRITER, "--wrap=none"],
                          cwd=paper_dir, capture_output=True)
    dt = time.time() - t0
    if proc.returncode != 0:
        return {"tag": tag, "error": proc.stderr.decode()[:300]}
    raw = proc.stdout.decode("utf-8", "replace").replace("\r\n", "\n")

    txt, mstats = T_normalise_math(raw)
    txt, fstats, _ = T_normalise_figures(txt, figdir, figdir)
    txt, astats, refmap = T_inject_anchors(txt)
    txt, rstats = T_resolve_refs(txt, refmap)
    txt, pstats = T_polish(txt)
    txt = txt.replace(figdir.replace("\\", "/"), "figures").replace(figdir, "figures")
    head = (f"<!-- source: arxiv:{tag}  route=html->pandoc  pipeline=m1.5-spike -->\n"
            f"<!-- generated: {time.strftime('%F %T')} -->\n\n")
    open(os.path.join(outdir, "paper.md"), "w", encoding="utf-8",
         newline="\n").write(head + txt)
    meta = {"tag": tag, "route": "html", "sec_convert": round(dt, 2),
            "md_kb": round(len(txt.encode()) / 1024, 1), "html": stats,
            "xlinks": len(re.findall(r"\]\(#", txt)),
            "math": mstats, "figure": fstats, "anchors": astats,
            "refs": rstats, "polish": pstats}
    open(os.path.join(outdir, "meta.json"), "w", encoding="utf-8").write(
        json.dumps(meta, ensure_ascii=False, indent=2))
    return meta


def _load_latex_route():
    """Import the LaTeX-route functions without running its __main__."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "textify_latex", os.path.join(BASE, "05_textify.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # __name__ != "__main__", so no main()
    return mod


_mod = _load_latex_route()
T_normalise_math = _mod.normalise_math
T_normalise_figures = _mod.normalise_figures
T_inject_anchors = _mod.inject_anchors
T_resolve_refs = _mod.resolve_refs
T_polish = _mod.polish


def main() -> None:
    tags = sys.argv[1:] or sorted(d for d in os.listdir(BASE) if d.startswith("26")
                                  and os.path.isdir(os.path.join(BASE, d)))
    rows = []
    for tag in tags:
        pdir = os.path.join(BASE, tag)
        if not os.path.isdir(pdir) or not os.path.exists(os.path.join(pdir, "arxiv.html")):
            continue
        print(f"### {tag}")
        rows.append(textify_html(pdir, tag))
    print("\n" + "=" * 104)
    print(f"{'paper':15s} {'s':>5s} {'page_ch':>8s} {'art_ch':>7s} {'KB':>7s} "
          f"{'rawEnv':>6s} {'eq':>4s} {'fig':>4s} {'img':>4s} {'assets':>6s} "
          f"{'failed':>6s} {'xlinks':>6s} {'resolv':>6s} {'unres':>5s}")
    for m in rows:
        if "error" in m:
            print(f"{m['tag']:15s} ERROR {m['error'][:70]}")
            continue
        h, a, mm = m["html"], m["anchors"], m["math"]
        print(f"{m['tag']:15s} {m['sec_convert']:5.2f} {h['page_chars']:8d} "
              f"{h['article_chars']:7d} {m['md_kb']:7.1f} {mm['kept_raw_env']:6d} "
              f"{a['eq']:4d} {a['fig']:4d} {m['figure']['images']:4d} "
              f"{h['figures']:6d} {h['imgs_failed']:6d} "
              f"{m.get('xlinks', 0):6d} {m['refs']['resolved']:6d} "
              f"{m['refs']['unresolved']:5d}")


if __name__ == "__main__":
    main()

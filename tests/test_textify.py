"""textify：根 .tex 判定、公式/图件归一化、锚点与交叉引用（EX §7 第 1–2 条）。"""

from __future__ import annotations

import os
import pathlib

import pytest

from academic_search import textify

# 真 pandoc 与真样本由 `conftest.real_pandoc` 定位（见 tests/conftest.py）
SAMPLE = pathlib.Path("experiments/m1.5/2610.18630v1")


# --------------------------------------------------------------- 根 .tex

def test_find_root_tex_prefers_candidate(tmp_path):
    (tmp_path / "main.tex").write_text("x", encoding="utf-8")
    (tmp_path / "other.tex").write_text("y", encoding="utf-8")
    assert textify.find_root_tex(str(tmp_path)).endswith("main.tex")


def test_find_root_tex_by_documentclass(tmp_path):
    (tmp_path / "a.tex").write_text(r"\documentclass{article}", encoding="utf-8")
    (tmp_path / "b.tex").write_text(r"\input{a}", encoding="utf-8")
    assert textify.find_root_tex(str(tmp_path)).endswith("a.tex")


def test_find_root_tex_ltx_is_tex(tmp_path):
    (tmp_path / "paper.ltx").write_text(r"\documentclass{article}", encoding="utf-8")
    assert textify.find_root_tex(str(tmp_path)).endswith("paper.ltx")


def test_find_root_tex_ambiguous_raises(tmp_path):
    (tmp_path / "a.tex").write_text(r"\documentclass{article}", encoding="utf-8")
    (tmp_path / "b.tex").write_text(r"\documentclass{book}", encoding="utf-8")
    with pytest.raises(textify.TextifyError) as info:
        textify.find_root_tex(str(tmp_path))
    assert info.value.reason == "ambiguous_root_tex"


def test_find_root_tex_missing_raises(tmp_path):
    with pytest.raises(textify.TextifyError) as info:
        textify.find_root_tex(str(tmp_path))
    assert info.value.reason == "no_root_tex"


# --------------------------------------------------------------- 工具定位

def test_find_pandoc_env_wins(tmp_path, monkeypatch):
    fake = tmp_path / "pandoc.exe"
    fake.write_bytes(b"x")
    assert textify.find_pandoc(str(tmp_path), env={"ACADEMIC_SEARCH_PANDOC": str(fake)}) == str(fake)


def test_find_pandoc_vendor(tmp_path):
    vendor = tmp_path / "vendor" / "pandoc"
    vendor.mkdir(parents=True)
    name = "pandoc.exe" if os.name == "nt" else "pandoc"
    (vendor / name).write_bytes(b"x")
    found = textify.find_pandoc(str(tmp_path), env={})
    assert found and found.endswith(name)


def test_find_pdftocairo_env_fallback(tmp_path, monkeypatch):
    # 记录的解析顺序是 PATH → 环境变量 → TeX Live；因此测环境变量档要先让 PATH 落空
    monkeypatch.setattr(textify.shutil, "which", lambda name: None)
    fake = tmp_path / "pdftocairo.exe"
    fake.write_bytes(b"x")
    assert textify.find_pdftocairo(env={"ACADEMIC_SEARCH_PDFTOCAIRO": str(fake)}) == str(fake)


# --------------------------------------------------------------- 公式

def test_normalise_math_unwraps_equation_and_lifts_label():
    text = "$$\n\\begin{equation}\\label{eq:ctrl}\n x = 1\n\\end{equation}\n$$"
    out, stats = textify.normalise_math(text)
    assert "\\begin{equation}" not in out
    assert "<!-- id:eq:ctrl -->" in out
    assert "$$\nx = 1\n$$" in out
    assert stats["env_unwrapped"] == 1 and stats["labels_lifted"] == 1


def test_normalise_math_wraps_align():
    text = "$$\n\\begin{align}\n a &= b\n\\end{align}\n$$"
    out, stats = textify.normalise_math(text)
    assert "\\begin{aligned}" in out and "\\end{aligned}" in out
    assert stats["env_wrapped"] == 1


def test_normalise_math_unknown_env_wrapped_as_aligned():
    text = "$$\n\\begin{weird}\n a\n\\end{weird}\n$$"
    out, stats = textify.normalise_math(text)
    assert "\\begin{aligned}" in out
    assert stats["kept_raw_env"] == 1


# --------------------------------------------------------------- 图件

def _media(tmp_path):
    media = tmp_path / "_media"
    (media / "figures").mkdir(parents=True)
    (media / "figures" / "fig.png").write_bytes(b"png")
    (media / "figures" / "fig.pdf").write_bytes(b"%PDF-1.4")
    return media


def test_normalise_figures_rewrites(tmp_path, monkeypatch):
    # 这里要测的是"没有渲染器"这一档，所以把 PyMuPDF 也按缺席处理
    monkeypatch.setattr(textify, "pymupdf_version", lambda: None)
    media = _media(tmp_path)
    fig_dir = tmp_path / "figures"
    text = (
        '<figure id="fig:hero">\n'
        '<figure id="fig:hero_a"><embed src="figures/fig.pdf" />\n'
        "<figcaption>Path A</figcaption></figure>\n"
        '<img src="figures/fig.png" alt="Panel B" />\n'
        "<figcaption>Long caption here.</figcaption>\n"
        "</figure>"
    )
    out, stats, figmap = textify.normalise_figures(text, str(media), str(fig_dir), None)
    assert "<!-- id:fig:hero -->" in out
    assert "![Panel B](figures/fig.png)" in out
    assert "figures/fig.pdf" in out            # 无 pdftocairo：保留 PDF 链接
    assert "*Long caption here.*" in out
    assert stats["figures"] == 1 and stats["images"] == 2
    assert stats["render_failed"] == 0 and stats["render_skipped"] == 1
    assert stats["missing_src"] == 0


def test_normalise_figures_missing_src(tmp_path):
    fig_dir = tmp_path / "figures"
    text = '<figure><img src="nope.png" /></figure>'
    out, stats, _ = textify.normalise_figures(text, str(tmp_path), str(fig_dir), None)
    assert stats["missing_src"] == 1 and stats["figures"] == 1


def test_normalise_figures_render_pdf_with_stub(tmp_path):
    media = _media(tmp_path)
    fig_dir = tmp_path / "figures"

    def fake_pdf_to_png(pdf, stem, pdftocairo, dpi=200):
        path = stem + ".png"
        pathlib.Path(path).write_bytes(b"png")
        return path

    original = textify.pdf_to_png
    textify.pdf_to_png = fake_pdf_to_png
    try:
        text = '<figure><embed src="figures/fig.pdf" /></figure>'
        out, stats, _ = textify.normalise_figures(
            text, str(media), str(fig_dir), "pdftocairo"
        )
    finally:
        textify.pdf_to_png = original
    assert "figures/fig.png" in out
    assert stats["rendered_png"] == 1


# --------------------------------------------------------------- 锚点

def test_inject_anchors_numbers_and_maps():
    text = "\n".join(
        [
            "<!-- id:sec:intro -->",
            "## Introduction",
            "<!-- id:eq:a -->",
            "$$",
            "x = 1",
            "$$",
            "<!-- id:fig:a -->",
            "![cap](figures/f.png)",
            "<!-- id:tab:a -->",
            "| a | b |",
            "| - | - |",
            "::: {.algorithm #alg:main}",
            "**Algorithm 1** foo",
            ":::",
            "::: {.assumption #assum:x}",
            "**Assumption 1** bar",
            ":::",
        ]
    )
    out, stats, refmap = textify.inject_anchors(text)
    assert refmap["sec:intro"] == ("sec", "1")
    assert refmap["eq:a"] == ("eq", "1")
    assert refmap["fig:a"] == ("fig", "1")
    assert refmap["tab:a"] == ("tbl", "1")
    assert refmap["alg:main"] == ("alg", "1")
    assert refmap["assum:x"] == ("env", "Assumption 1")
    assert "<!-- eq: 1 -->" in out and "<!-- fig: 1 -->" in out
    assert "<!-- alg: 1 -->" in out
    assert stats["tbl"] == 1 and stats["alg"] == 1


# --------------------------------------------------------------- 引用

def test_resolve_refs_route_a_and_bibliography():
    text = r"[\[eq:a\]](#eq:a) and [[1]](#bib.bib1)"
    out, stats = textify.resolve_refs(text, {"eq:a": ("eq", "2")})
    assert "[eq. 2](#eq:a)" in out
    assert "[[1]](#bib.bib1)" in out
    assert stats["resolved"] == 1 and stats["bibliography"] == 1 and stats["unresolved"] == 0


def test_resolve_refs_uses_source_fallback():
    text = r"[\[tab:x\]](#tab:x)"
    out, stats = textify.resolve_refs(text, {}, {"tab:x": ("tbl", "3")})
    assert "[Table 3](#tab:x)" in out
    assert stats["resolved"] == 1


def test_resolve_refs_records_unresolved():
    text = r"[\[fig:missing\]](#fig:missing)"
    out, stats = textify.resolve_refs(text, {})
    assert out == text
    assert stats["unresolved"] == 1
    assert stats["unresolved_targets"] == ["fig:missing"]


# --------------------------------------------------------------- 源码兜底

def test_source_refmap_orders_by_appearance():
    tex = (
        "\\section{Intro}\\label{sec:intro}\n"
        "\\begin{equation}\\label{eq:one} x \\end{equation}\n"
        "\\begin{figure}\\label{fig:one} y \\end{figure}\n"
        "\\begin{table}\\label{tab:one} z \\end{table}\n"
        "\\begin{equation}\\label{eq:two} w \\end{equation}\n"
        "\\begin{lemma}\\label{lem:one} q \\end{lemma}\n"
    )
    refmap = textify.source_refmap(tex)
    assert refmap["sec:intro"] == ("sec", "1")
    assert refmap["eq:one"] == ("eq", "1")
    assert refmap["fig:one"] == ("fig", "1")
    assert refmap["tab:one"] == ("tbl", "1")
    assert refmap["eq:two"] == ("eq", "2")
    assert refmap["lem:one"] == ("env", "Lemma 1")


# --------------------------------------------------------------- 编排

def test_normalise_pandoc_output_end_to_end(tmp_path):
    media = _media(tmp_path)
    fig_dir = tmp_path / "figures"
    raw = (
        "$$\n\\begin{equation}\\label{eq:a}\n x = 1\n\\end{equation}\n$$\n\n"
        "See [\\[eq:a\\]](#eq:a) and [\\[tab:x\\]](#tab:x).\n\n"
        '<figure id="fig:a"><img src="figures/fig.png" /></figure>\n'
    )
    tex = "\\begin{table}\\label{tab:x} t \\end{table}"
    out, stats = textify.normalise_pandoc_output(
        raw, str(media), str(fig_dir), tex_text=tex, pdftocairo=None
    )
    assert "[eq. 1](#eq:a)" in out
    assert "[Table 1](#tab:x)" in out
    assert stats["refs"]["resolved"] == 2
    assert stats["refs"]["unresolved"] == 0


# --------------------------------------------------------------- 真实 pandoc

@pytest.mark.skipif(not SAMPLE.is_dir(), reason="样本目录不在版本库里")
def test_real_pandoc_end_to_end(tmp_path, real_pandoc):
    root_tex = textify.find_root_tex(str(SAMPLE))
    media = tmp_path / "_media"
    raw = textify.run_pandoc(root_tex, str(media), pandoc=real_pandoc)
    assert "$$" in raw
    out, stats = textify.normalise_pandoc_output(
        raw, str(media), str(tmp_path / "figures"), pdftocairo=textify.find_pdftocairo()
    )
    assert stats["anchors"]["eq"] > 0
    assert stats["refs"]["resolved"] > 0
    assert "<!-- sec: 1" in out

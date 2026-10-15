"""textify 取件、解包与装配编排（M1.5）：e-print / HTML 两条路线。

不联网：HTTP 走桩客户端，pandoc 走桩；真 pandoc 的端到端在 test_textify.py。
"""

from __future__ import annotations

import base64
import gzip
import io
import pathlib
import tarfile

import pytest

from academic_search import envelope, htmlclean, identity, service, store, textify
from academic_search.sources import SourceError
from academic_search.sources import arxiv as arxiv_source
from academic_search.sources.http import HttpBytesResponse


class StubClient:
    """按 URL 形状回响应；没登记的 URL 当 404。"""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.requests = 0
        self.cost_usd = 0.0
        self.cost_basis = ""
        self.calls = []

    def get_bytes(self, url, *, headers=None, source="", raise_on_error=True, max_bytes=None):
        self.requests += 1
        self.calls.append(url)
        if url not in self.routes:
            raise SourceError(source or "stub", "404 未找到", False)
        entry = self.routes[url]
        status, payload = entry if isinstance(entry, tuple) else (200, entry)
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        return HttpBytesResponse(url, status, payload, {})


def tarball(files: dict) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def make_work(root, arxiv_id: str = "2610.00001v1", source: str = "arxiv"):
    uid = identity.record_uid(source, arxiv_id)
    conn = store.connect(root / "data" / "sidecar.db")
    try:
        with store.write_tx(conn):
            store.insert_record(conn, {
                "record_uid": uid, "source": source, "source_id": arxiv_id,
                "title": "T", "published_at": "2026-10-01", "type": "preprint",
            })
            if source == "arxiv":
                store.upsert_ident(conn, "arxiv", arxiv_id, uid)
            store.create_work(conn, "wk1")
            store.add_member(conn, uid, "wk1", "primary")
    finally:
        conn.close()
    return uid


def stub_pandoc(text: str):
    def run(source_file, out_media_dir, *, pandoc, reader="latex",
            bibliography=None, source_dir=None, timeout=300):
        return text
    return run


# --------------------------------------------------------------- 解包

def test_unpack_eprint_tarball(tmp_path):
    blob = tarball({
        "main.tex": "\\documentclass{article}\n\\begin{document}x\\end{document}",
        "figures/a.pdf": b"%PDF-1.4",
    })
    info = textify.unpack_eprint(blob, str(tmp_path / "src"))
    assert info["kind"] == "tarball"
    assert "main.tex" in info["files"] and "figures/a.pdf" in info["files"]
    assert textify.unpack_eprint  # 保持可读性：解包结果落到磁盘
    assert (tmp_path / "src" / "main.tex").read_text(encoding="utf-8").startswith("\\documentclass")


def test_unpack_eprint_rejects_traversal(tmp_path):
    blob = tarball({"../evil.tex": "x"})
    with pytest.raises(textify.TextifyError) as info:
        textify.unpack_eprint(blob, str(tmp_path / "src"))
    assert info.value.reason == "unpack_failed"
    assert not (tmp_path / "evil.tex").exists()


def test_unpack_eprint_gzip_single_tex(tmp_path):
    raw = b"\\documentclass{article}\n\\begin{document}x\\end{document}"
    info = textify.unpack_eprint(gzip.compress(raw), str(tmp_path / "src"), name_hint="2610.00001v1")
    assert info["kind"] == "gzip"
    assert info["files"] == ["2610.00001v1.tex"]
    assert (tmp_path / "src" / "2610.00001v1.tex").exists()


def test_unpack_eprint_plain_tex_and_pdf(tmp_path):
    assert textify.unpack_eprint(b"% plain\n\\documentclass{article}", str(tmp_path / "a"))["kind"] == "tex"
    assert textify.unpack_eprint(b"%PDF-1.7 x", str(tmp_path / "b"))["kind"] == "pdf"


def test_unpack_eprint_bad_gzip(tmp_path):
    with pytest.raises(textify.TextifyError) as info:
        textify.unpack_eprint(b"\x1f\x8bnot gzip", str(tmp_path / "src"))
    assert info.value.reason == "unpack_failed"


# --------------------------------------------------------------- 取件

def test_arxiv_normalise_id_and_urls():
    assert arxiv_source.normalise_id("arXiv:2610.18630v1") == "2610.18630v1"
    assert arxiv_source.normalise_id("https://arxiv.org/abs/2610.18630") == "2610.18630"
    assert arxiv_source.eprint_url("2610.18630v1").endswith("/e-print/2610.18630v1")
    assert arxiv_source.html_url("2610.18630").endswith("/html/2610.18630")
    with pytest.raises(SourceError):
        arxiv_source.normalise_id("../etc/passwd")


def test_fetch_eprint_and_html():
    client = StubClient({
        arxiv_source.eprint_url("2610.1"): b"\x1f\x8bxx",
        arxiv_source.html_url("2610.1"): "<html></html>",
    })
    assert arxiv_source.fetch_eprint(client, "2610.1") == b"\x1f\x8bxx"
    assert arxiv_source.fetch_html(client, "2610.1") == "<html></html>"


def test_fetch_html_404_raises():
    with pytest.raises(SourceError):
        arxiv_source.fetch_html(StubClient(), "2610.1")


# --------------------------------------------------------------- 渲染器标识

def test_renderer_id_tracks_the_tool(monkeypatch):
    monkeypatch.setattr(textify, "_pdftocairo_version", lambda tool: "24.08.0")
    assert textify.renderer_id("C:/x/pdftocairo.exe") == "pdftocairo@24.08.0"
    monkeypatch.setattr(textify, "pymupdf_version", lambda: None)
    assert textify.renderer_id(None) == "none"
    monkeypatch.setattr(textify, "pymupdf_version", lambda: "1.28.2")
    assert textify.renderer_id(None) == "pymupdf@1.28.2"


def test_normalise_figures_pymupdf_fallback(tmp_path, monkeypatch):
    media = tmp_path / "_media"
    (media / "figures").mkdir(parents=True)
    (media / "figures" / "fig.pdf").write_bytes(b"%PDF-1.4")

    def fake(pdf, stem, dpi=200):
        pathlib.Path(stem + ".png").write_bytes(b"png")
        return stem + ".png"

    monkeypatch.setattr(textify, "pdf_to_png_mupdf", fake)
    out, stats, _ = textify.normalise_figures(
        '<figure><embed src="figures/fig.pdf" /></figure>',
        str(media), str(tmp_path / "figures"), None,
    )
    assert "figures/fig.png" in out
    assert stats["rendered_png"] == 1 and stats["render_failed"] == 0


# --------------------------------------------------------------- HTML 清洗

def test_clean_latexml_drops_chrome_keeps_math():
    pytest.importorskip("lxml")
    raw = (
        "<html><body><article><nav>menu</nav>"
        '<p>Hello <span class="ltx_text">world</span></p>'
        '<math display="block"><mi>x</mi></math>'
        "</article></body></html>"
    )
    out, stats = htmlclean.clean_latexml(raw)
    assert "<nav" not in out and "ltx_text" not in out and "<span" not in out
    assert "display=" in out and "<math" in out and stats["math_kept"] >= 1
    assert stats["dropped"] == 1


def test_collect_media_inline_and_remote():
    pytest.importorskip("lxml")
    inline = "data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode("ascii")
    # 相对 src 按 arXiv 的实际布局写：页面在 /html/<id>，图件在 /html/<id>/<id>/... 下
    raw = ('<article><img src="2610.1/figs/a.png"/><object data="' + inline
           + '"></object></article>')
    entries = htmlclean.collect_media(raw, base_url="https://arxiv.org/html/2610.1")
    assert sorted(e["kind"] for e in entries) == ["inline", "remote"]
    remote = [e for e in entries if e["kind"] == "remote"][0]
    assert remote["url"] == "https://arxiv.org/html/2610.1/figs/a.png"
    out = htmlclean.rewrite_media(
        raw, {"2610.1/figs/a.png": "01-a.png", inline: "02-inline.svg"}
    )
    assert 'src="01-a.png"' in out and 'data="02-inline.svg"' in out


# --------------------------------------------------------------- 编排

def test_run_textify_latex_route(tmp_path, monkeypatch):
    make_work(tmp_path)
    blob = tarball({"main.tex": "\\documentclass{article}\n\\begin{document}x\\end{document}"})
    client = StubClient({arxiv_source.eprint_url("2610.00001v1"): blob})
    monkeypatch.setattr(service.textify, "run_pandoc", stub_pandoc(
        "$$\n\\begin{equation}\\label{eq:a}\n x = 1\n\\end{equation}\n$$\n\nSee [\\[eq:a\\]](#eq:a).\n"
    ))
    outcome = service.run_textify(
        tmp_path, "wk1", source="latex", work_dir=str(tmp_path / "out"),
        client=client, pandoc="pandoc-stub", pdftocairo="pdftocairo-stub",
    )
    assert outcome.source == "arxiv_latex"
    assert "[eq. 1](#eq:a)" in outcome.markdown
    assert outcome.converter_version.startswith("unknown|")
    assert outcome.requests == 1 and client.calls[0].endswith("/e-print/2610.00001v1")
    assert (tmp_path / "out" / "src" / "main.tex").exists()
    assert outcome.attempts == []


def test_run_textify_auto_falls_back_to_html(tmp_path, monkeypatch):
    pytest.importorskip("lxml")
    make_work(tmp_path)
    html = "<html><body><article><h1>T</h1><p>Body</p></article></body></html>"
    client = StubClient({arxiv_source.html_url("2610.00001v1"): html})
    monkeypatch.setattr(service.textify, "run_pandoc", stub_pandoc("# T\n"))
    outcome = service.run_textify(
        tmp_path, "wk1", source="auto", work_dir=str(tmp_path / "out"),
        client=client, pandoc="pandoc-stub", pdftocairo="pdftocairo-stub",
    )
    assert outcome.source == "arxiv_html"
    assert "# T" in outcome.markdown          # 锚点由归一化层注入，故不比对整段
    assert [a["source"] for a in outcome.attempts] == ["latex"]
    assert outcome.requests == 2             # 一次失败的 e-print + 一次成功的 HTML
    assert outcome.attempts[0]["reason"] == "404 未找到"
    assert outcome.stats["html"]["media_total"] == 0


def test_run_textify_no_arxiv_record(tmp_path):
    make_work(tmp_path, arxiv_id="W123", source="openalex")
    with pytest.raises(envelope.EnvelopeError) as info:
        service.run_textify(tmp_path, "wk1", work_dir=str(tmp_path / "out"), pandoc="x")
    assert info.value.code == envelope.SOURCE_ERROR


# 真 pandoc 与真样本由 `conftest.real_pandoc` 定位（见 tests/conftest.py）
SAMPLE = pathlib.Path("experiments/m1.5/2610.18630v1")


@pytest.mark.skipif(not SAMPLE.is_dir(), reason="样本目录不在版本库里")
def test_real_pandoc_service_latex_route(tmp_path, real_pandoc):
    """真 pandoc + 真样本走一遍 service：取件 → 解包 → 装配 → 归一化。"""
    make_work(tmp_path, arxiv_id="2610.18630v1")
    blob = tarball({
        name: (SAMPLE / name).read_bytes() for name in textify.list_files(str(SAMPLE))
    })
    client = StubClient({arxiv_source.eprint_url("2610.18630v1"): blob})
    outcome = service.run_textify(
        tmp_path, "wk1", source="latex", work_dir=str(tmp_path / "out"),
        client=client, pandoc=real_pandoc,
    )
    assert outcome.source == "arxiv_latex"
    assert outcome.attempts == []
    assert len(outcome.markdown) > 20000
    assert "<!-- sec: 1" in outcome.markdown
    assert outcome.stats["refs"]["resolved"] > 100
    assert outcome.stats["figure"]["figures"] > 0
    assert outcome.figures


@pytest.mark.skipif(not (SAMPLE / "arxiv.html").exists(), reason="样本 HTML 不在版本库里")
def test_real_latexml_cleaning_keeps_math():
    """真样本 731 KB 的 LaTeXML：清洗要缩体积，但不能碰 <math> 子树（EX §3.4 的坑）。"""
    pytest.importorskip("lxml")
    raw = (SAMPLE / "arxiv.html").read_text(encoding="utf-8", errors="replace")
    cleaned, stats = htmlclean.clean_latexml(raw)
    assert len(cleaned) < len(raw)
    assert stats["math_kept"] > 1000
    entries = htmlclean.collect_media(cleaned, base_url=arxiv_source.html_url("2610.18630v1"))
    assert entries and all(e["kind"] == "remote" for e in entries)


def test_run_textify_unknown_work_and_source(tmp_path):
    with pytest.raises(envelope.EnvelopeError) as info:
        service.run_textify(tmp_path, "nope", work_dir=str(tmp_path / "out"), pandoc="x")
    assert info.value.code == envelope.NOT_FOUND
    make_work(tmp_path)
    with pytest.raises(envelope.EnvelopeError) as info:
        service.run_textify(tmp_path, "wk1", source="pdf", work_dir=str(tmp_path / "out"), pandoc="x")
    assert info.value.code == envelope.INVALID_ARGS

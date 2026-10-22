"""read 的缓存、离线转换、清单隔离、刷新与真实 pandoc 验收。默认不联网。"""

import io
import json
import tarfile
from pathlib import Path

import pytest

from academic_search import cli, config, envelope, identity, paper, paths, service, store, textify
from academic_search.sources import arxiv
from academic_search.sources.http import HttpBytesResponse, HttpResponse

ARXIV = "2610.00001"
LATEX = arxiv.eprint_url(ARXIV)
HTML_URL = arxiv.html_url(ARXIV)
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30"><rect width="20" height="20"/></svg>'
TEX = r"""\documentclass{article}
\usepackage{graphicx}
\begin{document}
\section{Methods}\label{sec:methods}
The method uses $E=mc^2$.
\begin{figure}\includegraphics{plot.svg}\caption{A useful plot}\label{fig:plot}\end{figure}
See Figure~\ref{fig:plot}.
\end{document}
"""
HTML = '<html><body><article><h1>Methods</h1><p>The method uses energy.</p><figure id="fig1"><img src="plot.svg" alt="A useful plot"/></figure></article></body></html>'


def source_blob(svg=SVG, tex=TEX, figure_name="plot.svg"):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as archive:
        for name, data in {"main.tex": tex.encode(), figure_name: svg}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class Client:
    def __init__(self, routes=None):
        self.routes = routes or {}
        self.requests, self.cost_usd, self.cost_basis = 0, 0.0, "arXiv 免费"
        self.calls = []

    def get_bytes(self, url, **kwargs):
        self.calls.append((url, kwargs.get("headers", {})))
        self.requests += 1
        data = self.routes.get(url, (404, b""))
        return HttpBytesResponse(url, data[0], data[1], {"ETag": '"test"'})

    def get(self, url, **kwargs):
        result = self.get_bytes(url, **kwargs)
        return HttpResponse(url, result.status, result.content.decode(), result.headers)


@pytest.fixture
def work(tmp_path):
    uid = identity.record_uid("arxiv", ARXIV)
    conn = store.connect(config.db_path(tmp_path))
    with store.write_tx(conn):
        store.insert_record(conn, {"record_uid": uid, "source": "arxiv", "source_id": ARXIV,
                                   "title": "Test paper", "type": "preprint",
                                   "published_at": "2026-10-01", "source_version": "v1"})
        store.upsert_ident(conn, "arxiv", ARXIV, uid)
        store.create_work(conn, uid)
        store.add_member(conn, uid, uid, "primary")
    conn.close()
    return tmp_path, uid


def raw(work, kind="source_tar_gz", blob=None, version="v1"):
    root, uid = work
    if blob is None:
        blob = source_blob()
    conn = store.connect(config.db_path(root))
    with store.write_tx(conn):
        sha = paths.store_bytes(root, blob)
        store.add_artifact(conn, uid, kind, sha, None, None, source_version=version)
    conn.close()
    return sha


def set_remote(work, version):
    root, uid = work
    conn = store.connect(config.db_path(root))
    with store.write_tx(conn):
        conn.execute("UPDATE record SET source_version = ? WHERE record_uid = ?", (version, uid))
    conn.close()


@pytest.fixture
def converter(monkeypatch):
    calls = []
    monkeypatch.setattr(textify, "find_pandoc", lambda root: "pandoc-test")
    monkeypatch.setattr(textify, "find_pdftocairo", lambda root=None: None)
    monkeypatch.setattr(textify, "renderer_id", lambda tool: "none")
    monkeypatch.setattr(textify, "converter_version", lambda *args: "pandoc-test-v1")

    def convert(source_file, out_media_dir, **kwargs):
        calls.append(kwargs["reader"])
        media = Path(out_media_dir)
        media.mkdir(parents=True, exist_ok=True)
        source = Path(source_file).parent
        candidates = list(source.glob("*.svg"))
        image = media / "plot.svg"
        if candidates:
            image.write_bytes(candidates[0].read_bytes())
        return ('# Methods\n\nThe method uses $E=mc^2$.\n\n'
                '<figure id="fig:plot"><img src="' + str(image).replace("\\", "/")
                + '" alt="A useful plot"/></figure>\n')

    monkeypatch.setattr(textify, "run_pandoc", convert)
    return calls


def read(work, **kwargs):
    return service.run_read(work[0], work[1], **kwargs)


def test_first_read_then_cache_and_exact_locator(work, converter):
    client = Client({LATEX + "v1": (200, source_blob())})
    first = read(work, client=client)
    second = read(work, client=client)
    assert first["network"] is True and second["network"] is False
    assert client.requests == 1 and converter == ["latex"]
    assert first["doc"] == second["doc"]
    assert first["figures"] and first["anchors"]
    text = Path(first["doc"]["path"]).read_text(encoding="utf-8")
    for anchor in first["anchors"]:
        assert text.splitlines()[anchor["line"] - 1] == '<!-- sec: ' + anchor["label"] + ' -->'
    figure = first["figures"][0]
    assert paths.sha256_bytes(Path(figure["path"]).read_bytes()) == figure["sha256"]
    assert figure["ref"] in text and "data/tmp" not in text.replace("\\", "/")
    conn = store.connect(config.db_path(work[0]))
    runs = conn.execute("SELECT * FROM run ORDER BY run_id").fetchall()
    bundle = paper.bundles(conn, *work)[0]
    conn.close()
    assert [row["requests"] for row in runs] == [1, 0]
    assert str(work[0]) not in json.dumps(bundle, ensure_ascii=False)
    assert first["doc"]["source_version"] == "v1"


def test_plain_latex_without_comment_header(work, converter):
    raw(work, blob=TEX.encode())
    data = read(work, refresh="false")
    assert data["figures"] == [] and converter == ["latex"]
    assert data["anchors"]


def test_missing_pandoc_without_cached_body_is_source_error(work, monkeypatch):
    monkeypatch.setattr(textify, "find_pandoc", lambda root: None)
    with pytest.raises(envelope.EnvelopeError) as error:
        read(work, refresh="false")
    assert error.value.code == envelope.SOURCE_ERROR


def test_unknown_work_does_not_download(work, converter):
    client = Client()
    with pytest.raises(envelope.EnvelopeError) as error:
        service.run_read(work[0], "unknown", client=client)
    assert error.value.code == envelope.NOT_FOUND and not client.calls


def test_non_arxiv_work_does_not_download(work, converter):
    root, _ = work
    uid = identity.record_uid("openalex", "W123")
    conn = store.connect(config.db_path(root))
    with store.write_tx(conn):
        store.insert_record(conn, {"record_uid": uid, "source": "openalex", "source_id": "W123",
                                   "title": "No arXiv", "type": "article"})
        store.create_work(conn, uid)
        store.add_member(conn, uid, uid, "primary")
    conn.close()
    client = Client()
    with pytest.raises(envelope.EnvelopeError) as error:
        service.run_read(root, uid, client=client)
    assert error.value.code == envelope.SOURCE_ERROR and not client.calls


def test_pandoc_warnings_are_visible_on_cached_reads(work, converter, monkeypatch):
    raw(work)
    convert = textify.run_pandoc
    def warned(*args, **kwargs):
        kwargs["diagnostics"].append("unknown math macro")
        return convert(*args, **kwargs)
    monkeypatch.setattr(textify, "run_pandoc", warned)
    first = read(work, refresh="false")
    second = read(work, refresh="false")
    assert any("unknown math macro" in item for item in first["warnings"])
    assert first["warnings"] == second["warnings"]


def test_empty_document_is_failure_and_never_cached(work, converter, monkeypatch):
    raw(work)
    monkeypatch.setattr(textify, "run_pandoc", lambda *args, **kwargs: "")
    with pytest.raises(envelope.EnvelopeError) as error:
        read(work, refresh="false")
    assert error.value.code == envelope.SOURCE_ERROR
    conn = store.connect(config.db_path(work[0]))
    assert store.artifact_of(conn, work[1], "paper_manifest") is None
    conn.close()


def test_versioned_arxiv_ident_does_not_duplicate_version_suffix(work, converter):
    root, uid = work
    conn = store.connect(config.db_path(root))
    with store.write_tx(conn):
        conn.execute("UPDATE record_ident SET value = ? WHERE record_uid = ? AND ns = 'arxiv'",
                     (ARXIV + "v1", uid))
    conn.close()
    raw(work)
    data = read(work, refresh="false")
    assert data["front_matter"]["arxiv_id"] == ARXIV + "v1"


def test_projection_rebuild_uses_only_bundle_without_tools_or_network(work, converter, monkeypatch):
    raw(work)
    first = read(work, refresh="false")
    # 删除测试生成的投影，唯一资产 bytes 保留。
    paths.remove_tree(paths.works_dir(work[0]))
    monkeypatch.setattr(textify, "find_pandoc", lambda root: None)
    client = Client()
    second = read(work, refresh="false", client=client)
    assert client.calls == [] and converter == ["latex"]
    assert second["doc"]["sha256"] == first["doc"]["sha256"]
    projection = Path(second["doc"]["work_path"])
    assert projection.is_file()
    for figure in second["figures"]:
        assert (projection.parent / figure["ref"]).read_bytes() == Path(figure["path"]).read_bytes()


def test_cached_body_survives_unlaunchable_pandoc(work, converter, monkeypatch):
    raw(work)
    first = read(work, refresh="false")
    monkeypatch.setattr(textify, "converter_version", lambda *args: "unknown|writer|none")
    second = read(work, refresh="false")
    assert second["doc"]["sha256"] == first["doc"]["sha256"] and converter == ["latex"]
    assert any("版本无法确认" in item for item in second["warnings"])


def test_reader_revision_reconverts_local_source_offline(work, converter, monkeypatch):
    raw(work)
    first = read(work, refresh="false")
    monkeypatch.setattr(paper, "READER_VERSION", "read@next")
    client = Client()
    second = read(work, refresh="false", client=client)
    assert converter == ["latex", "latex"] and client.calls == []
    assert second["doc"]["converter_version"].endswith("|read@next")
    assert second["doc"]["sha256"] != first["doc"]["sha256"]


def test_metadata_upgrade_only_marks_stale(work, converter):
    raw(work)
    read(work, refresh="false")
    set_remote(work, "v2")
    client = Client()
    data = read(work, client=client)
    assert data["doc"]["stale"] is True and not client.calls
    assert data["doc"]["source_version"] == "v1" and data["doc"]["remote_version"] == "v2"


@pytest.mark.parametrize("local,remote", [(None, "v1"), ("v1", None), (None, None)])
def test_unknown_version_retains_known_side(work, converter, local, remote):
    raw(work, version=local)
    set_remote(work, remote)
    doc = read(work, refresh="false")["doc"]
    assert doc["source_version"] == local and doc["remote_version"] == remote
    assert doc["stale"] is False


def test_new_local_source_reconverts_and_isolates_figures(work, converter):
    raw(work)
    first = read(work, refresh="false")
    new_svg = SVG.replace(b'width="20"', b'width="12"')
    raw(work, blob=source_blob(new_svg), version="v2")
    second = read(work, refresh="false")
    assert converter == ["latex", "latex"]
    assert second["figures"][0]["sha256"] != first["figures"][0]["sha256"]
    assert Path(first["figures"][0]["path"]).read_bytes() == SVG
    projected = Path(second["doc"]["work_path"]).parent / second["figures"][0]["ref"]
    assert projected.read_bytes() == new_svg


def test_new_source_version_with_identical_bytes_updates_provenance(work, converter):
    blob = source_blob()
    raw(work, blob=blob)
    read(work, refresh="false")
    raw(work, blob=blob, version="v2")
    set_remote(work, "v2")
    data = read(work, refresh="false")
    assert data["doc"]["source_version"] == "v2" and not data["doc"]["stale"]
    assert converter == ["latex", "latex"]


def test_converter_upgrade_reuses_local_input(work, converter, monkeypatch):
    raw(work)
    read(work, refresh="false")
    monkeypatch.setattr(textify, "converter_version", lambda *args: "pandoc-test-v2")
    client = Client()
    data = read(work, refresh="false", client=client)
    assert not client.calls and converter == ["latex", "latex"]
    assert "v2" in data["doc"]["converter_version"]


def test_explicit_refresh_conditions_get_and_reconverts(work, converter):
    read(work, client=Client({LATEX + "v1": (200, source_blob())}))
    client = Client({LATEX: (304, b"")})
    data = read(work, refresh="true", client=client)
    assert data["network"] and converter == ["latex", "latex"]
    assert client.calls[0][1]["If-None-Match"] == '"test"'


def test_explicit_refresh_failure_does_not_return_old_body(work, converter):
    raw(work)
    first = read(work, refresh="false")
    with pytest.raises(envelope.EnvelopeError) as error:
        read(work, refresh="true", client=Client())
    assert error.value.code == envelope.SOURCE_ERROR
    assert Path(first["doc"]["path"]).is_file()


def test_offline_missing_is_projection_missing_and_no_requests(work, converter):
    client = Client()
    with pytest.raises(envelope.EnvelopeError) as error:
        read(work, refresh="false", client=client)
    assert error.value.code == envelope.PROJECTION_MISSING and client.calls == []


def test_html_fallback_from_pdf_only_eprint_and_single_ledger(work, converter):
    pytest.importorskip("lxml")
    client = Client({LATEX + "v1": (200, b"%PDF-1.7 not latex"),
                     HTML_URL + "v1": (200, HTML.encode()),
                     HTML_URL + "v1/plot.svg": (200, SVG),
                     "https://arxiv.org/html/plot.svg": (200, SVG)})
    data = read(work, client=client)
    assert converter == ["html"]
    assert data["figures"]
    assert any("回退 HTML" in item for item in data["warnings"])
    conn = store.connect(config.db_path(work[0]))
    runs = conn.execute("SELECT * FROM run").fetchall()
    conn.close()
    assert len(runs) == 1 and runs[0]["requests"] == client.requests


def test_html_offline_missing_media_is_visible_and_cached(work, converter):
    pytest.importorskip("lxml")
    raw(work, kind="source_html", blob=HTML.encode())
    client = Client()
    first = read(work, refresh="false", client=client)
    second = read(work, refresh="false", client=client)
    assert not client.calls
    assert first["figures"] == []
    assert any("媒体缺失" in item for item in first["warnings"])
    assert "图件缺失" in Path(first["doc"]["path"]).read_text(encoding="utf-8")
    assert first["warnings"] == second["warnings"]


def test_html_media_cache_survives_converter_upgrade_offline(work, converter, monkeypatch):
    pytest.importorskip("lxml")
    raw(work, kind="source_html", blob=HTML.encode())
    # 源码路线取件失败，再使用本地 HTML 与联网图件。
    first = read(work, client=Client({"https://arxiv.org/html/plot.svg": (200, SVG)}))
    monkeypatch.setattr(textify, "converter_version", lambda *args: "pandoc-test-v2")
    client = Client()
    second = read(work, refresh="false", client=client)
    assert first["figures"] and second["figures"] and client.calls == []
    assert first["figures"][0]["sha256"] == second["figures"][0]["sha256"]


def test_local_html_is_used_before_any_network_source(work, converter):
    pytest.importorskip("lxml")
    raw(work, kind="source_html", blob=HTML.encode())
    client = Client({"https://arxiv.org/html/plot.svg": (200, SVG)})
    data = read(work, client=client)
    assert data["figures"] and client.requests == 1
    assert all("e-print" not in call[0] for call in client.calls)


def test_auto_converter_upgrade_uses_cached_html_media_without_network(work, converter, monkeypatch):
    pytest.importorskip("lxml")
    raw(work, kind="source_html", blob=HTML.encode())
    read(work, client=Client({"https://arxiv.org/html/plot.svg": (200, SVG)}))
    monkeypatch.setattr(textify, "converter_version", lambda *args: "pandoc-test-v2")
    client = Client()
    data = read(work, client=client)
    assert data["figures"] and not client.calls and converter == ["html", "html"]


def test_existing_body_is_readable_offline_after_source_loss(work, converter, monkeypatch):
    sha = raw(work)
    first = read(work, refresh="false")
    source = paths.byte_path(work[0], sha)
    paths.make_writable(source)
    source.unlink()
    monkeypatch.setattr(textify, "converter_version", lambda *args: "pandoc-test-v2")
    data = read(work, refresh="false", client=Client())
    assert data["doc"]["sha256"] == first["doc"]["sha256"]
    assert any("本地缺少原件" in item for item in data["warnings"])


def test_conversion_failure_keeps_raw_and_failure_ledger(work, converter, monkeypatch):
    def broken(*args, **kwargs):
        raise textify.TextifyError("broken", "bad conversion")
    monkeypatch.setattr(textify, "run_pandoc", broken)
    with pytest.raises(envelope.EnvelopeError):
        read(work, client=Client({LATEX + "v1": (200, source_blob())}))
    conn = store.connect(config.db_path(work[0]))
    assert store.artifact_of(conn, work[1], "source_tar_gz") is not None
    assert store.artifact_of(conn, work[1], "paper_manifest") is None
    rows = conn.execute("SELECT * FROM run").fetchall()
    conn.close()
    assert len(rows) == 1 and rows[0]["status"] == "failed" and rows[0]["requests"] == 2


def test_busy_happens_before_network_or_conversion(work, converter):
    conn = store.connect(config.db_path(work[0]))
    client = Client()
    with store.write_tx(conn):
        with pytest.raises(envelope.EnvelopeError) as error:
            read(work, client=client)
    conn.close()
    assert error.value.code == envelope.BUSY and client.calls == [] and converter == []


def test_cli_read_is_locator_not_full_body(work, converter, capsys):
    raw(work)
    code = cli.main(["--root", str(work[0]), "read", "--work", work[1], "--refresh", "false"])
    data = json.loads(capsys.readouterr().out)
    assert code == 0 and data["ok"] and data["command"] == "read"
    assert "markdown" not in data["data"] and data["data"]["doc"]["path"]


def test_projection_failure_keeps_authoritative_locator(work, converter, monkeypatch):
    raw(work)
    first = read(work, refresh="false")
    def denied(*args, **kwargs):
        raise PermissionError("projection denied")
    monkeypatch.setattr(service, "_work_projection", denied)
    monkeypatch.setattr(paths, "project_bytes", denied)
    data = read(work, refresh="false")
    assert data["doc"]["path"] == first["doc"]["path"]
    assert "work_path" not in data["doc"]
    assert any("投影不可用" in item for item in data["warnings"])


def test_artifact_reregistration_updates_current_selection(work):
    one = raw(work, blob=b"one")
    raw(work, blob=b"two")
    raw(work, blob=b"one")
    conn = store.connect(config.db_path(work[0]))
    assert store.artifact_of(conn, work[1], "source_tar_gz")["sha256"] == one
    assert len(store.artifacts_of(conn, work[1], "source_tar_gz")) == 2
    conn.close()


@pytest.mark.parametrize("ref", ["../outside.png", "figures/../../x", "figures/C:x", "figures/..\\x"])
def test_manifest_rejects_escaping_figure_paths(ref):
    assert not paper.safe_ref(ref)


@pytest.mark.parametrize("route", ["latex", "latex-nested", "html", "html-object"])
def test_real_pandoc_read_from_cached_sources(work, real_pandoc, route):
    if route.startswith("html"):
        pytest.importorskip("lxml")
        embedded = HTML.replace('src="plot.svg"', 'src="data:image/svg+xml;base64,'
                                + __import__("base64").b64encode(SVG).decode() + '"')
        if route == "html-object":
            embedded = embedded.replace('<img src=', '<object data=').replace(
                'alt="A useful plot"/>', 'type="image/svg+xml"></object>')
        raw(work, kind="source_html", blob=embedded.encode())
    else:
        if route == "latex-nested":
            raw(work, blob=source_blob(tex=TEX.replace("{plot.svg}", "{figures/plot.svg}"),
                                      figure_name="figures/plot.svg"))
        else:
            raw(work)
    data = read(work, refresh="false", pandoc=real_pandoc)
    text = Path(data["doc"]["path"]).read_text(encoding="utf-8")
    assert "Methods" in text and data["anchors"]
    assert data["figures"] and "图件缺失" not in text
    assert not data["network"]
    if route.startswith("latex"):
        assert "$E=mc^2$" in text

"""源码预处理与真实 pandoc 的论文阅读回归；全部使用独立临时目录。"""

import io
import re
import tarfile
from pathlib import Path

import pytest

from academic_search import latex, service, textify


def files(root, mapping):
    for name, content in mapping.items():
        file = root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding="utf-8")
    return str(root / "main.tex"), str(root)


def test_expand_nested_inputs_comments_and_verbatim(tmp_path):
    root, directory = files(tmp_path, {
        "main.tex": "% \\input{missing}\n\\input{sections/method}\n"
                    r"\verb|\input{missing}|\begin{verbatim}\input{missing}\end{verbatim}",
        "sections/method.tex": r"\section{Method}\input{tables/result}",
        "tables/result.tex": r"\begin{table}\label{tab:result}Value\end{table}",
    })
    out = latex.expand_inputs(root, directory)
    assert r"\section{Method}" in out and "Value" in out
    assert r"\verb|\input{missing}|" in out
    assert textify.source_refmap(out)["tab:result"] == ("tbl", "1")


@pytest.mark.parametrize("include", ["../outside", "main"])
def test_expand_rejects_escape_and_cycle(tmp_path, include):
    root, directory = files(tmp_path, {"main.tex": r"\input{" + include + "}"})
    with pytest.raises(ValueError):
        latex.expand_inputs(root, directory)


def test_prepare_balanced_abstract_in_preamble_and_rules():
    source = (r"\documentclass{article}\abstract{Despite {nested} text and \{braces\}.}"
              r"\begin{document}\maketitle\section{Intro}"
              r"\cmidrule[0.4pt](lr){2-11}\cmidrule(r){12-17}"
              r"\verb|\cmidrule(lr){1-2}|\end{document}")
    out = latex.prepare(source)
    assert out.index(r"\maketitle") < out.index(r"\section*{Abstract}") < out.index(r"\section{Intro}")
    assert r"Despite {nested} text and \{braces\}." in out
    assert r"\cmidrule[0.4pt]" not in out and "12-17" not in out
    assert r"\verb|\cmidrule(lr){1-2}|" in out


def test_bibliographies_respects_declarations_nested_and_order(tmp_path):
    root, directory = files(tmp_path, {"main.tex": "", "wrong.bib": "",
                                       "refs/first.bib": "", "second.bib": ""})
    found = latex.bibliographies(r"\bibliography{refs/first,second}"
                                r"\addbibresource[location=local]{second.bib}", root, directory)
    assert [Path(file).name for file in found] == ["first.bib", "second.bib"]
    with pytest.raises(ValueError):
        latex.bibliographies(r"\bibliography{missing}", root, directory)


def test_source_numbering_skips_starred_sections_and_handles_appendices():
    source = (r"\section[Short]{Main}\label{sec:main}\section*{Acknowledgments}"
              r"\appendix\section{Data}\label{app:data}\section{Poses}\label{app:poses}"
              r"\subsection{Detail}\label{app:detail}"
              r"\begin{table*}\begin{center}\caption{Cap}\label{tab:x}\end{center}\end{table*}")
    mapping = textify.source_refmap(source)
    assert mapping["sec:main"] == ("sec", "1")
    assert mapping["app:poses"] == ("app", "B")
    assert mapping["app:detail"] == ("app", "B.1")
    assert mapping["tab:x"] == ("tbl", "1")
    out, stats = textify.resolve_refs(r"[\[app:poses\]](#app:poses) [\[tab:x,tab:missing\]](#tab:x,tab:missing)",
                                      {"app:poses": ("sec", "8")}, mapping)
    assert "[Appendix B]" in out and "[Table 1, Table ?]" in out
    assert stats["unresolved_targets"] == ["tab:missing"]


def test_polish_keeps_names_and_removes_presentation_attributes():
    out, stats = textify.polish("[Yang et al.]{.nocase} and [Example]{.uri}")
    assert out == "[Yang et al.] and [Example]" and stats["attrs_dropped"] == 2


def test_appendix_outline_covers_unlabelled_sections_and_paragraphs(tmp_path):
    source = (r"\section{Main}\appendix\section{Data}\label{app:data}"
              r"\paragraph{Data.}\paragraph{\benchname{}.}"
              r"\section{Sampling}\label{app:sampling}\subsection{Prior}"
              r"\paragraph{Details.}\section{Ablations}\section{Implementation}"
              r"\section{Limitations and Future Work}\section{Prompts}"
              r"\subsection{Judge}\label{app:judge}\paragraph{Revisiting benchmark.}")
    rendered = ("# Main\n# Data {#app:data}\n#### Data. {#data.}\n#### MAC-Bench. {#mac-bench.}\n"
                "# Sampling {#app:sampling}\n## Prior {#prior}\n#### Details. {#details.}\n"
                "# Ablations\n# Implementation\n# Limitations and Future Work\n# Prompts\n"
                "## Judge {#app:judge}\n#### Revisiting benchmark. {#revisiting-benchmark.}\n"
                r"See [\[app:sampling\]](#app:sampling) and [\[app:judge\]](#app:judge).")
    out, stats = textify.normalise_pandoc_output(rendered, str(tmp_path), str(tmp_path / "figures"),
                                               tex_text=source)
    assert "<!-- sec: A · Data. id:data. -->" in out
    assert "<!-- sec: A · MAC-Bench. id:mac-bench. -->" in out
    assert "<!-- sec: B.1 id:prior -->" in out
    assert "<!-- sec: B.1 · Details. id:details. -->" in out
    assert "<!-- sec: E -->\n# Limitations and Future Work" in out
    assert "<!-- sec: F.1 · Revisiting benchmark." in out
    assert "[Appendix B]" in out and "[Appendix F.1]" in out
    assert stats["anchors"]["unmatched_heads"] == 0
    assert not re.search(r"<!-- sec: [0-9]+\.0\.0", out)


@pytest.mark.parametrize("depth,number", [(2, None), (3, "A.0.1"), (4, "A.0.1.1")])
def test_outline_respects_explicit_numbering_depth(depth, number):
    source = (r"\setcounter{secnumdepth}{" + str(depth) + r"}\appendix\section{Data}"
              r"\subsubsection{Nested}\paragraph{Detail.}")
    _, outline = textify._source_structure(source)
    assert outline[1 if depth < 4 else 2]["number"] == number


def test_starred_and_unknown_headings_do_not_take_number_from_macro_heading(tmp_path):
    source = r"\section*{Overview}\appendix\section{\model{}}\section{Next}"
    rendered = "# Overview {.unnumbered}\n# Abstract {.unnumbered}\n# OverSeer\n# Next"
    out, stats = textify.normalise_pandoc_output(rendered, str(tmp_path), str(tmp_path / "figures"),
                                               tex_text=source)
    assert "<!-- sec: A -->\n# OverSeer" in out
    assert "<!-- sec: B -->\n# Next" in out
    assert "<!-- sec: Overview -->" in out and "<!-- sec: Abstract -->" in out
    assert stats["anchors"]["unmatched_heads"] == 0


def test_real_pandoc_multifile_abstract_tables_appendix_and_bibs(tmp_path, real_pandoc):
    mapping = {
        "main.tex": (r"\documentclass{article}\usepackage{booktabs}\usepackage{natbib}"
                     r"\abstract{\input{sections/abstract}}\begin{document}\maketitle"
                     r"\input{sections/main}\section*{Acknowledgments}\appendix"
                     r"\section{Data}\label{app:data}\paragraph{Data.}An unnumbered paragraph."
                     r"\section{Poses}\label{app:poses}\subsection{Prior}\label{app:prior}"
                     r"\paragraph{Details.}Another unnumbered paragraph."
                     r"\section{Ablations}\section{Implementation}\section{Limitations}"
                     r"\bibliography{refs/one,two,three}\end{document}"),
        "sections/abstract.tex": "Despite the success of video world models, we study shared memory.",
        "sections/main.tex": (r"\section{Method}\label{sec:method}"
                              r"See \ref{app:poses}, \ref{tab:results}. \citep{one,two,three}"
                              r"\input{tables/results}"),
        "tables/results.tex": (r"\begin{table}\begin{tabular}{lr}"
                               r"Name & Value\\\cmidrule(lr){1-2}Score & 42\\"
                               r"\end{tabular}\caption{Results}\label{tab:results}\end{table}"),
        "refs/one.bib": "@article{one, author={Alice Alpha}, title={One}, year={2024}}",
        "two.bib": "@article{two, author={Bob Beta}, title={Two}, year={2025}}",
        "three.bib": "@article{three, author={Cathy Gamma}, title={Three}, year={2026}}",
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in mapping.items():
            data = content.encode()
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    out, stats = service._convert_latex(None, "2610.00001", tmp_path, real_pandoc, None,
                                       blob=buffer.getvalue())
    assert re.search(r"(?m)^# Abstract", out)
    assert "Despite the success of video world models" in out
    assert "(lr)" not in out and "Score" in out and "42" in out
    assert "[Appendix B]" in out and "[Table 1]" in out
    assert "<!-- sec: A · Data. id:data. -->" in out
    assert "<!-- sec: B.1 · Details. id:details. -->" in out
    assert re.search(r"<!-- sec: E(?: id:[^ ]+)? -->\n# Limitations", out), "\n".join(
        line for line in out.splitlines() if line.startswith(("<!-- sec:", "#")))
    assert stats["anchors"]["unmatched_heads"] == 0
    assert stats["refs"]["unresolved"] == 0
    assert not any("not found" in warning for warning in stats["pandoc_warnings"])
    assert all(name in out for name in ("Alpha", "Beta", "Gamma"))
    assert (tmp_path / "src" / "main.tex").read_text(encoding="utf-8") == mapping["main.tex"]

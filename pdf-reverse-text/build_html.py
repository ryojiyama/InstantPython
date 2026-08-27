#!/usr/bin/env python3
"""
build_html.py

toc.json と source/ のマークダウンから、読書用の HTML を生成する。

出力:
    converted_pdf/index.html       章の目次
    converted_pdf/chapter01.html   1章の全文（話の目次 + 本文）
    converted_pdf/chapter02.html   ...

章の構成は toc.json で定義する。
各章に含まれる話（セクション）は、マークダウン内の見出し行から自動で拾う。

段落の結合には merge_paragraphs.py のルールをそのまま使う。

使い方:
    python3 build_html.py
    python3 build_html.py --toc toc.json --src source --out converted_pdf
"""

import argparse
import html
import json
import re
import sys
from pathlib import Path

from merge_paragraphs import merge_lines

# ---------------------------------------------------------------- 英文の判定

CJK = re.compile(r'[\u3040-\u30ff\u3400-\u9fff]')
LATIN = re.compile(r'[A-Za-z]')

JP_END = "。！？」』"          # 和文の終止
EN_END = ".!?"                 # 欧文の終止
EN_TAIL = "\"'’”)】"           # 終止符の後に続きうる閉じ記号
NEXT_OK = "「『（“\"\u3000—"   # 次の文の先頭に来てよい記号


def is_english(sentence: str) -> bool:
    """文の主言語が英語かどうか。固有名詞の和文が混ざっても英語と判定する。"""
    latin = len(LATIN.findall(sentence))
    cjk = len(CJK.findall(sentence))
    if latin == 0:
        return False
    return latin > cjk * 2


def _starts_sentence(ch: str) -> bool:
    return ch.isupper() or bool(CJK.match(ch)) or ch in NEXT_OK


def split_sentences(text: str):
    """文単位に分割する。区切りの空白は前の文に残し、順序と字面を保つ。"""
    out, buf = [], ""
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        buf += ch

        if ch in JP_END:
            j = i + 1
            while j < n and text[j] == " ":
                buf += text[j]
                j += 1
            out.append(buf)
            buf = ""
            i = j
            continue

        if ch in EN_END:
            j = i + 1
            while j < n and text[j] in EN_TAIL:
                buf += text[j]
                j += 1
            k = j
            while k < n and text[k] == " ":
                k += 1
            if k >= n or (k > j and _starts_sentence(text[k])):
                buf += text[j:k]
                out.append(buf)
                buf = ""
                i = k
                continue

        i += 1

    if buf:
        out.append(buf)
    return out or [text]


def markup_paragraph(text: str) -> str:
    """段落を文ごとに分け、英文を <span class="en"> で包む。"""
    out = []
    for s in split_sentences(text):
        core = s.rstrip()
        tail = s[len(core):]
        if core and is_english(core):
            out.append('<span class="en">%s</span>%s'
                       % (html.escape(core), tail))
        else:
            out.append(html.escape(core) + tail)
    return "".join(out)


# ---------------------------------------------------------------- 構造の解析

def parse_chapter(md_path: Path):
    """マークダウンを (見出し, [段落...]) のセクション列に分解する。"""
    lines = md_path.read_text(encoding="utf-8").splitlines()
    blocks = merge_lines(lines)

    sections = []
    current = None

    for kind, text in blocks:
        if kind == "heading":
            current = {"title": text.lstrip("# ").strip(), "paras": []}
            sections.append(current)
        elif kind == "sep":
            continue
        else:
            if current is None:
                current = {"title": "", "paras": []}
                sections.append(current)
            current["paras"].append(text)

    return [s for s in sections if s["paras"]]


def slug(i: int) -> str:
    return "sec%02d" % i


# ---------------------------------------------------------------- CSS

CSS = """
:root{
  --paper:#FCFBF7;
  --ink:#2E2A24;
  --rubric:#9A3B2E;
  --en:#7A4A2F;
  --rule:#E3DED2;
  --faint:#B9AC90;   /* 装飾記号のみ。本文テキストには使わない */
  --muted:#7C6F50;   /* 読ませる淡色。背景に対して 4.78:1 */
  --serif: Georgia,"Times New Roman","Hiragino Mincho ProN","Yu Mincho","YuMincho",serif;
}
*{box-sizing:border-box;}
body{
  margin:0; background:#EFEBE2; color:var(--ink);
  font-family:var(--serif);
  -webkit-text-size-adjust:100%;
}
.sheet{
  max-width:44rem; margin:0 auto; background:var(--paper);
  border-left:1px solid var(--rule); border-right:1px solid var(--rule);
  min-height:100vh; padding:44px 34px 96px;
  display:flow-root;          /* フロートを内包し、選択範囲の描画を親幅に広げない */
  position:relative;
}
.rubric{ color:var(--rubric); font-size:12px; letter-spacing:.3em; }
h1{ font-size:26px; font-weight:normal; margin:6px 0 4px; letter-spacing:.04em; }
h2{ font-size:17px; font-weight:normal; margin:0 0 4px; letter-spacing:.02em; }
.lede{ color:#6E6255; font-size:13px; line-height:1.8; margin:10px 0 0; }
hr.rule{ border:0; border-top:1px solid var(--rule); margin:18px 0 24px; }

nav.toc{ margin:0 0 8px; }
nav.toc ol{ list-style:none; margin:0; padding:0; }
nav.toc li{ border-bottom:1px solid var(--rule); }
nav.toc li:first-child{ border-top:1px solid var(--rule); }
nav.toc a{
  display:flex; gap:14px; align-items:baseline;
  padding:11px 4px; text-decoration:none; color:var(--ink);
}
nav.toc a:hover, nav.toc a:focus-visible{ background:#F5F1E7; }
nav.toc .num{ color:var(--rubric); font-size:11px; letter-spacing:.18em; min-width:3.4em; }
nav.toc .lbl{ font-size:15px; }
nav.toc .pending{ color:var(--muted); }
nav.toc li.off a{ pointer-events:none; }

.bar{
  position:sticky; top:0; z-index:5; background:var(--paper);
  border-bottom:1px solid var(--rule);
  display:flex; gap:10px; align-items:center; flex-wrap:wrap;
  padding:9px 0; margin-bottom:26px;
}
.bar a{ color:var(--rubric); text-decoration:none; font-size:12px; letter-spacing:.14em; }
.bar a:hover{ text-decoration:underline; }
.bar .sp{ flex:1; }
button{
  font-family:var(--serif); font-size:12px; color:var(--ink);
  background:transparent; border:1px solid var(--rule); border-radius:3px;
  padding:5px 11px; cursor:pointer;
}
button:hover{ background:#F5F1E7; }
button:focus-visible, a:focus-visible{ outline:2px solid var(--rubric); outline-offset:2px; }

section.ep{ margin:0 0 62px; scroll-margin-top:56px; display:flow-root; }
section.ep .rubric{ display:block; margin-bottom:6px; }
p{ font-size:15px; line-height:1.5; margin:0 0 1.15em; text-align:justify; }
p.first{ display:flow-root; }
p.first::first-letter{
  float:left; font-size:44px; line-height:.9; color:var(--rubric);
  padding:4px 10px 0 0;
}
.en{ color:var(--en); }
body.plain .en{ color:var(--ink); }

::selection{ background:#DCC9A0; color:var(--ink); }
::-moz-selection{ background:#DCC9A0; color:var(--ink); }

.star{ text-align:center; color:var(--faint); font-size:11px; letter-spacing:.4em; margin:30px 0; }
.top{ display:block; text-align:center; font-size:12px; letter-spacing:.16em;
      color:var(--rubric); text-decoration:none; margin-top:8px; }
.top:hover{ text-decoration:underline; }

@media (max-width:640px){
  .sheet{ padding:30px 20px 80px; border:0; }
  h1{ font-size:22px; }
  p{ text-align:left; }
}
@media print{
  body{ background:#fff; }
  .sheet{ border:0; max-width:none; }
  .bar, .top{ display:none; }
  section.ep{ page-break-inside:auto; }
}
"""

TOGGLE_JS = """
(function(){
  var b=document.getElementById('tint');
  if(!b) return;
  var on=true;
  b.addEventListener('click',function(){
    on=!on;
    document.body.classList.toggle('plain',!on);
    b.textContent='英文の色分け：'+(on?'ON':'OFF');
    b.setAttribute('aria-pressed', on?'true':'false');
  });
})();
"""


def page(title, body, css=CSS, script=""):
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "<title>%s</title>\n<style>%s</style>\n</head>\n<body>\n"
        '<div class="sheet">\n%s\n</div>\n%s\n</body>\n</html>\n'
        % (html.escape(title), css, body,
           ("<script>%s</script>" % script) if script else "")
    )


# ---------------------------------------------------------------- 生成

def build_index(toc, built):
    b = toc["book"]
    parts = ['<div class="rubric">目次</div>',
             "<h1>%s</h1>" % html.escape(b.get("title", "")),
             "<h2>%s</h2>" % html.escape(b.get("subtitle", ""))]
    if b.get("note"):
        parts.append('<p class="lede">%s</p>' % html.escape(b["note"]))
    parts.append('<hr class="rule">')

    parts.append('<nav class="toc" aria-label="章の目次"><ol>')
    for ch in toc["chapters"]:
        n = ch["number"]
        ready = n in built
        cls = "" if ready else ' class="off"'
        href = "chapter%02d.html" % n if ready else "#"
        label = ch.get("title") or "（未収録）"
        lcls = "lbl" if ready else "lbl pending"
        note = ch.get("note") or ""
        extra = ('<span class="lbl pending" style="margin-left:auto;font-size:12px">%s</span>'
                 % html.escape(note)) if (ready and note) else ""
        parts.append(
            '<li%s><a href="%s"%s><span class="num">%s章</span>'
            '<span class="%s">%s</span>%s</a></li>'
            % (cls, href, "" if ready else ' aria-disabled="true" tabindex="-1"',
               n, lcls, html.escape(label), extra)
        )
    parts.append("</ol></nav>")
    return page(b.get("title", "目次"), "\n".join(parts))


def build_chapter(ch, sections):
    n = ch["number"]
    head = [
        '<div class="bar"><a href="index.html">← 章の目次</a><span class="sp"></span>'
        '<button id="tint" aria-pressed="true">英文の色分け：ON</button></div>',
        '<div class="rubric">CHAPTER %s ・ %s章</div>' % (roman(n), n),
        "<h1>%s</h1>" % html.escape(ch.get("title", "")),
    ]
    if ch.get("note"):
        head.append('<p class="lede">%s</p>' % html.escape(ch["note"]))
    head.append('<hr class="rule">')

    head.append('<nav class="toc" aria-label="話の目次"><ol>')
    for i, s in enumerate(sections, 1):
        head.append(
            '<li><a href="#%s"><span class="num">%02d</span>'
            '<span class="lbl">%s</span></a></li>'
            % (slug(i), i, html.escape(s["title"]))
        )
    head.append("</ol></nav>")

    body = []
    for i, s in enumerate(sections, 1):
        body.append('<section class="ep" id="%s">' % slug(i))
        body.append('<div class="star">＊</div>')
        body.append('<div class="rubric">%02d</div>' % i)
        body.append("<h2>%s</h2>" % html.escape(s["title"]))
        body.append('<hr class="rule">')
        for j, p in enumerate(s["paras"]):
            cls = ' class="first"' if j == 0 else ""
            body.append("<p%s>%s</p>" % (cls, markup_paragraph(p)))
        body.append('<a class="top" href="#">▲ 話の目次へ</a>')
        body.append("</section>")

    return page("%s章　%s" % (n, ch.get("title", "")),
                "\n".join(head + body), script=TOGGLE_JS)


def roman(n):
    vals = [(10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--toc", default="toc.json")
    ap.add_argument("--src", default="source")
    ap.add_argument("--out", default="converted_pdf")
    args = ap.parse_args()

    toc_path, src_dir, out_dir = Path(args.toc), Path(args.src), Path(args.out)
    if not toc_path.exists():
        sys.exit("toc.json が見つかりません: %s" % toc_path)
    toc = json.loads(toc_path.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)

    built = {}
    for ch in toc["chapters"]:
        f = ch.get("file")
        if not f:
            continue
        md = src_dir / f
        if not md.exists():
            print("  スキップ: %s が見つかりません" % md)
            continue
        secs = parse_chapter(md)
        (out_dir / ("chapter%02d.html" % ch["number"])).write_text(
            build_chapter(ch, secs), encoding="utf-8")
        built[ch["number"]] = len(secs)

    (out_dir / "index.html").write_text(build_index(toc, built), encoding="utf-8")

    print("%-10s %s" % ("生成", "セクション数"))
    print("-" * 30)
    for n, c in sorted(built.items()):
        print("%-10s %d" % ("chapter%02d.html" % n, c))
    print("%-10s" % "index.html")
    print("-" * 30)
    print("出力先: %s/" % out_dir)


if __name__ == "__main__":
    main()

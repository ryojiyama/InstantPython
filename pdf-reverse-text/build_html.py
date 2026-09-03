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

段落の結合には merge_textparagraphs.py のルールをそのまま使う。
Markdown のリスト・引用・表・コードブロック・強調・リンクにも対応する。

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

try:
    from merge_textparagraphs import merge_lines
except ImportError:  # 旧ファイル名にも対応
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


# ------------------------------------------------------------ インライン記法

CODE_SPAN_RE = re.compile(r"(`+)(.+?)\1", re.S)
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;([^&]*)&quot;)?\)")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
STRONG_RE = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.S)
EM_RE = re.compile(r"(?<![\w*])(\*|_)(?=\S)(.+?)(?<=\S)\1(?![\w*])", re.S)
DEL_RE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.S)
RUBY_RE = re.compile(r"\{([^{}|]+)\|([^{}|]+)\}")  # {漢字|かんじ} → ルビ

SENTINEL = "\x00%d\x00"


def inline_md(text: str) -> str:
    """1 文ぶんのテキストを HTML に変換する。コードスパンは保護する。"""
    codes = []

    def stash(m):
        codes.append(m.group(2).strip())
        return SENTINEL % (len(codes) - 1)

    text = CODE_SPAN_RE.sub(stash, text)
    text = html.escape(text, quote=True)

    text = IMAGE_RE.sub(
        lambda m: '<img src="%s" alt="%s"%s>'
        % (m.group(2), m.group(1),
           ' title="%s"' % m.group(3) if m.group(3) else ""),
        text,
    )
    text = LINK_RE.sub(
        lambda m: '<a href="%s" rel="noopener">%s</a>' % (m.group(2), m.group(1)),
        text,
    )
    text = RUBY_RE.sub(r"<ruby>\1<rt>\2</rt></ruby>", text)
    text = STRONG_RE.sub(r"<strong>\2</strong>", text)
    text = EM_RE.sub(r"<em>\2</em>", text)
    text = DEL_RE.sub(r"<del>\1</del>", text)

    for i, c in enumerate(codes):
        text = text.replace(SENTINEL % i, "<code>%s</code>" % html.escape(c))
    return text


def markup_text(text: str) -> str:
    """文ごとに分け、英文を <span class="en"> で包みつつインライン記法を適用する。"""
    out = []
    for s in split_sentences(text):
        core = s.rstrip()
        tail = s[len(core):]
        if core and is_english(core):
            out.append('<span class="en">%s</span>%s' % (inline_md(core), tail))
        else:
            out.append(inline_md(core) + tail)
    return "".join(out)


# ---------------------------------------------------------- ブロックの HTML 化

IDEOGRAPHIC_SPACE = "\u3000"
LIST_ITEM_RE = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$", re.S)
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})\s*([A-Za-z0-9_+-]*)")
NO_DROPCAP = set("「『（(“\"—…・＊*>#|-")


def _list_item(text):
    m = LIST_ITEM_RE.match(text)
    if not m:
        return 0, "ul", text.strip()
    indent, marker, body = m.group(1), m.group(2), m.group(3)
    tag = "ol" if marker[0].isdigit() else "ul"
    return len(indent.expandtabs(4)), tag, body.strip()


def render_list(items):
    """連続するリストブロックを入れ子つきの <ul>/<ol> にする。"""
    out, stack = [], []
    for text in items:
        indent, tag, body = _list_item(text)
        while stack and indent < stack[-1][0]:
            out.append("</li></%s>" % stack.pop()[1])
        if not stack:
            stack.append((indent, tag))
            out.append("<%s>" % tag)
        elif indent > stack[-1][0]:
            stack.append((indent, tag))
            out.append("<%s>" % tag)
        else:
            out.append("</li>")
        out.append("<li>%s" % markup_text(body))
    while stack:
        out.append("</li></%s>" % stack.pop()[1])
    return "".join(out)


def render_table(rows):
    """連続する表ブロックを <table> にする。2 行目の区切り行は捨てる。"""
    def cells(row):
        row = row.strip().strip("|")
        return [c.strip() for c in row.split("|")]

    if not rows:
        return ""
    head = cells(rows[0])
    body = rows[1:]
    aligns = ["left"] * len(head)
    if body and re.fullmatch(r"[\s|:\-]+", body[0]):
        for i, spec in enumerate(cells(body[0])[: len(head)]):
            if spec.startswith(":") and spec.endswith(":"):
                aligns[i] = "center"
            elif spec.endswith(":"):
                aligns[i] = "right"
        body = body[1:]

    out = ['<div class="tw"><table>', "<thead><tr>"]
    for i, c in enumerate(head):
        out.append('<th style="text-align:%s">%s</th>' % (aligns[i], markup_text(c)))
    out.append("</tr></thead><tbody>")
    for row in body:
        out.append("<tr>")
        for i, c in enumerate(cells(row)):
            a = aligns[i] if i < len(aligns) else "left"
            out.append('<td style="text-align:%s">%s</td>' % (a, markup_text(c)))
        out.append("</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def render_code(text):
    lines = text.split("\n")
    lang = ""
    m = FENCE_RE.match(lines[0]) if lines else None
    if m:
        lang = m.group(2)
        lines = lines[1:]
        if lines and FENCE_RE.match(lines[-1]):
            lines = lines[:-1]
    else:  # インデントコード
        lines = [re.sub(r"^(?: {4}|\t)", "", l) for l in lines]
    cls = ' class="lang-%s"' % html.escape(lang) if lang else ""
    return "<pre><code%s>%s</code></pre>" % (cls, html.escape("\n".join(lines)))


def render_quote(text):
    body = re.sub(r"^\s{0,3}>\s?", "", text)
    return "<blockquote><p>%s</p></blockquote>" % markup_text(body)


def render_body(blocks):
    """(kind, text) の列を本文 HTML に変換する。"""
    out = []
    i, n = 0, len(blocks)
    first_para = True

    while i < n:
        kind, text = blocks[i]

        if kind == "list":
            group = []
            while i < n and blocks[i][0] == "list":
                group.append(blocks[i][1])
                i += 1
            out.append(render_list(group))
            continue

        if kind == "table":
            group = []
            while i < n and blocks[i][0] == "table":
                group.append(blocks[i][1])
                i += 1
            out.append(render_table(group))
            continue

        if kind == "code":
            out.append(render_code(text))
        elif kind == "quote":
            out.append(render_quote(text))
        elif kind == "html":
            out.append(text)
        elif kind == "sep":
            out.append('<div class="star">＊</div>')
        elif kind == "heading":  # セクション内の小見出し (h3 以下)
            out.append("<h3>%s</h3>" % markup_text(heading_text(text)))
        elif kind == "para":
            head = text.lstrip(IDEOGRAPHIC_SPACE)
            drop = first_para and head[:1] not in NO_DROPCAP and not is_english(head)
            # ドロップキャップ時は字下げの全角スペースを外す (先頭文字が空白になるため)
            out.append('<p%s>%s</p>'
                       % (' class="first"' if drop else "",
                          markup_text(head if drop else text)))
            first_para = False
        i += 1

    return "\n".join(out)


# ---------------------------------------------------------------- 構造の解析

def heading_level(text: str) -> int:
    m = re.match(r"^\s{0,3}(#{1,6})\s", text)
    if m:
        return len(m.group(1))
    if "\n" in text:  # Setext
        return 1 if text.rsplit("\n", 1)[1].startswith("=") else 2
    return 2  # 「◯◯話」形式


def heading_text(text: str) -> str:
    text = text.split("\n", 1)[0]
    return re.sub(r"^\s{0,3}#{1,6}\s*", "", text).rstrip("# ").strip()


def parse_frontmatter(text: str) -> dict:
    meta = {}
    for line in text.splitlines()[1:]:
        if line.strip() in ("---", "..."):
            break
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip("\"'")
    return meta


def parse_chapter(md_path: Path):
    """マークダウンを (メタ情報, セクション列) に分解する。"""
    lines = md_path.read_text(encoding="utf-8").splitlines()
    blank_breaks = md_path.suffix.lower() in (".md", ".markdown")
    blocks = merge_lines(lines, blank_breaks=blank_breaks)

    meta, sections, current = {}, [], None

    for kind, text in blocks:
        if kind == "__unclosed_fence__":
            continue

        if kind == "frontmatter":
            meta.update(parse_frontmatter(text))
            continue

        if kind == "heading":
            lvl = heading_level(text)
            if lvl == 1 and not sections:
                meta.setdefault("title", heading_text(text))
                continue
            if lvl <= 2:
                current = {"title": heading_text(text), "blocks": []}
                sections.append(current)
                continue

        if current is None:
            current = {"title": "", "blocks": []}
            sections.append(current)
        current["blocks"].append((kind, text))

    for s in sections:
        # 前後の区切り線はセクションの飾り (＊) と重なるので落とす
        bs = s["blocks"]
        while bs and bs[0][0] == "sep":
            bs.pop(0)
        while bs and bs[-1][0] == "sep":
            bs.pop()
        s["html"] = render_body(bs)
    return meta, [s for s in sections if s["blocks"]]


def slug(i: int) -> str:
    return "sec%02d" % i


# ---------------------------------------------------------------- CSS

CSS = """
:root{
  --paper:#FCFBF7;
  --page:#EFEBE2;
  --ink:#2E2A24;
  --rubric:#9A3B2E;
  --en:#7A4A2F;
  --rule:#E3DED2;
  --faint:#B9AC90;   /* 装飾記号のみ。本文テキストには使わない */
  --muted:#7C6F50;   /* 読ませる淡色。背景に対して 4.78:1 */
  --wash:#F5F1E7;
  --serif: Georgia,"Times New Roman","Hiragino Mincho ProN","Yu Mincho","YuMincho",serif;
  --mono: ui-monospace,"SFMono-Regular",Menlo,Consolas,"Noto Sans Mono CJK JP",monospace;
}
*{box-sizing:border-box;}
html{ scroll-behavior:smooth; }
@media (prefers-reduced-motion:reduce){ html{ scroll-behavior:auto; } }
body{
  margin:0; background:var(--page); color:var(--ink);
  font-family:var(--serif);
  -webkit-text-size-adjust:100%;
  font-kerning:normal;
}
.sheet{
  max-width:42rem; margin:0 auto; background:var(--paper);
  border-left:1px solid var(--rule); border-right:1px solid var(--rule);
  min-height:100vh; padding:44px 40px 96px;
  display:flow-root;          /* フロートを内包し、選択範囲の描画を親幅に広げない */
  position:relative;
}
.rubric{ color:var(--rubric); font-size:12px; letter-spacing:.3em; }
h1{ font-size:26px; font-weight:normal; margin:6px 0 4px; letter-spacing:.04em; }
h2{ font-size:17px; font-weight:normal; margin:0 0 4px; letter-spacing:.02em; }
h3{ font-size:15px; font-weight:normal; color:var(--rubric);
    margin:2em 0 .8em; letter-spacing:.03em; }
.lede{ color:#6E6255; font-size:13px; line-height:1.9; margin:10px 0 0; }
hr.rule{ border:0; border-top:1px solid var(--rule); margin:18px 0 24px; }

nav.toc{ margin:0 0 8px; }
nav.toc ol{ list-style:none; margin:0; padding:0; }
nav.toc li{ border-bottom:1px solid var(--rule); }
nav.toc li:first-child{ border-top:1px solid var(--rule); }
nav.toc a{
  display:flex; gap:14px; align-items:baseline;
  padding:11px 4px; text-decoration:none; color:var(--ink);
}
nav.toc a:hover, nav.toc a:focus-visible{ background:var(--wash); }
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
button:hover{ background:var(--wash); }
button:focus-visible, a:focus-visible{ outline:2px solid var(--rubric); outline-offset:2px; }

.progress{
  position:fixed; inset:0 auto auto 0; height:2px; width:0;
  background:var(--rubric); z-index:9; transition:width .1s linear;
}
@media (prefers-reduced-motion:reduce){ .progress{ transition:none; } }

section.ep{ margin:0 0 62px; scroll-margin-top:60px; display:flow-root; }
section.ep .rubric{ display:block; margin-bottom:6px; }
p{ font-size:15px; line-height:1.9; margin:0 0 1.15em; text-align:justify;
   word-break:normal; overflow-wrap:anywhere; line-break:strict; }
p.first{ display:flow-root; }
p.first::first-letter{
  float:left; font-size:44px; line-height:.9; color:var(--rubric);
  padding:4px 10px 0 0;
}
.en{ color:var(--en); }
body.plain .en{ color:var(--ink); }

section.ep ul, section.ep ol{ margin:0 0 1.3em; padding-left:1.6em; }
section.ep li{ font-size:15px; line-height:1.9; margin:.2em 0; }
section.ep li::marker{ color:var(--rubric); }

blockquote{
  margin:1.6em 0; padding:.2em 0 .2em 1.2em;
  border-left:2px solid var(--faint); color:var(--muted);
}
blockquote p{ margin:0; font-size:14px; }

code{
  font-family:var(--mono); font-size:.86em;
  background:var(--wash); border:1px solid var(--rule); border-radius:3px;
  padding:.1em .35em;
}
pre{
  margin:1.6em 0; padding:14px 16px; overflow-x:auto;
  background:var(--wash); border:1px solid var(--rule); border-radius:4px;
}
pre code{ background:none; border:0; padding:0; font-size:13px; line-height:1.7; }

.tw{ overflow-x:auto; margin:1.6em 0; }
table{ border-collapse:collapse; width:100%; font-size:14px; }
th, td{ border-bottom:1px solid var(--rule); padding:8px 10px; }
th{ color:var(--rubric); font-weight:normal; letter-spacing:.06em;
    border-bottom:1px solid var(--faint); }
tbody tr:hover{ background:var(--wash); }

img{ max-width:100%; height:auto; display:block; margin:1.6em auto; }
ruby rt{ font-size:.5em; color:var(--muted); letter-spacing:0; }
strong{ font-weight:600; }
em{ font-style:italic; }
del{ color:var(--muted); }
a{ color:var(--rubric); }

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
  p.first::first-letter{ font-size:38px; }
}
@media print{
  body{ background:#fff; }
  .sheet{ border:0; max-width:none; }
  .bar, .top, .progress{ display:none; }
  section.ep{ page-break-inside:auto; }
  pre, blockquote, table{ page-break-inside:avoid; }
}
"""

TOGGLE_JS = """
(function(){
  var b=document.getElementById('tint');
  if(b){
    var on=true;
    b.addEventListener('click',function(){
      on=!on;
      document.body.classList.toggle('plain',!on);
      b.textContent='英文の色分け：'+(on?'ON':'OFF');
      b.setAttribute('aria-pressed', on?'true':'false');
    });
  }
  var bar=document.querySelector('.progress');
  if(bar){
    var tick=function(){
      var h=document.documentElement;
      var max=h.scrollHeight-h.clientHeight;
      bar.style.width=(max>0? (h.scrollTop/max*100):0)+'%';
    };
    addEventListener('scroll',tick,{passive:true});
    addEventListener('resize',tick);
    tick();
  }
})();
"""


def page(title, body, css=CSS, script="", desc=""):
    meta = ('<meta name="description" content="%s">\n' % html.escape(desc)) if desc else ""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "%s"
        "<title>%s</title>\n<style>%s</style>\n</head>\n<body>\n"
        '<div class="sheet">\n%s\n</div>\n%s\n</body>\n</html>\n'
        % (meta, html.escape(title), css, body,
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
    return page(b.get("title", "目次"), "\n".join(parts), desc=b.get("note", ""))


def build_chapter(ch, sections, meta=None):
    meta = meta or {}
    n = ch["number"]
    title = ch.get("title") or meta.get("title", "")
    note = ch.get("note") or meta.get("note", "")

    head = [
        '<div class="progress" role="presentation"></div>',
        '<div class="bar"><a href="index.html">← 章の目次</a><span class="sp"></span>'
        '<button id="tint" aria-pressed="true">英文の色分け：ON</button></div>',
        '<div class="rubric">CHAPTER %s ・ %s章</div>' % (roman(n), n),
        "<h1>%s</h1>" % html.escape(title),
    ]
    if note:
        head.append('<p class="lede">%s</p>' % html.escape(note))
    head.append('<hr class="rule">')

    if len(sections) > 1:
        head.append('<nav class="toc" aria-label="話の目次"><ol>')
        for i, s in enumerate(sections, 1):
            head.append(
                '<li><a href="#%s"><span class="num">%02d</span>'
                '<span class="lbl">%s</span></a></li>'
                % (slug(i), i, html.escape(s["title"] or "（無題）"))
            )
        head.append("</ol></nav>")

    body = []
    for i, s in enumerate(sections, 1):
        body.append('<section class="ep" id="%s">' % slug(i))
        body.append('<div class="star">＊</div>')
        if s["title"]:
            body.append('<div class="rubric">%02d</div>' % i)
            body.append("<h2>%s</h2>" % html.escape(s["title"]))
            body.append('<hr class="rule">')
        body.append(s["html"])
        if len(sections) > 1:
            body.append('<a class="top" href="#">▲ 話の目次へ</a>')
        body.append("</section>")

    return page("%s章　%s" % (n, title), "\n".join(head + body),
                script=TOGGLE_JS, desc=note)


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
        if not md.exists():  # 拡張子違いを補う
            for alt in (md.with_suffix(".md"), md.with_suffix(".txt")):
                if alt.exists():
                    md = alt
                    break
        if not md.exists():
            print("  スキップ: %s が見つかりません" % (src_dir / f))
            continue
        meta, secs = parse_chapter(md)
        (out_dir / ("chapter%02d.html" % ch["number"])).write_text(
            build_chapter(ch, secs, meta), encoding="utf-8")
        built[ch["number"]] = len(secs)

    (out_dir / "index.html").write_text(build_index(toc, built), encoding="utf-8")

    print("%-16s %s" % ("生成", "セクション数"))
    print("-" * 30)
    for n, c in sorted(built.items()):
        print("%-16s %d" % ("chapter%02d.html" % n, c))
    print("%-16s" % "index.html")
    print("-" * 30)
    print("出力先: %s/" % out_dir)


if __name__ == "__main__":
    main()

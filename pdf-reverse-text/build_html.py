#!/usr/bin/env python3
"""
build_html.py

toc.json と source/ のマークダウンから、読書用の HTML を生成する。

出力:
    converted_pdf/index.html        章の目次 + 全章検索
    converted_pdf/chapter01.html    1章の全文（話の目次 + 本文）
    converted_pdf/chapter02.html    ...
    converted_pdf/search-index.js   章をまたぐ検索の索引

章の構成は toc.json で定義する。
各章に含まれる話（セクション）は、マークダウン内の見出し行から自動で拾う。

段落の結合には merge_textparagraphs.py のルールをそのまま使う。
Markdown のリスト・引用・表・コードブロック・強調・リンクにも対応する。

英語学習向けの仕掛け:
    ・英文には lang="en" を付ける（Safari の辞書・読み上げ・翻訳が英語として扱う）
    ・本文は <article> に入れる（Safari のリーダーが本文だけを拾う）
    ・「英文だけ」トグルで和文を伏せる
    ・英文をクリックすると、その 1 文を英語音声で読み上げる
    ・どのページからでも章をまたいで全文検索できる

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
            # 文末と確定するまで buf には足さない (足すと i が戻ったとき二重になる)
            j = i + 1
            while j < n and text[j] in EN_TAIL:
                j += 1
            k = j
            while k < n and text[k] == " ":
                k += 1
            if k >= n or (k > j and _starts_sentence(text[k])):
                buf += text[i + 1:k]
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
    """文ごとに分け、英文は <span class="en" lang="en">、和文は <span class="ja"> で包む。

    lang="en" は Safari の「調べる」「読み上げ」「翻訳」に英語だと伝えるために要る。
    """
    out = []
    for s in split_sentences(text):
        core = s.rstrip()
        tail = s[len(core):]
        if not core:
            out.append(s)
            continue
        cls = "en" if is_english(core) else "ja"
        lang = ' lang="en"' if cls == "en" else ""
        out.append('<span class="%s"%s>%s</span>%s'
                   % (cls, lang, inline_md(core), tail))
    return "".join(out)


def contains_english(text: str) -> bool:
    """ブロック内に英文が 1 文でもあるか (英文だけ表示の判定に使う)。"""
    return any(is_english(s.rstrip()) for s in split_sentences(text) if s.strip())


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
            cls = (["first"] if drop else []) + ([] if contains_english(text) else ["ja"])
            out.append('<p%s>%s</p>'
                       % (' class="%s"' % " ".join(cls) if cls else "",
                          markup_text(head if drop else text)))
            first_para = False
        else:  # 想定外の種別。捨てずに段落として出し、気づけるよう警告する
            print("  警告: 未知のブロック種別 %r を段落として出力しました: %s"
                  % (kind, text[:30].replace("\n", " ")), file=sys.stderr)
            out.append("<p>%s</p>" % markup_text(text))
            first_para = False
        i += 1

    return "\n".join(out)


# ---------------------------------------------------------------- 構造の解析

SETEXT_RULE = re.compile(r"\s{0,3}(=+|-+)\s*")


def heading_level(text: str) -> int:
    lines = text.split("\n")
    m = re.match(r"^\s{0,3}(#{1,6})\s", lines[0])
    if m:
        return len(m.group(1))
    if len(lines) > 1 and SETEXT_RULE.fullmatch(lines[1]):  # Setext は 2 行目が下線
        return 1 if lines[1].lstrip().startswith("=") else 2
    return 2  # 「◯◯話」形式


def heading_text(text: str) -> str:
    text = text.split("\n", 1)[0]
    return re.sub(r"^\s{0,3}#{1,6}\s*", "", text).rstrip("# ").strip()


FM_KEY = re.compile(r"^[A-Za-z_][\w.\-]*\s*:")


def looks_like_frontmatter(text: str) -> bool:
    """本物の frontmatter か、ただの区切り線 --- かを見分ける。

    frontmatter は開始記号の次の行から key: value が続き、閉じ記号で終わる。
    空行や本文が挟まっていれば、それは水平線であって frontmatter ではない。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() not in ("---", "+++"):
        return False
    for line in lines[1:]:
        s = line.strip()
        if s in ("---", "...", "+++"):
            return True          # key: value だけで閉じられた
        if not s or not FM_KEY.match(s):
            return False         # 空行や本文が来た時点で違う
    return False                 # 閉じられていない


def normalize_blocks(blocks, blank_breaks):
    """frontmatter を騙った区切り線を、区切り線 + 本文に戻す。"""
    out = []
    for kind, text in blocks:
        if kind == "frontmatter" and not looks_like_frontmatter(text):
            lines = text.splitlines()
            out.append(("sep", lines[0]))
            out.extend(merge_lines(lines[1:], blank_breaks=blank_breaks))
        else:
            out.append((kind, text))
    return out


def parse_frontmatter(text: str):
    """(メタ情報, 閉じ記号より後ろに残った本文) を返す。

    閉じの --- の直後に空行がないと本文が同じブロックに入ってくる。
    捨てずに呼び出し側へ渡す。
    """
    meta, rest = {}, []
    closed = False
    for line in text.splitlines()[1:]:
        if closed:
            rest.append(line)
            continue
        if line.strip() in ("---", "..."):
            closed = True
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip("\"'")
    return meta, "\n".join(rest).strip("\n")


def parse_chapter(md_path: Path, dump: bool = False):
    """マークダウンを (メタ情報, セクション列) に分解する。"""
    lines = md_path.read_text(encoding="utf-8").splitlines()
    blank_breaks = md_path.suffix.lower() in (".md", ".markdown")
    blocks = merge_lines(lines, blank_breaks=blank_breaks)
    blocks = normalize_blocks(blocks, blank_breaks)

    # H1 が 1 つだけなら章題、複数あるなら「話」の見出しとして扱う
    h1_count = sum(1 for k, t in blocks if k == "heading" and heading_level(t) == 1)
    h1_is_title = h1_count == 1

    if dump:
        print("--- %s: 解析したブロック (H1=%d, 章題に使う=%s) ---"
              % (md_path, h1_count, h1_is_title), file=sys.stderr)
        for j, (k, t) in enumerate(blocks):
            one = t.replace("\n", "⏎")
            print("  [%03d] %-14s %s%s"
                  % (j, k, one[:60], "…" if len(one) > 60 else ""), file=sys.stderr)
        print("---", file=sys.stderr)

    meta, sections, current = {}, [], None

    def ensure():
        """無題の話を必要になった時点で開く。"""
        nonlocal current
        if current is None:
            current = {"title": "", "blocks": []}
            sections.append(current)
        return current

    for kind, text in blocks:
        if kind == "__unclosed_fence__":
            continue

        if kind == "frontmatter":
            fm, rest = parse_frontmatter(text)
            meta.update(fm)
            if rest.strip():
                ensure()["blocks"].append(("para", rest))
            continue

        if kind == "heading":
            # 見出し行の直後に空行がないと本文が同じブロックに入る。
            # 1 行目を見出し、残りを段落として扱い、取りこぼさない。
            lvl = heading_level(text)
            head, _, rest = text.partition("\n")
            if rest and SETEXT_RULE.fullmatch(rest.split("\n", 1)[0]):
                rest = rest.split("\n", 1)[1] if "\n" in rest else ""

            if lvl == 1 and h1_is_title and not any(s["blocks"] for s in sections):
                meta.setdefault("title", heading_text(head))
            elif lvl <= 2:
                current = {"title": heading_text(head), "blocks": []}
                sections.append(current)
            else:  # h3 以下は話の中の小見出し
                ensure()["blocks"].append(("heading", head))

            if rest.strip():
                ensure()["blocks"].append(("para", rest))
            continue

        ensure()["blocks"].append((kind, text))

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


# ------------------------------------------------------------ 章をまたぐ検索

STRIP_MD = [
    (re.compile(r"^\s{0,3}(`{3,}|~{3,}).*$", re.M), ""),      # フェンス行
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.M), ""),             # 見出し記号
    (re.compile(r"^\s{0,3}>\s?", re.M), ""),                  # 引用記号
    (re.compile(r"^\s*([-*+]|\d{1,9}[.)])\s+", re.M), ""),    # リスト記号
    (re.compile(r"!?\[([^\]]*)\]\([^)]*\)"), r"\1"),          # 画像・リンク
    (re.compile(r"\{([^{}|]+)\|[^{}|]+\}"), r"\1"),           # ルビ
    (re.compile(r"[*_`~]+"), ""),                             # 強調・コード
    (re.compile(r"\s+"), " "),
]


def plain_text(text: str) -> str:
    for pat, rep in STRIP_MD:
        text = pat.sub(rep, text)
    return text.strip()


def index_entries(number, chapter_title, sections):
    """検索索引の項目を作る。アンカーは build_chapter の連番と揃える。"""
    out = []
    for i, s in enumerate(sections, 1):
        for kind, text in s["blocks"]:
            if kind in ("sep", "html"):
                continue
            body = plain_text(text)
            if len(body) < 2:
                continue
            out.append({
                "f": "chapter%02d.html" % number,
                "c": number,
                "ct": chapter_title,
                "s": slug(i),
                "st": s["title"],
                "x": body,
            })
    return out


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
  /* 幅はウィンドウに対する割合。--sheet-w は幅メニューと --width で変わる */
  width:var(--sheet-w, 70vw); max-width:100%; min-width:min(100%, 36rem);
  margin:0 auto; background:var(--paper);
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

input.q{
  font-family:var(--serif); font-size:13px; color:var(--ink);
  background:var(--paper); border:1px solid var(--rule); border-radius:3px;
  padding:5px 9px; min-width:11em; flex:1 1 12em;
}
input.q::placeholder{ color:var(--muted); }
input.q:focus-visible{ outline:2px solid var(--rubric); outline-offset:1px; }
label.rate, label.width{ font-size:12px; color:var(--muted); letter-spacing:.08em; }
label.rate select, label.width select{
  font-family:var(--serif); font-size:12px; color:var(--ink);
  background:transparent; border:1px solid var(--rule); border-radius:3px;
  padding:3px 4px; margin-left:4px;
}

.results{ margin:0 0 26px; border-top:1px solid var(--rule); }
.results ol{ list-style:none; margin:0; padding:0; }
.results li{ border-bottom:1px solid var(--rule); }
.results a{
  display:block; padding:10px 4px; text-decoration:none; color:var(--ink);
}
.results a:hover, .results a:focus-visible{ background:var(--wash); }
.results .where{
  display:block; color:var(--rubric); font-size:11px; letter-spacing:.16em;
  margin-bottom:3px;
}
.results .snip{ display:block; font-size:14px; line-height:1.7; }
.results .none{ color:var(--muted); font-size:13px; padding:10px 4px; margin:0; }
.results .count{ color:var(--muted); font-size:11px; letter-spacing:.14em;
                 padding:8px 4px 0; margin:0; }

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
.en{
  color:var(--en); cursor:pointer;
  hyphens:auto; -webkit-hyphens:auto;
  overflow-wrap:normal; word-break:normal;   /* 英単語を途中で割らない */
}
.en:hover{ text-decoration:underline dotted var(--faint); text-underline-offset:3px; }
.en.speaking{ background:#F0E4C8; border-radius:2px; box-shadow:0 0 0 2px #F0E4C8; }
body.plain .en{ color:var(--ink); }

/* 英文だけ表示（見出しと表は構造として残す） */
body.enonly p .ja,
body.enonly li .ja,
body.enonly blockquote .ja{ display:none; }
body.enonly p.ja,
body.enonly li:not(:has(.en)),
body.enonly blockquote:not(:has(.en)){ display:none; }
body.enonly p.first::first-letter{
  float:none; font-size:inherit; line-height:inherit; color:inherit; padding:0;
}
body.enonly .en{ color:var(--ink); }

mark{ background:#F3E2B8; color:var(--ink); border-radius:2px; padding:0 .1em; }

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
  .sheet{ width:100%; min-width:0; padding:30px 20px 80px; border:0; }
  label.width{ display:none; }
  h1{ font-size:22px; }
  p{ text-align:left; }
  p.first::first-letter{ font-size:38px; }
}
@media print{
  body{ background:#fff; }
  .sheet{ border:0; max-width:none; width:auto; min-width:0; }
  .bar, .top, .progress, .results, input.q{ display:none; }
  section.ep{ page-break-inside:auto; }
  pre, blockquote, table{ page-break-inside:avoid; }
}
"""

TOGGLE_JS = """
(function(){
  var flip=function(id, label, cls, on){
    var b=document.getElementById(id);
    if(!b) return;
    var state=on;
    var paint=function(){
      b.textContent=label+'：'+(state?'ON':'OFF');
      b.setAttribute('aria-pressed', state?'true':'false');
    };
    b.addEventListener('click',function(){
      state=!state;
      document.body.classList.toggle(cls, cls==='plain' ? !state : state);
      paint();
    });
    paint();
  };
  flip('tint','英文の色分け','plain',true);   /* OFF のとき body.plain */
  flip('only','英文だけ','enonly',false);

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

# 英文をクリックすると、その 1 文だけを英語音声で読み上げる。
SPEECH_JS = """
(function(){
  if(!('speechSynthesis' in window)) return;
  var rate=document.getElementById('rate'), cur=null, voice=null;
  var load=function(){
    var vs=speechSynthesis.getVoices()||[];
    var en=vs.filter(function(v){ return /^en/i.test(v.lang); });
    voice=en.filter(function(v){ return v.localService; })[0] || en[0] || null;
  };
  load();
  speechSynthesis.addEventListener('voiceschanged', load);

  var stop=function(){
    speechSynthesis.cancel();
    if(cur){ cur.classList.remove('speaking'); cur=null; }
  };
  document.addEventListener('click',function(e){
    if(e.target.closest('a, button, input, select')) return;
    var el=e.target.closest('.en');
    if(!el) return;
    if(cur===el){ stop(); return; }
    stop();
    var u=new SpeechSynthesisUtterance(el.textContent);
    u.lang='en-US';
    if(voice) u.voice=voice;
    u.rate=rate ? parseFloat(rate.value) || 1 : 1;
    u.onend=u.onerror=function(){
      el.classList.remove('speaking');
      if(cur===el) cur=null;
    };
    cur=el; el.classList.add('speaking');
    speechSynthesis.speak(u);
  });
  addEventListener('keydown',function(e){ if(e.key==='Escape') stop(); });
  addEventListener('pagehide',stop);
})();
"""

# 検索結果から来たとき (#sec02|q=...) は、その話へ飛んで語を光らせる。
HIGHLIGHT_JS = """
(function(){
  var apply=function(){
    var raw=location.hash.slice(1).replace(/%7[Cc]q=/,'|q=');
    if(!raw) return;
    var id=raw, q='', p=raw.indexOf('|q=');
    if(p>=0){ id=raw.slice(0,p); q=decodeURIComponent(raw.slice(p+3)); }
    var root=document.getElementById(id);
    if(!root) return;
    root.scrollIntoView();
    if(!q) return;
    var needle=q.toLowerCase(), first=null, nodes=[], n;
    var walk=document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    while((n=walk.nextNode())) nodes.push(n);
    nodes.forEach(function(node){
      if(node.parentNode.closest('mark, pre')) return;
      var text=node.nodeValue, low=text.toLowerCase(), i=low.indexOf(needle);
      if(i<0) return;
      var frag=document.createDocumentFragment(), pos=0;
      while(i>=0){
        frag.appendChild(document.createTextNode(text.slice(pos,i)));
        var m=document.createElement('mark');
        m.textContent=text.slice(i,i+q.length);
        frag.appendChild(m);
        if(!first) first=m;
        pos=i+q.length;
        i=low.indexOf(needle,pos);
      }
      frag.appendChild(document.createTextNode(text.slice(pos)));
      node.parentNode.replaceChild(frag,node);
    });
    if(first) first.scrollIntoView({block:'center'});
  };
  apply();
  addEventListener('hashchange',apply);
})();
"""

# 章をまたぐ全文検索。索引は window.SEARCH_INDEX に入っている。
SEARCH_JS = """
(function(){
  var box=document.getElementById('q'), panel=document.getElementById('results');
  if(!box||!panel) return;
  var data=window.SEARCH_INDEX;
  if(!data||!data.length){
    box.disabled=true;
    box.placeholder='索引を読み込めません（章の目次から検索）';
    return;
  }
  var esc=function(s){
    return s.replace(/[&<>]/g,function(c){
      return c==='&'?'&amp;':c==='<'?'&lt;':'&gt;';
    });
  };
  var LIMIT=40;
  var render=function(q){
    panel.innerHTML='';
    if(!q){ panel.hidden=true; return; }
    panel.hidden=false;
    var needle=q.toLowerCase(), hits=[], total=0;
    for(var i=0;i<data.length;i++){
      var at=data[i].x.toLowerCase().indexOf(needle);
      if(at<0) continue;
      total++;
      if(hits.length<LIMIT) hits.push([data[i],at]);
    }
    if(!total){ panel.innerHTML='<p class="none">見つかりません</p>'; return; }
    var html='<p class="count">'+total+' 件'+(total>LIMIT?'（先頭 '+LIMIT+' 件）':'')+'</p><ol>';
    hits.forEach(function(h){
      var d=h[0], at=h[1];
      var s=Math.max(0,at-40), e=Math.min(d.x.length,at+q.length+70);
      var snip=(s>0?'…':'')+esc(d.x.slice(s,at))
        +'<mark>'+esc(d.x.slice(at,at+q.length))+'</mark>'
        +esc(d.x.slice(at+q.length,e))+(e<d.x.length?'…':'');
      var where=d.c+'章'+(d.ct?'　'+d.ct:'')+(d.st?'　・　'+d.st:'');
      var href=d.f+'#'+d.s+'|q='+encodeURIComponent(q);
      html+='<li><a href="'+href+'"><span class="where">'+esc(where)+'</span>'
           +'<span class="snip">'+snip+'</span></a></li>';
    });
    panel.innerHTML=html+'</ol>';
  };
  var timer;
  box.addEventListener('input',function(){
    clearTimeout(timer);
    timer=setTimeout(function(){ render(box.value.trim()); },120);
  });
  box.addEventListener('keydown',function(e){
    if(e.key==='Escape'){ box.value=''; render(''); box.blur(); }
  });
  addEventListener('keydown',function(e){
    if(e.key==='/'&&document.activeElement!==box&&!e.metaKey&&!e.ctrlKey){
      e.preventDefault(); box.focus(); box.select();
    }
  });
})();
"""


# ---------------------------------------------------------------- 本文の幅

# 幅メニューの選択肢 (ウィンドウ幅に対する %)。--width で既定値を変えられる。
WIDTH_CHOICES = (50, 60, 70, 80, 90, 100)
DEFAULT_WIDTH = 70


def width_control():
    opts = "".join(
        '<option value="%d"%s>%d%%</option>'
        % (w, " selected" if w == DEFAULT_WIDTH else "", w)
        for w in sorted(set(WIDTH_CHOICES) | {DEFAULT_WIDTH}))
    return ('<label class="width">幅'
            '<select id="width" aria-label="本文の幅（ウィンドウに対する割合）">'
            '%s</select></label>' % opts)


# 描画前に保存済みの幅を当てる (ちらつき防止のため <head> に置く)
WIDTH_HEAD_JS = """
(function(){
  var w=%d;
  try{ var s=localStorage.getItem('sheet-width'); if(s&&+s>=30&&+s<=100) w=+s; }catch(e){}
  document.documentElement.style.setProperty('--sheet-w', w+'vw');
})();
"""

WIDTH_JS = """
(function(){
  var sel=document.getElementById('width');
  if(!sel) return;
  var cur=parseFloat(getComputedStyle(document.documentElement)
                     .getPropertyValue('--sheet-w'))||0;
  if(cur){
    if(!sel.querySelector('option[value="'+cur+'"]')){
      var o=document.createElement('option'); o.value=cur; o.textContent=cur+'%';
      sel.appendChild(o);
    }
    sel.value=String(cur);
  }
  sel.addEventListener('change',function(){
    document.documentElement.style.setProperty('--sheet-w', sel.value+'vw');
    try{ localStorage.setItem('sheet-width', sel.value); }catch(e){}
  });
})();
"""


def page(title, body, css=CSS, script="", desc="", scripts=()):
    meta = ('<meta name="description" content="%s">\n' % html.escape(desc)) if desc else ""
    tags = "".join('<script src="%s"></script>\n' % s for s in scripts)
    script = WIDTH_JS + script
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ja">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "%s"
        "<title>%s</title>\n<style>%s</style>\n<script>%s</script>\n</head>\n<body>\n"
        '<div class="sheet">\n%s\n</div>\n%s%s\n</body>\n</html>\n'
        % (meta, html.escape(title), css, WIDTH_HEAD_JS % DEFAULT_WIDTH, body, tags,
           ("<script>%s</script>" % script) if script else "")
    )


# ---------------------------------------------------------------- 生成

def build_index(toc, built, index_js=""):
    b = toc["book"]
    parts = ['<div class="bar"><span class="rubric">目次</span><span class="sp"></span>'
             '<input id="q" class="q" type="search" placeholder="全章を検索（/）" '
             'aria-label="全章を検索">' + width_control() + '</div>',
             '<div id="results" class="results" hidden></div>',
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
    return page(b.get("title", "目次"), "\n".join(parts),
                script=index_js + SEARCH_JS, desc=b.get("note", ""))


def build_chapter(ch, sections, meta=None):
    meta = meta or {}
    n = ch["number"]
    title = ch.get("title") or meta.get("title", "")
    note = ch.get("note") or meta.get("note", "")

    head = [
        '<div class="progress" role="presentation"></div>',
        '<div class="bar">'
        '<a href="index.html">← 章の目次</a>'
        '<input id="q" class="q" type="search" placeholder="全章を検索（/）" '
        'aria-label="全章を検索">'
        '<span class="sp"></span>'
        + width_control() +
        '<label class="rate">読み上げ'
        '<select id="rate" aria-label="読み上げの速さ">'
        '<option value="0.8">0.8x</option>'
        '<option value="1" selected>1.0x</option>'
        '<option value="1.2">1.2x</option>'
        '</select></label>'
        '<button id="tint" aria-pressed="true">英文の色分け：ON</button>'
        '<button id="only" aria-pressed="false">英文だけ：OFF</button>'
        '</div>',
        '<div id="results" class="results" hidden></div>',
        # <article> にまとめると Safari のリーダーが本文だけを拾える
        "<article>",
        "<header>",
        '<div class="rubric">CHAPTER %s ・ %s章</div>' % (roman(n), n),
        "<h1>%s</h1>" % html.escape(title),
    ]
    if note:
        head.append('<p class="lede">%s</p>' % html.escape(note))
    head.append('<hr class="rule">')
    head.append("</header>")

    # 章の導入部 (題のない話) は番号を持たせず、目次にも出さない
    entries, num = [], 0
    for i, s in enumerate(sections, 1):
        if s["title"]:
            num += 1
            entries.append((i, num, s))
        else:
            entries.append((i, None, s))
    listed = [e for e in entries if e[1]]

    if len(listed) > 1:
        head.append('<nav class="toc" aria-label="話の目次"><ol>')
        for i, k, s in listed:
            head.append(
                '<li><a href="#%s"><span class="num">%02d</span>'
                '<span class="lbl">%s</span></a></li>'
                % (slug(i), k, html.escape(s["title"]))
            )
        head.append("</ol></nav>")

    body = []
    for i, k, s in entries:
        body.append('<section class="ep" id="%s">' % slug(i))
        body.append('<div class="star">＊</div>')
        if s["title"]:
            body.append('<div class="rubric">%02d</div>' % k)
            body.append("<h2>%s</h2>" % html.escape(s["title"]))
            body.append('<hr class="rule">')
        body.append(s["html"])
        if len(listed) > 1:
            body.append('<a class="top" href="#">▲ 話の目次へ</a>')
        body.append("</section>")
    body.append("</article>")

    return page("%s章　%s" % (n, title), "\n".join(head + body),
                script=TOGGLE_JS + SPEECH_JS + HIGHLIGHT_JS + SEARCH_JS,
                desc=note, scripts=("search-index.js",))


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
    ap.add_argument("--dump-blocks", action="store_true",
                    help="merge_lines が返したブロックを標準エラーに出す")
    ap.add_argument("--width", type=int, default=DEFAULT_WIDTH,
                    help="本文の幅の既定値。ウィンドウ幅に対する %% (30〜100、既定 %d)"
                    % DEFAULT_WIDTH)
    args = ap.parse_args()
    if not 30 <= args.width <= 100:
        sys.exit("--width は 30〜100 で指定してください: %d" % args.width)
    globals()["DEFAULT_WIDTH"] = args.width

    toc_path, src_dir, out_dir = Path(args.toc), Path(args.src), Path(args.out)
    if not toc_path.exists():
        sys.exit("toc.json が見つかりません: %s" % toc_path)
    toc = json.loads(toc_path.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)

    built, entries = {}, []
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
        meta, secs = parse_chapter(md, dump=args.dump_blocks)
        (out_dir / ("chapter%02d.html" % ch["number"])).write_text(
            build_chapter(ch, secs, meta), encoding="utf-8")
        built[ch["number"]] = len(secs)
        entries.extend(index_entries(
            ch["number"], ch.get("title") or meta.get("title", ""), secs))

    index_js = "window.SEARCH_INDEX=%s;\n" % json.dumps(entries, ensure_ascii=False)
    (out_dir / "search-index.js").write_text(index_js, encoding="utf-8")
    (out_dir / "index.html").write_text(
        build_index(toc, built, index_js), encoding="utf-8")

    print("%-16s %s" % ("生成", "セクション数"))
    print("-" * 30)
    for n, c in sorted(built.items()):
        print("%-16s %d" % ("chapter%02d.html" % n, c))
    print("%-16s" % "index.html")
    print("%-16s %d 項目 / %.0f KB"
          % ("search-index.js", len(entries), len(index_js.encode("utf-8")) / 1024))
    print("-" * 30)
    print("出力先: %s/" % out_dir)


if __name__ == "__main__":
    main()

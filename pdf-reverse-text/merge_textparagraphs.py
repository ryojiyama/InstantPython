#!/usr/bin/env python3
"""
merge_textparagraphs.py

converted_pdf/split_chapters/ 内の .txt / .md ファイルを読み込み、1文ごとに分断された
段落を本来のまとまりに結合して出力ディレクトリに出力する。

Markdown 対応:
  - コードフェンス (``` / ~~~) の中は一切加工せず、そのまま出力する
  - ATX 見出し (#) / Setext 見出し (===, ---) を見出しとして扱う
  - 箇条書き・番号付きリスト、引用 (>)、表 (|)、水平線、HTML ブロックを保持する
  - YAML フロントマター (先頭の ---) をそのまま維持する
  - リスト行・表行は空行を挟まず連結する (loose list 化を防ぐ)

使い方:
    python3 merge_textparagraphs.py
    python3 merge_textparagraphs.py --src converted_pdf/split_chapters --out converted_pdf/merged_chapters
    python3 merge_textparagraphs.py --ext .txt .md
    python3 merge_textparagraphs.py --blank-line break
    python3 merge_textparagraphs.py --no-report
"""

import argparse
import re
import sys
from pathlib import Path

IDEOGRAPHIC_SPACE = "\u3000"  # 全角スペース

# 見出し行: 行頭が全角数字 + 「話」 で始まるもの
HEADING_RE = re.compile(r"^[０-９]+話(?:[：:].+)?")

# 会話文の開始に使われる括弧
QUOTE_OPEN = "「"
QUOTE_CLOSE = "」"

# --- Markdown 用パターン ---------------------------------------------------
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")           # コードフェンス
ATX_RE = re.compile(r"^\s{0,3}#{1,6}(?:\s|$)")            # # 見出し
SETEXT_RE = re.compile(r"^\s{0,3}(?:=+|-{2,})\s*$")       # 下線式見出し
HR_RE = re.compile(r"^\s{0,3}([-*_])\s*(?:\1\s*){2,}$")   # 水平線 --- *** ___
LIST_RE = re.compile(r"^\s{0,3}(?:[-*+]|\d{1,9}[.)])\s+")  # リスト項目
BLOCKQUOTE_RE = re.compile(r"^\s{0,3}>")                   # 引用
TABLE_RE = re.compile(r"^\s{0,3}\|")                       # 表
HTML_RE = re.compile(r"^\s{0,3}</?[A-Za-z!]")              # HTML ブロック
INDENT_CODE_RE = re.compile(r"^(?: {4}|\t)\S")             # インデントコード

# 結合してはいけない (=独立ブロックとして扱う) 種別
VERBATIM_KINDS = {"code", "table", "html", "frontmatter", "sep", "heading"}
# 行を折り返しとして吸収できる種別
CONTINUABLE_KINDS = {"para", "list", "quote"}
# 連続時に空行を挟まず 1 改行で連結する種別
TIGHT_KINDS = {"list", "table"}


def is_heading(line: str) -> bool:
    """見出し行かどうか。Markdown の ATX 見出しも含める。"""
    if ATX_RE.match(line):
        return True
    return bool(HEADING_RE.match(line.lstrip()))


def is_separator(line: str) -> bool:
    return bool(HR_RE.match(line))


def is_latin(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch in ".,!?\"')]*_`")


def needs_space(prev: str, nxt: str) -> bool:
    """連結時に半角スペースが要るか。英文が隣接する場合のみ True。"""
    if not prev or not nxt:
        return False
    return is_latin(prev[-1]) or is_latin(nxt[0])


def starts_new_block(line: str) -> bool:
    """この行は直前の段落に吸収せず、新しいブロックを始めるべきか。"""
    return bool(
        ATX_RE.match(line)
        or LIST_RE.match(line)
        or BLOCKQUOTE_RE.match(line)
        or TABLE_RE.match(line)
        or HTML_RE.match(line)
        or HR_RE.match(line)
        or INDENT_CODE_RE.match(line)
        or line.startswith(IDEOGRAPHIC_SPACE)
        or line.lstrip().startswith(QUOTE_OPEN)
        or HEADING_RE.match(line.lstrip())
    )


def merge_lines(lines, blank_breaks: bool = True):
    """
    行のリストを受け取り、(kind, text) のリストを返す。
    kind: 'sep' | 'heading' | 'para' | 'list' | 'quote' | 'table'
          | 'code' | 'html' | 'frontmatter'
    """
    blocks = []
    buf = []
    cur_kind = "para"

    def flush():
        nonlocal cur_kind
        if buf:
            blocks.append((cur_kind, "".join(buf)))
            buf.clear()
        cur_kind = "para"

    def start(kind, text):
        nonlocal cur_kind
        flush()
        cur_kind = kind
        buf.append(text)

    lines = [raw.rstrip("\n") for raw in lines]
    i = 0
    n = len(lines)

    # --- YAML フロントマター ---
    if n and lines[0].strip() == "---":
        for j in range(1, n):
            if lines[j].strip() in ("---", "..."):
                blocks.append(("frontmatter", "\n".join(lines[: j + 1])))
                i = j + 1
                break

    open_fence = None  # 閉じ忘れ検出用

    while i < n:
        line = lines[i]

        # --- コードフェンス: 閉じるまで丸ごと退避 ---
        m = FENCE_RE.match(line)
        if m:
            flush()
            marker = m.group(1)[0] * 3
            code = [line]
            i += 1
            closed = False
            while i < n:
                code.append(lines[i])
                if FENCE_RE.match(lines[i]) and lines[i].lstrip().startswith(marker):
                    closed = True
                    i += 1
                    break
                i += 1
            if not closed:
                open_fence = line.strip()
            blocks.append(("code", "\n".join(code)))
            continue

        # --- 空行 ---
        if not line.strip():
            if blank_breaks:
                flush()
            i += 1
            continue

        # --- Setext 見出し (直前の段落の下線) ---
        if SETEXT_RE.match(line) and buf and cur_kind == "para":
            text = "".join(buf)
            buf.clear()
            cur_kind = "para"
            blocks.append(("heading", text + "\n" + line.strip()))
            i += 1
            continue

        # --- 水平線 ---
        if is_separator(line):
            flush()
            blocks.append(("sep", line.strip()))
            i += 1
            continue

        # --- 見出し ---
        if is_heading(line):
            flush()
            blocks.append(("heading", line))
            i += 1
            continue

        # --- インデントコードブロック ---
        if INDENT_CODE_RE.match(line):
            flush()
            code = []
            while i < n and (INDENT_CODE_RE.match(lines[i]) or not lines[i].strip()):
                code.append(lines[i])
                i += 1
            while code and not code[-1].strip():
                code.pop()
            blocks.append(("code", "\n".join(code)))
            continue

        # --- HTML ブロック ---
        if HTML_RE.match(line):
            start("html", line)
            flush()
            i += 1
            continue

        # --- 表 ---
        if TABLE_RE.match(line):
            start("table", line)
            flush()
            i += 1
            continue

        # --- リスト項目 ---
        if LIST_RE.match(line):
            start("list", line)
            i += 1
            continue

        # --- 引用 ---
        if BLOCKQUOTE_RE.match(line):
            if cur_kind == "quote" and buf:
                body = re.sub(r"^\s{0,3}>\s?", "", line)
                prev = buf[-1]
                buf.append((" " if needs_space(prev, body) else "") + body)
            else:
                start("quote", line)
            i += 1
            continue

        # --- 段落の開始位置が明示されている行 ---
        if line.startswith(IDEOGRAPHIC_SPACE) or line.lstrip().startswith(QUOTE_OPEN):
            start("para", line)
            i += 1
            continue

        # --- それ以外は直前のブロックに結合 (折り返しとみなす) ---
        if buf and cur_kind in CONTINUABLE_KINDS:
            prev = buf[-1]
            buf.append((" " if needs_space(prev, line) else "") + line)
        else:
            start("para", line)
        i += 1

    flush()
    if open_fence:
        blocks.append(("__unclosed_fence__", open_fence))
    return blocks


def find_issues(blocks):
    """目視確認すべき箇所を洗い出す。"""
    issues = []
    for i, (kind, text) in enumerate(blocks):
        if kind == "__unclosed_fence__":
            issues.append((i, "コードフェンスが閉じられていない", text))
            continue

        if kind in VERBATIM_KINDS or kind == "code":
            continue

        opens = text.count(QUOTE_OPEN)
        closes = text.count(QUOTE_CLOSE)
        if opens != closes:
            issues.append(
                (i, "カギ括弧の開閉が不一致 (開 %d / 閉 %d)" % (opens, closes), text)
            )

        if text.count("```") % 2 == 1:
            issues.append((i, "段落内にインラインでないバッククォート 3 連", text))

        if kind == "para" and len(text) > 400:
            issues.append((i, "段落が長すぎる可能性 (%d 文字)" % len(text), text))

    return issues


def render(blocks) -> str:
    out = []
    prev_kind = None
    for kind, text in blocks:
        if kind == "__unclosed_fence__":
            continue
        if out and kind == prev_kind and kind in TIGHT_KINDS:
            out.append("\n" + text)
        elif out:
            out.append("\n\n" + text)
        else:
            out.append(text)
        prev_kind = kind
    return "".join(out) + "\n"


def process_file(src: Path, out_dir: Path, write_report: bool, blank_line: str):
    lines = src.read_text(encoding="utf-8").splitlines()
    before = len([l for l in lines if l.strip()])

    if blank_line == "auto":
        blank_breaks = src.suffix.lower() in (".md", ".markdown")
    else:
        blank_breaks = blank_line == "break"

    blocks = merge_lines(lines, blank_breaks=blank_breaks)
    paras = [b for b in blocks if b[0] in CONTINUABLE_KINDS]

    out_path = out_dir / src.name
    out_path.write_text(render(blocks), encoding="utf-8")

    issues = find_issues(blocks)
    if write_report and issues:
        rep = out_dir / (src.stem + "_report.txt")
        with rep.open("w", encoding="utf-8") as f:
            f.write("%s — 目視確認が必要な箇所 %d 件\n" % (src.name, len(issues)))
            f.write("=" * 60 + "\n\n")
            for idx, reason, text in issues:
                f.write("[ブロック %d] %s\n" % (idx, reason))
                f.write(text[:200] + ("…" if len(text) > 200 else "") + "\n\n")

    return {
        "name": src.name,
        "before": before,
        "after": len(paras),
        "issues": len(issues),
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--src",
        default="converted_pdf/split_chapters",
        help="入力ディレクトリ (既定: converted_pdf/split_chapters)",
    )
    ap.add_argument(
        "--out",
        default="converted_pdf/merged_chapters",
        help="出力ディレクトリ (既定: converted_pdf/merged_chapters)",
    )
    ap.add_argument(
        "--ext",
        nargs="+",
        default=[".txt", ".md"],
        help="対象とする拡張子 (既定: .txt .md)",
    )
    ap.add_argument(
        "--blank-line",
        choices=("auto", "ignore", "break"),
        default="auto",
        help="空行の扱い。auto=.md は段落区切り / .txt は無視 (既定: auto)",
    )
    ap.add_argument("--no-report", action="store_true", help="確認用レポートを出力しない")
    args = ap.parse_args()

    src_dir = Path(args.src)
    out_dir = Path(args.out)

    if not src_dir.is_dir():
        sys.exit("入力ディレクトリが見つかりません: %s" % src_dir)

    out_dir.mkdir(parents=True, exist_ok=True)

    exts = {e if e.startswith(".") else "." + e for e in args.ext}
    files = sorted(
        p for p in src_dir.iterdir()
        if p.is_file() and p.suffix.lower() in exts and not p.stem.endswith("_report")
    )
    if not files:
        sys.exit("%s に対象ファイル (%s) がありません" % (src_dir, " ".join(sorted(exts))))

    results = [process_file(f, out_dir, not args.no_report, args.blank_line) for f in files]

    print("%-32s %8s %8s %8s" % ("ファイル", "処理前", "処理後", "要確認"))
    print("-" * 60)
    for r in results:
        print("%-32s %8d %8d %8d" % (r["name"], r["before"], r["after"], r["issues"]))
    print("-" * 60)
    print("出力先: %s/" % out_dir)

    total_issues = sum(r["issues"] for r in results)
    if total_issues:
        print("要確認 %d 件。*_report.txt を参照してください。" % total_issues)


if __name__ == "__main__":
    main()

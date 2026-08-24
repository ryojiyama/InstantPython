#!/usr/bin/env python3
"""
merge_paragraphs.py

source/ 内のマークダウンを読み込み、1文ごとに分断された段落を
本来のまとまりに結合して converted_pdf/ に出力する。

結合ルール（この順で判定）:
  1. `---`            -> セクション区切り。そのまま出力し、段落を打ち切る
  2. 見出し行          -> そのまま出力し、段落を打ち切る
                          (例: `１話　災厄の予言` `３話：前半　悪巧み`)
  3. `　` で始まる行   -> 新しい段落の開始
  4. `「` で始まる行   -> 会話文。独立した段落として扱う
  5. それ以外          -> 直前の段落に結合する

同時に、目視確認が必要そうな箇所を report ファイルに書き出す。

使い方:
    python3 merge_paragraphs.py
    python3 merge_paragraphs.py --src source --out converted_pdf
    python3 merge_paragraphs.py --no-report
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


def is_heading(line: str) -> bool:
    """見出し行かどうか。Markdown の # 見出しも含める。"""
    if line.startswith("#"):
        return True
    return bool(HEADING_RE.match(line))


def is_separator(line: str) -> bool:
    return line.strip() in ("---", "***", "___")


def merge_lines(lines):
    """
    行のリストを受け取り、(kind, text) のリストを返す。
    kind は 'sep' | 'heading' | 'para' のいずれか。
    """
    blocks = []
    buf = []

    def flush():
        if buf:
            blocks.append(("para", "".join(buf)))
            buf.clear()

    for raw in lines:
        line = raw.rstrip("\n")

        if not line.strip():
            continue

        if is_separator(line):
            flush()
            blocks.append(("sep", line.strip()))
            continue

        if is_heading(line):
            flush()
            blocks.append(("heading", line))
            continue

        if line.startswith(IDEOGRAPHIC_SPACE):
            flush()
            buf.append(line)
            continue

        if line.startswith(QUOTE_OPEN):
            flush()
            buf.append(line)
            continue

        # それ以外は直前の段落に結合
        if buf:
            prev = buf[-1]
            # 和文どうしはそのまま連結、英文が絡む場合は半角スペースを挟む
            if needs_space(prev, line):
                buf.append(" " + line)
            else:
                buf.append(line)
        else:
            buf.append(line)

    flush()
    return blocks


def is_latin(ch: str) -> bool:
    return ch.isascii() and (ch.isalnum() or ch in ".,!?\"')")


def needs_space(prev: str, nxt: str) -> bool:
    """連結時に半角スペースが要るか。英文が隣接する場合のみ True。"""
    if not prev or not nxt:
        return False
    return is_latin(prev[-1]) or is_latin(nxt[0])


def find_issues(blocks):
    """目視確認すべき箇所を洗い出す。"""
    issues = []
    for i, (kind, text) in enumerate(blocks):
        if kind != "para":
            continue

        opens = text.count(QUOTE_OPEN)
        closes = text.count(QUOTE_CLOSE)
        if opens != closes:
            issues.append(
                (i, "カギ括弧の開閉が不一致 (開 %d / 閉 %d)" % (opens, closes), text)
            )

        if len(text) > 400:
            issues.append((i, "段落が長すぎる可能性 (%d 文字)" % len(text), text))

    return issues


def render(blocks) -> str:
    out = []
    for kind, text in blocks:
        out.append(text)
    return "\n\n".join(out) + "\n"


def process_file(src: Path, out_dir: Path, write_report: bool):
    lines = src.read_text(encoding="utf-8").splitlines()
    before = len([l for l in lines if l.strip()])

    blocks = merge_lines(lines)
    paras = [b for b in blocks if b[0] == "para"]

    out_path = out_dir / src.name
    out_path.write_text(render(blocks), encoding="utf-8")

    issues = find_issues(blocks)
    if write_report and issues:
        rep = out_dir / (src.stem + "_report.txt")
        with rep.open("w", encoding="utf-8") as f:
            f.write("%s — 目視確認が必要な箇所 %d 件\n" % (src.name, len(issues)))
            f.write("=" * 60 + "\n\n")
            for idx, reason, text in issues:
                f.write("[段落 %d] %s\n" % (idx, reason))
                f.write(text[:200] + ("…" if len(text) > 200 else "") + "\n\n")

    return {
        "name": src.name,
        "before": before,
        "after": len(paras),
        "issues": len(issues),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default="source", help="入力ディレクトリ (既定: source)")
    ap.add_argument("--out", default="converted_pdf", help="出力ディレクトリ (既定: converted_pdf)")
    ap.add_argument("--no-report", action="store_true", help="確認用レポートを出力しない")
    args = ap.parse_args()

    src_dir = Path(args.src)
    out_dir = Path(args.out)

    if not src_dir.is_dir():
        sys.exit("入力ディレクトリが見つかりません: %s" % src_dir)

    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(src_dir.glob("*.md"))
    if not files:
        sys.exit("%s に .md ファイルがありません" % src_dir)

    results = [process_file(f, out_dir, not args.no_report) for f in files]

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

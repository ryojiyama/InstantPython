"""
todo.csv に挙がったセル画像を取り出して、ラベル付けしやすい形にまとめる。

  python make_todo_sheets.py                 # todo.csv を読む
  python make_todo_sheets.py other_todo.csv

出力:
  todo_cells/<帳票>/r03_4ヶ月_pred-○_0.67.png   個別画像(ファイル名に情報を埋め込む)
  todo_sheets/<帳票>.png                        ページ単位のコンタクトシート

コンタクトシートには、そのページで既にラベルが確定しているセルを
「参考」として先頭に並べる。○ と ◎ の区別は濃さの絶対値では決まらず
(ページによって細字が濃かったり太字が薄かったりする)、
同じページ内での相対比較でしか判断できないため。

見出し行(1〜2行目)は列見出しであってデータではないので除外する。
"""

import os
import shutil
import sys

import cv2
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

TODO_CSV = sys.argv[1] if len(sys.argv) > 1 else "todo.csv"
LABEL_CSV = "labels.csv"
CELL_DIR = "cells"
OUT_CELLS = "todo_cells"
OUT_SHEETS = "todo_sheets"

CELL_W, CELL_H = 170, 62
GAP_X, GAP_Y = 10, 26          # コマ間の余白(GAP_Y にキャプションを書く)
COLS = 5
HEADER_ROWS = 2                # 表の見出し行数。この行はデータではない
REF_PER_CLASS = 3

# 日本語が描けるフォントを順に探す(macOS / Linux / Windows)
FONT_CANDIDATES = [
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/AquaKana.ttc",
    "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/meiryo.ttc",
    "C:/Windows/Fonts/msgothic.ttc",
]


def load_font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    print("警告: 日本語フォントが見つかりません。文字化けする可能性があります。")
    return ImageFont.load_default()


FONT = load_font(13)
FONT_BIG = load_font(17)
FONT_MID = load_font(14)


def load_cell(name):
    im = cv2.imread(os.path.join(CELL_DIR, name))
    return None if im is None else cv2.cvtColor(im, cv2.COLOR_BGR2RGB)


def build_sheet(title, items, refs):
    """items / refs: [(キャプション, RGB画像)]"""
    blocks = []
    if refs:
        blocks.append(("参考 — このページの確定済み", refs))
    blocks.append(("要判定", items))

    def n_rows(block):
        return max(1, -(-len(block) // COLS))

    height = 40 + sum(24 + n_rows(b) * (CELL_H + GAP_Y) for _, b in blocks) + 10
    width = COLS * (CELL_W + GAP_X) + 16
    canvas = Image.new("RGB", (width, height), "white")
    dr = ImageDraw.Draw(canvas)
    dr.text((10, 10), title, fill="black", font=FONT_BIG)

    y = 44
    for heading, block in blocks:
        dr.text((10, y), heading, fill=(110, 110, 110), font=FONT_MID)
        y += 24
        for i, (caption, im) in enumerate(block):
            r, c = divmod(i, COLS)
            x = 8 + c * (CELL_W + GAP_X)
            yy = y + r * (CELL_H + GAP_Y)
            if im is not None:
                canvas.paste(Image.fromarray(im).resize((CELL_W, CELL_H)), (x, yy))
            dr.rectangle([x, yy, x + CELL_W, yy + CELL_H], outline=(205, 205, 205))
            dr.text((x + 2, yy + CELL_H + 4), caption, fill=(190, 0, 0), font=FONT)
        y += n_rows(block) * (CELL_H + GAP_Y)
    return canvas


def main():
    todo = pd.read_csv(TODO_CSV, encoding="utf-8-sig")
    before = len(todo)
    todo = todo[todo["行"] > HEADER_ROWS]
    if before != len(todo):
        print(f"見出し行を除外: {before - len(todo)}件 -> 残り {len(todo)}件")

    labels = (pd.read_csv(LABEL_CSV, encoding="utf-8-sig")
              if os.path.exists(LABEL_CSV) else pd.DataFrame())

    for d in (OUT_CELLS, OUT_SHEETS):
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)

    n_copied = 0
    for sheet_name, g in todo.groupby("帳票"):
        sub = os.path.join(OUT_CELLS, str(sheet_name))
        os.makedirs(sub, exist_ok=True)

        items = []
        for _, r in g.sort_values(["行", "列"]).iterrows():
            im = load_cell(r["file"])
            if im is None:
                print("  見つかりません:", r["file"])
                continue
            name = f"r{int(r['行']):02d}_{r['列']}_pred-{r['予測']}_{r['確信度']}.png"
            cv2.imwrite(os.path.join(sub, name),
                        cv2.cvtColor(im, cv2.COLOR_RGB2BGR))
            n_copied += 1
            items.append((f"{int(r['行'])}行 {r['列']} → {r['予測']} ({r['確信度']})", im))

        refs = []
        if len(labels) and "帳票" in labels.columns:
            done = labels[(labels["帳票"] == sheet_name)
                          & labels["label"].notna()
                          & (labels["label"].astype(str).str.strip() != "")]
            for cls, gg in done.groupby("label"):
                if str(cls) == "空欄":
                    continue
                for _, r in gg.sort_values("濃さ").head(REF_PER_CLASS).iterrows():
                    im = load_cell(r["file"])
                    if im is not None:
                        refs.append((f"{cls}   濃さ{r['濃さ']:.0f}", im))

        path = os.path.join(OUT_SHEETS, f"{sheet_name}.png")
        build_sheet(f"{sheet_name}    要判定 {len(items)}件", items, refs).save(path)
        print(f"{sheet_name}: 要判定 {len(items):3d}件 / 参考 {len(refs):2d}件 -> {path}")

    print(f"\n個別画像 {n_copied}枚 -> {OUT_CELLS}/")
    print(f"コンタクトシート -> {OUT_SHEETS}/")
    print("\nシートを見ながら labels.csv の label 列を埋めてください。")
    print("○ と ◎ は「参考」欄の同じページのセルと見比べて相対的に判断すること。")


if __name__ == "__main__":
    main()

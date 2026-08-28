"""
セルのラベル付けをブラウザ上で完結させるツール。

  python make_label_ui.py              # todo.csv からラベリング画面を生成
  python make_label_ui.py all          # 未ラベルのセルすべてを対象にする
  python make_label_ui.py merge ~/Downloads/labels_ui.csv
                                       # ブラウザで出力したCSVを labels.csv に取り込む

生成物: label_ui.html (単体で完結。ブラウザで開くだけ)

セル単体を切り出すと位置の手がかりが失われ、原本で行を数え直すことになる。
そこで判定対象のセルだけでなく「その行を丸ごと」表示し、対象セルを赤枠で囲む。
品番と受入日が一緒に見えるので、原本のどこかが即座に分かる。
"""

from __future__ import annotations

import base64
import glob
import html
import json
import os
import re
import sys
from collections import defaultdict

import cv2
import numpy as np
import pandas as pd

import extract as E

TODO_CSV = "todo.csv"
LABEL_CSV = "labels.csv"
INPUT_DIR = "input"
OUT_HTML = "label_ui.html"

STRIP_WIDTH = 1000        # 行ストリップの表示幅(px)
HEADER_ROWS = 2           # 表の見出し行。データではないので対象外
CELL_RE = re.compile(r"^(?P<tag>.+)_r(?P<row>\d+)_c(?P<col>\d)\.png$")


# ---------------------------------------------------------------- ページの解決

def resolve_pages() -> dict:
    """labels.csv の「帳票」タグ -> (PDFパス, ページ番号) の対応を作る。"""
    pages = {}
    for pdf in sorted(glob.glob(os.path.join(INPUT_DIR, "*.pdf"))):
        stem = re.sub(r"[^0-9A-Za-z_-]", "_",
                      os.path.splitext(os.path.basename(pdf))[0])
        import fitz
        doc = fitz.open(pdf)
        n = len(doc)
        doc.close()
        for p in range(n):
            tag = stem if n == 1 else f"{stem}_p{p + 1:02d}"
            pages[tag] = (pdf, p)
    return pages


# ---------------------------------------------------------------- 画像生成

def row_strip(img, xs, ys, row: int, col: int) -> str:
    """1行分を切り出し、対象セルを赤枠で囲んで base64 PNG を返す。"""
    r = row - 1                                  # 1始まり -> 0始まり
    pad = max(2, (ys[r + 1] - ys[r]) // 6)
    y0 = max(0, ys[r] - pad)
    y1 = min(img.shape[0], ys[r + 1] + pad)
    strip = img[y0:y1, xs[0]:xs[-1]].copy()

    c = E.COL_FIRST_RESULT + (col - 1)
    cv2.rectangle(strip,
                  (xs[c] - xs[0], ys[r] - y0),
                  (xs[c + 1] - xs[0], ys[r + 1] - y0),
                  (0, 0, 255), max(2, strip.shape[0] // 20))

    scale = STRIP_WIDTH / strip.shape[1]
    strip = cv2.resize(strip, (STRIP_WIDTH, max(1, int(strip.shape[0] * scale))))
    ok, buf = cv2.imencode(".png", strip)
    return base64.b64encode(buf).decode() if ok else ""


# ---------------------------------------------------------------- HTML

HTML_TEMPLATE = """<!doctype html>
<meta charset="utf-8">
<title>セルのラベル付け</title>
<style>
 body{font-family:system-ui,-apple-system,"Hiragino Sans",sans-serif;
      margin:0;background:#f6f6f7;color:#222}
 header{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;
        padding:10px 16px;display:flex;gap:16px;align-items:center;z-index:10;
        box-shadow:0 1px 4px rgba(0,0,0,.06)}
 header b{font-size:15px} #prog{font-variant-numeric:tabular-nums}
 button{font:inherit;padding:6px 12px;border:1px solid #bbb;background:#fff;
        border-radius:6px;cursor:pointer}
 button:hover{background:#eef}
 .item{background:#fff;margin:12px 16px;padding:10px 12px;border-radius:8px;
       border:1px solid #e2e2e2}
 .item.done{opacity:.45}
 .meta{font-size:13px;color:#555;margin-bottom:6px}
 .meta .pred{color:#a00}
 .item img{width:100%;display:block;border:1px solid #eee;border-radius:4px}
 .btns{margin-top:8px;display:flex;gap:6px;flex-wrap:wrap}
 .btns button.sel{background:#1a6ed8;color:#fff;border-color:#1a6ed8}
 .btns button.guess{border-color:#1a6ed8}
 .hint{font-size:12px;color:#888;margin-left:auto}
 #dl{background:#1a6ed8;color:#fff;border-color:#1a6ed8}
</style>
<header>
  <b>セルのラベル付け</b>
  <span id="prog"></span>
  <button onclick="fillGuess()">未判定を予測で埋める</button>
  <button id="dl" onclick="save()">CSVを書き出す</button>
  <span class="hint">キー: 1=空欄 2=○ 3=◎ 4=× 5=— 6=在庫なし</span>
</header>
<div id="list"></div>
<script>
const CLASSES = ["空欄","○","◎","×","—","在庫なし"];
const ITEMS = __ITEMS__;
const state = {};
let focused = 0;

function render(){
  const list = document.getElementById("list");
  list.innerHTML = ITEMS.map((it,i)=>`
    <div class="item" id="it${i}">
      <div class="meta">${it.sheet} &nbsp;/&nbsp; ${it.row}行 ${it.col}
        &nbsp; <span class="pred">予測: ${it.pred} (${it.conf})</span></div>
      <img src="data:image/png;base64,${it.img}">
      <div class="btns">${CLASSES.map(c=>
        `<button data-i="${i}" data-c="${c}"
           class="${c===it.pred?'guess':''}" onclick="pick(${i},'${c}')">${c}</button>`
      ).join("")}</div>
    </div>`).join("");
  update();
}
function pick(i,c){ state[i]=c; update(); focus(i+1); }
function fillGuess(){ ITEMS.forEach((it,i)=>{ if(!state[i]) state[i]=it.pred; }); update(); }
function update(){
  ITEMS.forEach((it,i)=>{
    const el = document.getElementById("it"+i);
    el.classList.toggle("done", !!state[i]);
    el.querySelectorAll(".btns button").forEach(b=>
      b.classList.toggle("sel", b.dataset.c===state[i]));
  });
  const n = Object.keys(state).length;
  document.getElementById("prog").textContent = `${n} / ${ITEMS.length} 判定済み`;
}
function focus(i){
  if(i>=ITEMS.length) return;
  focused=i;
  document.getElementById("it"+i).scrollIntoView({block:"center",behavior:"smooth"});
}
document.addEventListener("keydown",e=>{
  const k = parseInt(e.key,10);
  if(k>=1 && k<=6){ pick(focused, CLASSES[k-1]); e.preventDefault(); }
  if(e.key==="ArrowDown"){ focus(focused+1); e.preventDefault(); }
  if(e.key==="ArrowUp"){ focus(Math.max(0,focused-1)); e.preventDefault(); }
});
function save(){
  const rows = [["file","label"]];
  ITEMS.forEach((it,i)=>{ if(state[i]) rows.push([it.file, state[i]]); });
  if(rows.length===1){ alert("判定済みの項目がありません。"); return; }
  const csv = "\\ufeff" + rows.map(r=>r.join(",")).join("\\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv],{type:"text/csv"}));
  a.download = "labels_ui.csv";
  a.click();
}
render();
</script>
"""


def build(mode: str) -> None:
    labels = pd.read_csv(LABEL_CSV, encoding="utf-8-sig")
    labels["label"] = labels["label"].astype(str).str.strip()

    if mode == "all":
        target = labels[~labels["label"].isin(E.CLASSES)][["file"]].copy()
        target["予測"], target["確信度"] = "", ""
    else:
        if not os.path.exists(TODO_CSV):
            raise SystemExit(f"{TODO_CSV} がありません。'all' を指定するか todo.csv を作ってください。")
        target = pd.read_csv(TODO_CSV, encoding="utf-8-sig")

    # 見出し行はデータではないので除外
    parsed = target["file"].map(lambda f: CELL_RE.match(str(f)))
    target = target[parsed.notna()].copy()
    target["tag"] = [m.group("tag") for m in parsed if m]
    target["row"] = [int(m.group("row")) for m in parsed if m]
    target["col"] = [int(m.group("col")) for m in parsed if m]
    before = len(target)
    target = target[target["row"] > HEADER_ROWS]
    if before != len(target):
        print(f"見出し行を除外: {before - len(target)}件")

    pages = resolve_pages()
    clf = (E.SymbolClassifier.load("classifier.pkl")
           if os.path.exists("classifier.pkl") else None)

    items = []
    for tag, g in target.groupby("tag"):
        if tag not in pages:
            print(f"  PDFが見つかりません: {tag} (input/ を確認してください)")
            continue
        pdf, page_no = pages[tag]
        img = E.prepare(pdf, page_no)
        xs, ys, _ = E.detect_grid(img)

        for _, r in g.sort_values(["row", "col"]).iterrows():
            row, col = int(r["row"]), int(r["col"])
            if row >= len(ys):
                continue
            pred, conf = str(r.get("予測", "")), str(r.get("確信度", ""))
            if not pred and clf:
                lab, cf = clf.predict(E.crop(img, xs, ys, row - 1,
                                             E.COL_FIRST_RESULT + col - 1))
                pred, conf = lab, f"{cf:.2f}"
            items.append({
                "file": r["file"], "sheet": tag, "row": row,
                "col": E.TIMINGS[col - 1], "pred": pred or "空欄",
                "conf": conf or "-",
                "img": row_strip(img, xs, ys, row, col),
            })
        print(f"  {tag}: {len(g)}件")

    if not items:
        raise SystemExit("対象がありません。")

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(HTML_TEMPLATE.replace("__ITEMS__", json.dumps(items, ensure_ascii=False)))

    size = os.path.getsize(OUT_HTML) / 1e6
    print(f"\n{OUT_HTML} を作成しました（{len(items)}件 / {size:.1f}MB）")
    print("ブラウザで開いて判定し、「CSVを書き出す」を押してください。")
    print(f"その後: python {os.path.basename(__file__)} merge ~/Downloads/labels_ui.csv")


def merge(csv_path: str) -> None:
    ui = pd.read_csv(csv_path, encoding="utf-8-sig")
    labels = pd.read_csv(LABEL_CSV, encoding="utf-8-sig")

    mapping = {}
    for _, r in ui.iterrows():
        lab = E.normalize_label(r["label"])
        if lab:
            mapping[str(r["file"])] = lab
        else:
            print(f"  解釈できない値なので無視: {r['file']} = {r['label']!r}")

    cur = labels["label"].astype(str).str.strip()
    new = labels["file"].map(mapping)
    changed = int(((new.notna()) & (new != cur)).sum())
    labels["label"] = new.fillna(cur).replace("nan", "")

    bak = LABEL_CSV + ".bak_before_merge"
    labels.to_csv(bak, index=False, encoding="utf-8-sig")
    labels.to_csv(LABEL_CSV, index=False, encoding="utf-8-sig")

    filled = labels[labels["label"].isin(E.CLASSES)]
    print(f"{len(mapping)}件を取り込みました（うち更新 {changed}件）。退避: {bak}")
    print(f"ラベル済み合計: {len(filled)}件 / {len(labels)}件")
    print("内訳:", filled["label"].value_counts().to_dict())
    print("\n次: python extract.py train")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "todo"
    if arg == "merge":
        if len(sys.argv) < 3:
            raise SystemExit("使い方: python make_label_ui.py merge <ダウンロードしたCSV>")
        merge(sys.argv[2])
    else:
        build(arg)

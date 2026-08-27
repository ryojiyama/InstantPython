"""
帳票 No.109 の正解ラベルを labels.csv の label 列に流し込む。

calibrate の直後に一度だけ実行する。
セル画像を目視確認して確定させた内容で、ファイル名の接頭辞(帳票名)には依存しない。

  python extract.py calibrate input/*.pdf
  python apply_truth.py
  python extract.py train

対象PDFがすべて同じ帳票(No.109)のスキャンであることが前提。
別の帳票を追加した場合は、その帳票の分だけ手でラベル付けすること。
"""

import re
import sys

import pandas as pd

# (グリッド行番号) -> {列番号: ラベル}。記載のない列は 空欄。
# 見出し行(1,2)と表外(24-27)は学習から除外するため TRUTH に含めない。
TRUTH = {
    3:  {1: "×", 2: "在庫なし"},
    4:  {1: "×", 2: "在庫なし"},
    5:  {1: "×", 2: "在庫なし"},
    6:  {1: "×"},
    7:  {1: "○"},
    8:  {},
    9:  {1: "—"},
    10: {1: "—"},
    11: {1: "—"},
    12: {1: "—", 2: "○", 3: "○", 4: "○", 5: "○", 6: "◎"},
    13: {1: "—", 2: "○", 3: "○", 4: "○", 5: "○"},
    14: {1: "—", 2: "○", 3: "○", 4: "○"},
    15: {1: "—", 2: "○", 3: "◎"},
    16: {1: "—", 2: "○"},
    17: {1: "—", 2: "○", 3: "×", 4: "在庫なし"},
    18: {1: "—", 2: "○", 3: "×", 4: "在庫なし"},
    19: {1: "—", 2: "×"},
    20: {1: "—"},
    21: {1: "—"},
    22: {1: "—", 2: "○", 3: "○"},
    23: {1: "—"},
}

CSV = sys.argv[1] if len(sys.argv) > 1 else "labels.csv"
CELL_RE = re.compile(r"r(\d+)_c(\d)\.png$")


def label_for(filename: str) -> str:
    m = CELL_RE.search(str(filename))
    if not m:
        return ""
    r, c = int(m.group(1)), int(m.group(2))
    if r not in TRUTH:
        return ""          # 空欄のままにして学習対象から外す
    return TRUTH[r].get(c, "空欄")


def main() -> None:
    df = pd.read_csv(CSV, encoding="utf-8-sig")
    if "file" not in df.columns:
        raise SystemExit(f"{CSV} に file 列がありません。")

    df["label"] = df["file"].map(label_for)
    df.to_csv(CSV, index=False, encoding="utf-8-sig")

    filled = df[df["label"] != ""]
    print(f"{CSV}: 全{len(df)}行中 {len(filled)}行にラベルを設定しました。")
    print("内訳:", filled["label"].value_counts().to_dict())
    if "帳票" in df.columns:
        print("帳票別:", filled.groupby("帳票").size().to_dict())

    # 推定(ヒューリスティックの下書き)との一致率。低くても問題ない。
    if "推定" in df.columns:
        acc = (filled["label"] == filled["推定"]).mean()
        print(f"推定との一致率: {acc:.3f} (推定は下書きなので8〜9割が正常)")


if __name__ == "__main__":
    main()

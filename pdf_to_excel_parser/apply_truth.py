"""
既知の帳票の正解ラベルを labels.csv の label 列に流し込む。

  python extract.py calibrate input/*.pdf
  python apply_truth_v2.py
  python extract.py train

セル画像を目視確認して確定させた内容。labels.csv の「帳票」列の接頭辞で
どの正解表を使うかを決めるので、ファイル名の付き方に依存しない。
ここに定義の無い帳票は label を空のままにする(=学習対象外)。その帳票は
手でラベル付けするか、既存の分類器で予測して確信度の低いセルだけ確認する。
"""

import re
import sys

import pandas as pd

# ---- 帳票 No.109 (2026.08.19版) : mask_test / Scanned_Document_19 -----------
# 21データ行 × 7列。見出し行(1,2)と表外(24-27)は学習対象外。
TRUTH_109 = {
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

# ---- 帳票 No.10 (2018.03.30版) : scanned_document_109 の 1ページ目 ----------
# 40データ行 × 7列。行ピッチが約44pxと従来の1/3で、この様式の学習に必須。
TRUTH_109_P01 = {
    3:  {1: "×", 2: "在庫なし"},
    4:  {1: "×", 2: "在庫なし"},
    5:  {1: "◎", 2: "在庫なし"},
    6:  {1: "◎"},
    7:  {},
    8:  {1: "—", 2: "◎", 3: "在庫なし"},
    9:  {1: "—", 2: "◎", 3: "在庫なし"},
    10: {1: "—", 3: "在庫なし"},
    11: {1: "—"},
    12: {1: "—"},
    13: {1: "—", 2: "○", 3: "○", 4: "○", 5: "在庫なし"},
    14: {1: "—", 2: "○", 3: "○", 4: "○"},
    15: {1: "—", 2: "×"},
    16: {1: "—", 2: "○", 3: "×"},
    17: {1: "—", 2: "○"},
    18: {1: "—"},
    19: {1: "—", 2: "◎", 3: "在庫なし"},
    20: {1: "—", 3: "在庫なし"},
    21: {1: "—"},
    22: {1: "—", 2: "○", 3: "○", 4: "◎"},
    23: {1: "—", 2: "○"},
    24: {1: "—"},
    25: {1: "×", 2: "在庫なし"},
    26: {1: "×", 2: "在庫なし"},
    27: {1: "×", 2: "在庫なし"},
    28: {1: "○", 2: "◎", 3: "在庫なし"},
    29: {1: "○", 2: "◎"},
    30: {1: "◎"},
    31: {1: "◎"},
    32: {},
    33: {1: "—", 2: "○", 3: "×", 4: "在庫なし"},
    34: {1: "—", 2: "○", 3: "在庫なし"},
    35: {1: "—", 2: "○"},
    36: {1: "—", 2: "◎"},
    37: {1: "—", 2: "◎"},
    38: {1: "—"},
    39: {1: "—"},
    40: {1: "—"},
    41: {1: "—", 2: "◎", 3: "在庫なし"},
    42: {1: "—"},
}

# 「帳票」列(またはファイル名)の接頭辞 -> 正解表。長い接頭辞から順に照合する。
TRUTH_BY_SHEET = {
    "scanned_document_109_p01": TRUTH_109_P01,
    "mask_test": TRUTH_109,
    "Scanned_Document_19": TRUTH_109,
}

CSV = sys.argv[1] if len(sys.argv) > 1 else "labels.csv"
CELL_RE = re.compile(r"r(\d+)_c(\d)\.png$")


def truth_for(name: str):
    for prefix in sorted(TRUTH_BY_SHEET, key=len, reverse=True):
        if str(name).startswith(prefix):
            return TRUTH_BY_SHEET[prefix]
    return None


def main() -> None:
    df = pd.read_csv(CSV, encoding="utf-8-sig")
    if "file" not in df.columns:
        raise SystemExit(f"{CSV} に file 列がありません。")

    labels, unknown = [], set()
    for _, row in df.iterrows():
        sheet = row["帳票"] if "帳票" in df.columns else row["file"]
        table = truth_for(sheet)
        m = CELL_RE.search(str(row["file"]))
        if table is None or m is None:
            unknown.add(str(sheet))
            labels.append("")
            continue
        r, c = int(m.group(1)), int(m.group(2))
        labels.append("" if r not in table else table[r].get(c, "空欄"))

    df["label"] = labels
    df.to_csv(CSV, index=False, encoding="utf-8-sig")

    filled = df[df["label"] != ""]
    print(f"{CSV}: 全{len(df)}行中 {len(filled)}行にラベルを設定しました。")
    print("内訳:", filled["label"].value_counts().to_dict())
    if "帳票" in df.columns:
        print("帳票別:", filled.groupby("帳票").size().to_dict())
    if unknown:
        print("\n正解表が未定義のため空欄のまま:")
        for u in sorted(unknown):
            print("  -", u)
        print("  → 手でラベル付けするか、既存の分類器で予測して確信度の低いセルだけ確認してください。")

    if "推定" in df.columns and len(filled):
        acc = (filled["label"] == filled["推定"]).mean()
        print(f"\n推定との一致率: {acc:.3f} (推定は下書きなので8〜9割が正常)")


if __name__ == "__main__":
    main()

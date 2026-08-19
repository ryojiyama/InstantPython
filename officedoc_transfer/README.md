# xlsx_to_docx.py  —  使い方

## ディレクトリ構造

```
officedoc_transfer/
├── xlsx_converter.py   ← このスクリプト
├── README.md         ← このファイル
├── input/            ← 変換したい .xlsx をここに置く
└── output/           ← 変換後の .docx がここに出力される
    └── {元ファイル名}/
        ├── {シート名A}.docx
        └── {シート名B}.docx
```

## 実行方法

```bash
# input/ に .xlsx を入れてから実行
cd ~/Develop/officedoc_transfer
python3 xlsx_converter.py
```

引数は不要。`input/` 内の全 `.xlsx` を一括処理する。

### コマンドについて

Mac では `python` は Python 2 を指す場合があるため、**`python3` を使うこと**。

```bash
# バージョン確認
python3 --version   # Python 3.x.x と出れば OK
```

### 初回のみ：依存ライブラリのインストール

```bash
pip3 install python-docx
```

## 変換仕様

| 要素 | 処理 |
|---|---|
| 画像 | オリジナル品質で埋め込み。A4本文幅を超える場合はアスペクト比を保って縮小 |
| テキストボックス | 編集可能な段落として流し込み。太字・文字色・フォントサイズを保持 |
| 見出し（「１）〜」「【〜】」形式） | Word の Heading 1 スタイルを適用 |
| 空シート / drawing なし | 自動スキップ |
| フッター | 空領域として確保（AF による後付け用） |

## ページ設定

- 用紙：A4 縦
- 余白：上 1.5cm / 下 2.0cm（フッター考慮）/ 左右 1.8cm

## 動作環境

- Python 3.8 以上
- Mac / Windows / Linux 対応
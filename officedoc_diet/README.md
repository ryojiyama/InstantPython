# officedoc_diet

Office文書（現在は Wordファイル `.docx` に対応）に含まれる画像を自動的にリサイズ・強制JPEG化し、ファイルサイズを劇的に（数十MBレベルで）削減するPythonツールです。

## 📁 ディレクトリ構成

```text
officedoc_diet/
├── .gitignore
├── README.md
├── requirements.txt       # 必要なライブラリ (Pillow)
├── src/
│   └── core.py            # 実行プログラム本体
├── input/                 # 圧縮したい元のファイルを置くフォルダ
├── results/               # 圧縮完了後のファイルが出力されるフォルダ
└── venv/                  # Python仮想環境（自動生成）

🛠️ セットアップ手順（初回のみ）
このツールを初めて使うときの準備です。ターミナル（Ghostty等）でこのフォルダ（officedoc_diet）を開き、以下のコマンドを実行します。

1. 仮想環境の作成

Bash
python3 -m venv venv
2. 仮想環境の有効化

Bash
# Mac / Linux (zsh, bash) の場合
source venv/bin/activate

# Windows (PowerShell) の場合
.\venv\Scripts\Activate.ps1
※ターミナルの左端に (venv) と表示されれば成功です。

3. 必要なライブラリのインストール

Bash
pip install -r requirements.txt
🚀 実行手順（普段の使い方）
1. 仮想環境を有効にする
作業を始める前に、必ず仮想環境をオンにしてください。

Bash
source venv/bin/activate
2. ファイルを配置する
圧縮したいWordファイルを large_document.docx という名前にして、input/ フォルダの中に置きます。

3. プログラムを実行する
以下のコマンドでダイエット処理を開始します。

Bash
python src/core.py
4. 結果を確認する
処理が完了すると、results/ フォルダ内に軽量化された slim_document.docx が生成されます。

⚙️ 設定のカスタマイズ
より強く圧縮したい、または画質を少し保ちたい場合は、src/core.py の末尾にある以下の数値を変更してください。

Python
# quality: JPEGの品質 (1〜100、低いほど低画質・高圧縮)
# max_width: 画像の最大横幅px (これ以上のサイズの画像は縮小されます)
diet_docx(input_file, output_file, quality=50, max_width=1024)

---

これを入れておけば、数ヶ月後にツールを使おうとした時でも「どうやって動かすんだっけ…？」と迷うことがなくなります。
import os
import sys
try:
    from pypdf import PdfReader
except ImportError:
    print("pypdfがインストールされていません。'pip install pypdf' を実行してください。")
    sys.exit(1)

def check_pdf_status(folder_path):
    if not os.path.isdir(folder_path):
        print(f"エラー: フォルダ '{folder_path}' が見つかりません。")
        print("💡 スクリプトと同じ場所に 'unlocked' フォルダを作成し、その中にPDFを入れてください。")
        return

    print(f"フォルダ '{folder_path}' 内のPDFをチェックします...\n" + "-"*40)

    pdf_files = [f for f in os.listdir(folder_path) if f.lower().endswith('.pdf')]
    if not pdf_files:
        print("PDFファイルが見つかりませんでした。")
        return

    for filename in pdf_files:
        filepath = os.path.join(folder_path, filename)
        print(f"📄 {filename}")

        try:
            reader = PdfReader(filepath)

            # 1. パスワード保護（暗号化）のチェック
            if reader.is_encrypted:
                print("   ❌ NG: パスワード保護（暗号化）されています。制限を解除する必要があります。")
                print("-" * 40)
                continue

            # 2. テキスト抽出のチェック（最初の3ページをサンプリング）
            has_text = False
            num_pages = min(len(reader.pages), 3)

            for i in range(num_pages):
                text = reader.pages[i].extract_text()
                # 抽出できたテキストが一定文字数以上ならテキストベースと判定
                if text and len(text.strip()) > 50:
                    has_text = True
                    break

            if has_text:
                print("   ✅ OK: テキスト抽出可能で、制限もありません。")
            else:
                print("   ⚠️ 警告: テキストが抽出できませんでした。スキャンされた画像PDFの可能性があります。")

        except Exception as e:
            print(f"   ❌ エラー: ファイルの読み込みに失敗しました ({e})")

        print("-" * 40)

if __name__ == "__main__":
    # 実行しているスクリプトファイル自身のディレクトリの絶対パスを取得
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # スクリプトと同じ階層にある 'unlocked' フォルダのパスを作成
    target_folder = os.path.join(base_dir, "unlocked")

    # (オプション) もしコマンドライン引数で別のフォルダが指定された場合はそちらを優先する
    if len(sys.argv) > 1:
        target_folder = sys.argv[1]

    check_pdf_status(target_folder)

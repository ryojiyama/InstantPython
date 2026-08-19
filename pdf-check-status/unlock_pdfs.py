from pathlib import Path
import pikepdf

def unlock_pdfs():
    # ディレクトリの設定
    current_dir = Path(__file__).parent
    source_dir = current_dir / "source"
    output_dir = current_dir / "unlocked"

    # 出力先フォルダを作成（存在しない場合のみ）
    output_dir.mkdir(exist_ok=True)

    # sourceディレクトリ内のPDFファイルを取得
    pdf_files = list(source_dir.glob("*.pdf"))

    if not pdf_files:
        print("sourceディレクトリ内にPDFファイルが見つかりません。")
        return

    print(f"処理を開始します。対象ファイル数: {len(pdf_files)}件\n")

    for pdf_path in pdf_files:
        try:
            # PDFを開く（権限パスワードのみであれば自動で突破して開ける）
            with pikepdf.Pdf.open(pdf_path) as pdf:
                if pdf.is_encrypted:
                    print(f"🔓 ロック解除中: {pdf_path.name}")
                    output_path = output_dir / pdf_path.name

                    # 別名で保存し直すことで権限パスワードが消去される
                    pdf.save(output_path)
                    print(f"  -> 保存完了: {output_path.relative_to(current_dir)}")
                else:
                    print(f"⏩ スキップ (ロックなし): {pdf_path.name}")

        except pikepdf.PasswordError:
            # 開くこと自体にパスワードが必要（閲覧パスワード）な場合の例外処理
            print(f"❌ エラー: {pdf_path.name} は「閲覧パスワード」がかかっているため解除できません。")
        except Exception as e:
            print(f"⚠️ 予期せぬエラー ({pdf_path.name}): {e}")

if __name__ == "__main__":
    unlock_pdfs()

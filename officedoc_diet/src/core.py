import os
import zipfile
import shutil
import tempfile
from PIL import Image
from docx import Document

def diet_docx_dynamic(input_path, output_path, quality=70, dpi=150, default_max_width=1280):
    """
    Wordファイル内の画像（JPG/PNG/GIF）をドキュメント内での表示サイズに合わせて動的にリサイズし、
    すべての中身をJPEG形式で強制上書きすることで、ファイル容量を極限まで削減します。
    """
    # パスを絶対パス化してクラッシュを防止
    input_path = os.path.abspath(input_path)
    output_path = os.path.abspath(output_path)

    # ---------------------------------------------------------
    # ステップ1: ドキュメント内の「最大表示サイズ」を調査
    # ---------------------------------------------------------
    max_widths_in_inches = {}

    try:
        doc = Document(input_path)
        for shape in doc.inline_shapes:
            try:
                blip = shape._inline.graphic.graphicData.pic.blipFill.blip
                rId = blip.embed
                target_part = doc.part.rels[rId].target_part
                file_name = os.path.basename(target_part.partname)

                width_inches = shape.width.inches

                if file_name in max_widths_in_inches:
                    max_widths_in_inches[file_name] = max(max_widths_in_inches[file_name], width_inches)
                else:
                    max_widths_in_inches[file_name] = width_inches
            except Exception:
                continue
    except Exception as e:
        print(f"  警告: python-docxによる解析に失敗しました。デフォルト値で一括処理します。({e})")

    # ---------------------------------------------------------
    # ステップ2: 画像の展開と個別最適化リサイズ ＆ 強制JPEG変換
    # ---------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp_dir:

        # ZIP解凍ブロック
        with zipfile.ZipFile(input_path, 'r') as zip_ref:
            zip_ref.extractall(tmp_dir)

        media_dir = os.path.join(tmp_dir, "word", "media")

        if os.path.exists(media_dir):
            for file_name in os.listdir(media_dir):
                file_path = os.path.join(media_dir, file_name)

                ext = os.path.splitext(file_name)[1].lower()
                # 対象拡張子に '.gif' を追加
                if ext in ['.jpg', '.jpeg', '.png', '.gif']:
                    try:
                        if file_name in max_widths_in_inches:
                            calc_width = int(max_widths_in_inches[file_name] * dpi)
                            max_width = max(calc_width, 200)
                            log_mode = f"動的計算 ({max_width}px)"
                        else:
                            max_width = default_max_width
                            log_mode = f"標準一律 ({max_width}px)"

                        with Image.open(file_path) as img:
                            # 変数の再代入を防ぐため、加工用変数を別名で用意
                            processed_img = img

                            # 1. 計算された個別の上限に基づくリサイズ
                            if img.width > max_width:
                                ratio = max_width / float(img.width)
                                new_height = int(float(img.height) * float(ratio))
                                processed_img = img.resize((max_width, new_height), Image.Resampling.LANCZOS)
                                status = f"➔ 縮小 ({processed_img.width}x{processed_img.height})"
                            else:
                                status = "➔ サイズ維持"

                            # 2. 強制JPEG化のための背景処理（PNGの透過や、GIFの透明色対策を強化）
                            # 透過情報があるRGBA/LA、または透明色設定のあるパレットモード(P)の場合
                            if processed_img.mode in ('RGBA', 'LA') or (processed_img.mode == 'P' and 'transparency' in processed_img.info):
                                processed_img = processed_img.convert('RGBA')
                                background = Image.new("RGB", processed_img.size, (255, 255, 255))
                                # アルファチャンネルをマスクにして白背景の上に合成
                                background.paste(processed_img, (0, 0), processed_img.split()[-1])
                                processed_img = background
                            else:
                                # 透過がない場合は単純にRGBに変換
                                processed_img = processed_img.convert('RGB')

                            # 3. 拡張子が何であれ、中身を「JPEG」として強制上書き保存（裏技）
                            processed_img.save(file_path, format='JPEG', quality=quality)
                            print(f"  [強力JPEG変換] {file_name} : {log_mode} {status}")

                    except Exception as e:
                        print(f"  [エラー] {file_name} の処理に失敗: {e}")

        # ---------------------------------------------------------
        # ステップ3: 再度ZIP圧縮してDocxファイルを作成
        # ---------------------------------------------------------
        out_dir = os.path.dirname(output_path)
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)

        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zip_out:
            for root, dirs, files in os.walk(tmp_dir):
                for file in files:
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, tmp_dir)
                    zip_out.write(full_path, rel_path)


# --- 一括実行制御システム ---
if __name__ == "__main__":
    base_dir = os.getcwd()
    input_dir = os.path.join(base_dir, "input")
    output_dir = os.path.join(base_dir, "results")

    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    # inputフォルダ内の .docx ファイルをリストアップ（一時ファイルは除外）
    word_files = [
        f for f in os.listdir(input_dir)
        if f.lower().endswith('.docx') and not f.startswith('~$')
    ]

    print(f"=== Wordファイル一括ダイエットシステム (JPG/PNG/GIF全対応版) ===")
    print(f"監視フォルダ: {input_dir}")
    print(f"出力フォルダ: {output_dir}\n")

    if not word_files:
        print("【お知らせ】")
        print("input フォルダの中に .docx ファイルが見つかりませんでした。")
        print("軽量化したいファイルを input フォルダに入れてから、もう一度実行してください。")
    else:
        print(f"発見したファイル数: {len(word_files)} 件")

        for index, file_name in enumerate(word_files, 1):
            input_file_path = os.path.join(input_dir, file_name)

            name_without_ext, ext = os.path.splitext(file_name)
            output_file_name = f"{name_without_ext}_slim{ext}"
            output_file_path = os.path.join(output_dir, output_file_name)

            print(f"\n[{index}/{len(word_files)}] 処理中: {file_name}")
            try:
                diet_docx_dynamic(input_file_path, output_file_path, quality=70, dpi=150)
                print(f"  ➔ 保存完了: {output_file_name}")
            except Exception as e:
                print(f"  ➔ [致命的エラー] ファイルの処理に失敗しました: {e}")

        print("\n🎉 すべての処理が終了しました！")

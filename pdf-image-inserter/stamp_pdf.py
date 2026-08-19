#!/usr/bin/env python3
import fitz  # PyMuPDF
import os
import random
from pathlib import Path
from PIL import Image
import io

def mm_to_points(mm):
    """ミリメートルをポイントに変換 (1mm = 2.83465 points)"""
    return mm * 2.83465

def add_stamp_to_pdf(pdf_path, stamp_image_path, output_path):
    """PDFの1ページ目にハンコ画像を追加"""

    # PDFを開く
    doc = fitz.open(pdf_path)
    page = doc[0]  # 1ページ目

    # ページサイズを取得
    page_rect = page.rect
    page_width = page_rect.width
    page_height = page_rect.height

    # ランダムなずれを追加（±3mm、±3度）
    random_offset_x = mm_to_points(random.uniform(-3, 3))
    random_offset_y = mm_to_points(random.uniform(-3, 3))
    random_rotation = random.uniform(-3, 3)

    # PILで画像を開いて回転
    pil_img = Image.open(stamp_image_path)

    # 透明度を保持して回転（expand=Trueで画像サイズを調整）
    rotated_img = pil_img.rotate(random_rotation, expand=True, resample=Image.BICUBIC)

    # 回転した画像をバイトデータに変換
    img_bytes = io.BytesIO()
    rotated_img.save(img_bytes, format='PNG')
    img_bytes.seek(0)

    # 画像のアスペクト比を取得
    img_width, img_height = rotated_img.size
    img_aspect_ratio = img_width / img_height

    # ハンコのサイズ設定（高さ20mm）
    stamp_height = mm_to_points(20)
    stamp_width = stamp_height * img_aspect_ratio

    # 基準位置：左下から25mm×25mm（画像の左下を基準）
    base_x = mm_to_points(25)
    base_y = page_height - mm_to_points(25)  # PDFは上が原点なので変換

    # 最終的な配置位置（左下基準）
    final_x = base_x + random_offset_x
    final_y = base_y + random_offset_y

    # 画像を配置する矩形を定義（左下基準なので y - height）
    image_rect = fitz.Rect(
        final_x,
        final_y - stamp_height,
        final_x + stamp_width,
        final_y
    )

    # 回転済み画像を挿入（rotateパラメータは使わない）
    page.insert_image(image_rect, stream=img_bytes.getvalue())

    # 保存
    doc.save(output_path)
    doc.close()

def main():
    # 現在のディレクトリ（実行ディレクトリ）
    current_dir = Path.cwd()

    # ハンコ画像のパス
    stamp_image = current_dir.parent / "Stampimage" / "qcm_yama.png"

    if not stamp_image.exists():
        print(f"❌ ハンコ画像が見つかりません: {stamp_image}")
        return

    # 出力先フォルダを作成
    output_dir = current_dir / "Stamped"
    output_dir.mkdir(exist_ok=True)  # フォルダが無ければ作成

    # 現在のディレクトリ内の全PDFファイルを取得
    pdf_files = list(current_dir.glob("*.pdf"))

    # _Stamped.pdf で終わるファイルは除外
    pdf_files = [f for f in pdf_files if not f.stem.endswith("_Stamped")]

    if not pdf_files:
        print("❌ 処理対象のPDFファイルが見つかりません")
        return

    print(f"📁 処理対象: {len(pdf_files)} ファイル")
    print(f"🖼️  ハンコ画像: {stamp_image.name}")
    print(f"📂 出力先: {output_dir}\n")

    # 各PDFを処理
    for pdf_file in pdf_files:
        output_file = output_dir / f"{pdf_file.stem}_Stamped.pdf"  # Stampedフォルダに出力

        try:
            add_stamp_to_pdf(str(pdf_file), str(stamp_image), str(output_file))
            print(f"✅ {pdf_file.name} → Stamped/{output_file.name}")
        except Exception as e:
            print(f"❌ {pdf_file.name} の処理中にエラー: {e}")

    print("\n🎉 処理完了！")

if __name__ == "__main__":
    main()

import os
from pdfminer.high_level import extract_text
from pdfminer.layout import LAParams

# パスの設定
input_pdf_path = os.path.join('source', 'N6472DL.pdf')
output_txt_path = os.path.join('converted_pdf', 'output.txt')

try:
    print("PDFを解析中...（数十秒かかる場合があります）")

    # 縦書きを正しく検出するためのレイアウトパラメータを設定
    laparams = LAParams(detect_vertical=True)

    # テキストの抽出
    raw_text = extract_text(input_pdf_path, laparams=laparams)

    # 抽出したテキストを保存
    with open(output_txt_path, 'w', encoding='utf-8') as f:
        f.write(raw_text)

    print(f"正常に変換が完了しました。出力先: {output_txt_path}")

except FileNotFoundError:
    print(f"エラー: {input_pdf_path} が見つかりません。")
except Exception as e:
    print(f"予期せぬエラーが発生しました: {e}")

import os
import re

# パスの設定
input_txt_path = os.path.join('converted_pdf', 'output.txt')
output_txt_path = os.path.join('converted_pdf', 'formatted_output.txt')

def clean_text(text):
    # 余分な改行をすべて削除して1行に繋げる
    text = re.sub(r'[\r\n]+', '', text)

    # 句点（。！？）の後に改行を2つ追加して段落を作る
    text = re.sub(r'([。！？])', r'\1\n\n', text)

    # 閉じ括弧（」』】）の後に改行を追加
    text = re.sub(r'([」』】])', r'\1\n', text)

    # 連続する改行を最大2つに調整
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()

try:
    print(f"テキストを読み込んでいます: {input_txt_path}")
    with open(input_txt_path, 'r', encoding='utf-8') as f:
        raw_text = f.read()

    print("テキストを整形中...")
    cleaned_text = clean_text(raw_text)

    # ＝＝＝ もし「第一章（２話の手前）」まで抽出したい場合は、以下の3行のコメント記号（#）を外してください ＝＝＝
    # chapter1_match = re.search(r'(.*?)(?=２話)', cleaned_text, re.DOTALL)
    # if chapter1_match:
    #     cleaned_text = chapter1_match.group(1)

    # 整形したテキストを保存
    with open(output_txt_path, 'w', encoding='utf-8') as f:
        f.write(cleaned_text)

    print(f"正常に整形が完了しました。出力先: {output_txt_path}")

except FileNotFoundError:
    print(f"エラー: {input_txt_path} が見つかりません。抽出済みのテキストが存在するか確認してください。")
except Exception as e:
    print(f"予期せぬエラーが発生しました: {e}")

import re
import os

# 読み込むファイル名と出力先フォルダ
input_file = 'formatted_output.txt'
output_dir = 'split_chapters'

os.makedirs(output_dir, exist_ok=True)

with open(input_file, 'r', encoding='utf-8') as f:
    text = f.read()

# 改ページ記号(任意) + 全角/半角数字 +「話」の直前でテキストを分割する正規表現
# (?= ... ) は「このパターンの直前」を意味する先読みアサーションです
pattern = r'(?=\x0c?[０-９0-9]+話)'
chapters = re.split(pattern, text)

chapter_count = 1
for chapter in chapters:
    # 空白だけの要素はスキップ
    if not chapter.strip():
        continue

    # 各章の1行目を取得してファイル名の一部にする（記号などは削除）
    first_line = chapter.strip().split('\n')[0]
    safe_title = re.sub(r'[\\/*?:"<>|\x0c]', "", first_line)[:20] # ファイル名に使えない文字を削除し20文字に制限

    output_filename = os.path.join(output_dir, f'{chapter_count:02d}_{safe_title}.txt')

    with open(output_filename, 'w', encoding='utf-8') as out_f:
        out_f.write(chapter)

    print(f'保存しました: {output_filename}')
    chapter_count += 1

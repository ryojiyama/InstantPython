import csv

# ==========================================
# 設定
# ==========================================
INPUT_CSV = 'practice_data.csv'      # 読み込む練習用CSV
OUTPUT_CSV = 'pure_product_data.csv' # 出力する純粋な商品マスタCSV
ENCODING = 'cp932'

# 除外するキーワードのリスト
# 列名に以下のいずれかが含まれている場合、その列をごっそり削除します
EXCLUDE_KEYWORDS = [
    '(標準手配先',
    '(標準受入ロケーション',
    '(標準使用ロケーション',
    '(標準完成ロケーション',
    '(製造責任者',
    '(生産管理担当',
    'ログインユーザー'
]

def create_pure_product_csv():
    try:
        with open(INPUT_CSV, mode='r', encoding=ENCODING) as infile, \
             open(OUTPUT_CSV, mode='w', encoding=ENCODING, newline='') as outfile:

            reader = csv.reader(infile)
            writer = csv.writer(outfile)

            # 1行目（ヘッダー）を読み込む
            headers = next(reader)

            # 残すべき列のインデックス（番号）を特定する
            keep_indices = []
            keep_headers = []

            for i, header in enumerate(headers):
                # EXCLUDE_KEYWORDS のいずれも含まれていない列だけを残す
                if not any(keyword in header for keyword in EXCLUDE_KEYWORDS):
                    keep_indices.append(i)
                    keep_headers.append(header)

            # 絞り込んだヘッダーを書き込む
            writer.writerow(keep_headers)

            # 2行目以降のデータも同様に絞り込んで書き込む
            for row in reader:
                filtered_row = [row[i] for i in keep_indices]
                writer.writerow(filtered_row)

        print(f"✅ 成功: 結合されていた関連マスタを除外し、純粋な商品属性だけに絞り込みました。")
        print(f"（元のカラム数: {len(headers)}列 ➔ 絞り込み後: {len(keep_headers)}列）")
        print(f"ファイル '{OUTPUT_CSV}' を確認してください。")

    except Exception as e:
        print(f"❌ エラーが発生しました: {e}")

if __name__ == "__main__":
    create_pure_product_csv()

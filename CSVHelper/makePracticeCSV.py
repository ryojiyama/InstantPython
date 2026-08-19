import csv

# ==========================================
# 設定（ご自身の環境に合わせて書き換えてください）
# ==========================================
INPUT_CSV = 'clean_product_master.csv'  # 読み込む3万行の元CSVファイル名
OUTPUT_CSV = 'clean_product_master_sample.csv' # 出力する5行の練習用CSVファイル名
ROW_COUNT = 5                    # 抽出したいデータ行数（ヘッダー行は含めず）
ENCODING = 'cp932'               # エクセルで作ったCSV等で文字化けする場合は 'cp932' に変更

def create_sample_csv():
    try:
        # 元ファイルを開き、新しいファイルを作成する
        with open(INPUT_CSV, mode='r', encoding=ENCODING) as infile, \
             open(OUTPUT_CSV, mode='w', encoding=ENCODING, newline='') as outfile:

            reader = csv.reader(infile)
            writer = csv.writer(outfile)

            # 1行ずつ読み込み、新しいファイルに書き込む
            for i, row in enumerate(reader):
                # i=0 はヘッダー行。指定した行数に達したらループを抜ける
                if i > ROW_COUNT:
                    break
                writer.writerow(row)

        print(f"✅ 成功: '{OUTPUT_CSV}' にヘッダーと{ROW_COUNT}行のデータを出力しました。")

    except FileNotFoundError:
        print(f"❌ エラー: '{INPUT_CSV}' が見つかりません。ファイル名と保存場所を確認してください。")
    except Exception as e:
        print(f"❌ 予期せぬエラーが発生しました: {e}")

if __name__ == "__main__":
    create_sample_csv()

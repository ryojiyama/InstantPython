import pandas as pd

def remove_empty_columns():
    INPUT_CSV = 'pure_product_data.csv'   # 137列に絞り込んだCSV
    OUTPUT_CSV = 'clean_product_data.csv' # 空列を削除した最終出力CSV
    ENCODING = 'cp932'                    # Windows環境・Excel向けの文字コード

    try:
        # 1. CSVファイルを読み込んでデータフレーム（表形式）にする
        print(f"'{INPUT_CSV}' を読み込んでいます...")
        df = pd.read_csv(INPUT_CSV, encoding=ENCODING)
        original_col_count = len(df.columns)

        # 2. 【重要】すべての行が空（NaN）である列を一括で削除する
        # axis=1 は「列」方向を指定、how='all' は「すべて空なら」を指定しています
        df_cleaned = df.dropna(axis=1, how='all')
        cleaned_col_count = len(df_cleaned.columns)

        # 3. 綺麗になったデータを新しいCSVファイルとして出力（保存）する
        # index=False で行番号がCSVに書き込まれるのを防ぎます
        df_cleaned.to_csv(OUTPUT_CSV, index=False, encoding=ENCODING)

        # 結果のレポート
        dropped_count = original_col_count - cleaned_col_count
        print("✅ 処理が完了しました！")
        print(f"・元のカラム数: {original_col_count}列")
        print(f"・削除した空の列: {dropped_count}列")
        print(f"・最終カラム数: {cleaned_col_count}列")
        print(f"'{OUTPUT_CSV}' に保存されました。")

    except FileNotFoundError:
        print(f"❌ エラー: '{INPUT_CSV}' が見つかりません。")
    except Exception as e:
        print(f"❌ 予期せぬエラーが発生しました: {e}")

if __name__ == "__main__":
    remove_empty_columns()

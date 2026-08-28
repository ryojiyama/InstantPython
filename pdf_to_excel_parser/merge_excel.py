import os
import re
import glob
import pandas as pd

def merge_and_deduplicate_excel_files(
    folder_path="output",
    file_pattern="extracted_Vol*.xlsx",
    output_filename="combined_Vol01_to_Vol10_unique.xlsx",
    key_column="キー"
):
    # 自然順（Vol01 -> Vol02 ... -> Vol10）でソート
    def natural_sort_key(s):
        return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s)]

    # 対象ファイル一覧の取得
    all_files = glob.glob(os.path.join(folder_path, file_pattern))
    # 出力ファイル自体を除外
    target_files = [f for f in all_files if os.path.basename(f) != output_filename]
    target_files = sorted(target_files, key=natural_sort_key)

    if not target_files:
        print("結合対象のファイルが見つかりませんでした。フォルダパスやファイル名を確認してください。")
        return None

    print(f"▼ 以下の順序で {len(target_files)} 件のファイルを結合します:")
    for idx, filepath in enumerate(target_files, start=1):
        print(f"  [{idx:02d}] {os.path.basename(filepath)}")

    # 各ファイルを順に読み込んで結合
    df_list = []
    for filepath in target_files:
        df = pd.read_excel(filepath)
        df_list.append(df)

    combined_df = pd.concat(df_list, ignore_index=True)
    total_rows = len(combined_df)

    # ----------------------------------------------------
    # 「キー」列で重複探索し、最初（一番上）を残して2行目以降を削除
    # ----------------------------------------------------
    if key_column in combined_df.columns:
        dedup_df = combined_df.drop_duplicates(subset=[key_column], keep='first')
        removed_count = total_rows - len(dedup_df)
        print("\n----------------------------------------")
        print(f"▼ 重複削除の実行結果:")
        print(f"  結合直後の行数 : {total_rows:,} 行")
        print(f"  削除された行数 : {removed_count:,} 行")
        print(f"  最終的な行数   : {len(dedup_df):,} 行")
        print("----------------------------------------")
    else:
        print(f"\n※警告: 列 '{key_column}' が見つかりませんでした。重複削除をスキップします。")
        dedup_df = combined_df

    # 保存
    output_path = os.path.join(folder_path, output_filename)
    dedup_df.to_excel(output_path, index=False)
    print(f"出力ファイル: {output_path}\n")

    return dedup_df

if __name__ == "__main__":
    merge_and_deduplicate_excel_files()

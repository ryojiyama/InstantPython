import pandas as pd
import time

# ==========================================
# 設定
# ==========================================
INPUT_CSV = 'original_data.csv'       # 読み込む3万行の元ファイル名に変更してください
OUTPUT_CSV = 'clean_product_master.csv' # 出力する完成形のファイル名
ENCODING = 'cp932'                      # Windows環境/Excel向けの文字コード

# 除外するキーワード（取引先、部門、担当者などの関連マスタ）
EXCLUDE_KEYWORDS = [
    '(標準手配先',
    '(標準受入ロケーション',
    '(標準使用ロケーション',
    '(標準完成ロケーション',
    '(製造責任者',
    '(生産管理担当',
    'ログインユーザー'
]

def clean_master_data():
    print(f"🚀 処理を開始します。'{INPUT_CSV}' を読み込み中...")
    start_time = time.time()

    try:
        # 1. データの読み込み
        # low_memory=False: 100MB規模のファイルを安定して読み込むための設定
        df = pd.read_csv(INPUT_CSV, encoding=ENCODING, low_memory=False)
        original_col_count = len(df.columns)
        original_row_count = len(df)

        # 2. キーワードによる列の除外（関連マスタの切り離し）
        # EXCLUDE_KEYWORDSが含まれない列だけを残す
        keep_cols = [col for col in df.columns if not any(kw in col for kw in EXCLUDE_KEYWORDS)]
        df_filtered = df[keep_cols]
        filtered_col_count = len(df_filtered.columns)

        # 3. 完全に空（NaN）の列を削除
        df_cleaned = df_filtered.dropna(axis=1, how='all')
        final_col_count = len(df_cleaned.columns)

        # 4. CSVとして保存
        # index=False で不要な行番号出力を防ぐ
        df_cleaned.to_csv(OUTPUT_CSV, index=False, encoding=ENCODING)

        elapsed_time = time.time() - start_time

        # 結果レポート
        print("\n✅ マスタのクレンジングが完了しました！")
        print(f"⏱️ 処理時間: {elapsed_time:.1f} 秒")
        print(f"📊 処理件数: {original_row_count:,} 行")
        print("--- カラム数の推移 ---")
        print(f"① 元データ          : {original_col_count} 列")
        print(f"② 関連マスタ除外後 : {filtered_col_count} 列")
        print(f"③ 空カラム削除後    : {final_col_count} 列 (最終出力)")
        print(f"\n📂 クリーンなデータを '{OUTPUT_CSV}' に保存しました。")

    except FileNotFoundError:
        print(f"❌ エラー: '{INPUT_CSV}' が見つかりません。ファイル名を確認してください。")
    except Exception as e:
        print(f"❌ 予期せぬエラーが発生しました: {e}")

if __name__ == "__main__":
    clean_master_data()

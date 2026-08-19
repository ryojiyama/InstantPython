#!/bin/bash

# ログディレクトリとファイル
LOG_DIR="$HOME/Develop/InstantPython/pdf-image-inserter/log"
LOG_FILE="$LOG_DIR/log-$(date '+%Y%m%d').txt"

# ログディレクトリを作成（存在しない場合）
mkdir -p "$LOG_DIR"

echo "========================================" >> "$LOG_FILE"
echo "$(date '+%Y-%m-%d %H:%M:%S') 処理開始" >> "$LOG_FILE"

# パス設定
NAS_BASE="/Volumes/帳票保存"
LOCAL_WORK="$HOME/Develop/InstantPython/pdf-image-inserter/OriginPDF"
STAMP_SCRIPT="$HOME/Develop/InstantPython/pdf-image-inserter/stamp_pdf.py"
VENV="$HOME/Develop/InstantPython/pdf-image-inserter/.venv/bin/activate"

# NASがマウントされているか確認
if [ ! -d "$NAS_BASE" ]; then
    echo "ℹ️  NASがマウントされていません。処理をスキップします。" >> "$LOG_FILE"
    echo "$(date '+%Y-%m-%d %H:%M:%S') 処理スキップ（NAS未接続）" >> "$LOG_FILE"
    echo "" >> "$LOG_FILE"
    exit 0
fi

# 作業ディレクトリをクリーンアップ
rm -rf "$LOCAL_WORK"/*.pdf
rm -rf "$LOCAL_WORK"/Stamped/*.pdf

# 処理対象フォルダ
FOLDERS=("配達" "出荷")

for FOLDER in "${FOLDERS[@]}"; do
    NAS_FOLDER="$NAS_BASE/$FOLDER"

    if [ ! -d "$NAS_FOLDER" ]; then
        echo "⚠️  フォルダが見つかりません: $NAS_FOLDER" >> "$LOG_FILE"
        continue
    fi

    echo "📁 $FOLDER フォルダを処理中..." >> "$LOG_FILE"

    # 未処理ファイル（*Stamped.pdfでないもの）をローカルにコピー
    COUNT=0
    for PDF in "$NAS_FOLDER"/*.pdf; do
        if [ -f "$PDF" ]; then
            BASENAME=$(basename "$PDF")
            # _Stamped.pdfで終わらないファイルのみ
            if [[ "$BASENAME" == *.pdf ]] && [[ "$BASENAME" != *_Stamped.pdf ]]; then
                cp "$PDF" "$LOCAL_WORK/"
                echo "  📥 コピー: $BASENAME" >> "$LOG_FILE"
                COUNT=$((COUNT + 1))
            fi
        fi
    done

    if [ $COUNT -eq 0 ]; then
        echo "  ℹ️  未処理ファイルなし" >> "$LOG_FILE"
        continue
    fi

    # ハンコ処理実行
    cd "$LOCAL_WORK"
    source "$VENV"
    python3 "$STAMP_SCRIPT" >> "$LOG_FILE" 2>&1

    # 処理済みファイルをNASに戻す
    if [ -d "$LOCAL_WORK/Stamped" ]; then
        for STAMPED_PDF in "$LOCAL_WORK/Stamped"/*_Stamped.pdf; do
            if [ -f "$STAMPED_PDF" ]; then
                cp "$STAMPED_PDF" "$NAS_FOLDER/"
                BASENAME=$(basename "$STAMPED_PDF")
                echo "  📤 転送完了: $BASENAME" >> "$LOG_FILE"
            fi
        done
    fi

    # ローカルファイルを削除
    rm -f "$LOCAL_WORK"/*.pdf
    rm -rf "$LOCAL_WORK"/Stamped/*.pdf

    echo "  ✅ $FOLDER 処理完了" >> "$LOG_FILE"
done

echo "$(date '+%Y-%m-%d %H:%M:%S') 全処理完了" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

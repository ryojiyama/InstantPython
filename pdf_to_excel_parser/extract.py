"""
使い捨て式防じんマスク 定期性能確認 帳票 抽出スクリプト
(案B: 格子確定 + 記号分類。ページ全体をVLMに投げない設計)

実物PDFの解析結果に基づく設計:
  - テキストレイヤーなし / 埋め込みJPEG 4676x3306 @400dpi / /Rotate 270
  - 表本体は「印字」であり手書きではない
  - 保管期間7列は等幅(誤差1px)、行は等ピッチ(約128px) の剛体グリッド
  - 濃淡が意味を持つ(濃い◎=今回追加分 / 淡い○×=これまでの結果)
  - 受入日末尾の赤●=KNH社製KF25g使用品

使い方:
  python extract.py calibrate input/mask_test.pdf
      → output/grid_check.png を目視確認(線が実際の罫線に乗っているか)
      → cells/ の画像を見ながら labels.csv の label 列を埋める
  python extract.py train
  python extract.py run

依存:
  pip install pymupdf pillow numpy opencv-python pandas openpyxl scikit-learn
  pip install ocrmac        # macOS Vision Framework (左2列のOCR)
"""

from __future__ import annotations

import os
import re
import sys
import glob
import pickle
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import cv2
import numpy as np
import pandas as pd
import fitz  # PyMuPDF

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("extract")

# ---------------------------------------------------------------- 帳票定義

INPUT_DIR, OUTPUT_DIR = "input", "output"
CELL_DIR, MODEL_PATH, LABEL_CSV = "cells", "classifier.pkl", "labels.csv"

TIMINGS = ["2ヶ月", "4ヶ月", "8ヶ月", "1年", "1年4ヶ月", "1年8ヶ月", "2年"]
TIMING_MONTHS = [2, 4, 8, 12, 16, 20, 24]
CLASSES = ["空欄", "○", "◎", "×", "—", "在庫なし"]

N_COLS = 9          # 品番 + 受入日 + 保管期間7列
COL_ITEM, COL_DATE, COL_FIRST_RESULT = 0, 1, 2
THUMB = 32          # 分類器へ渡すセル画像の正規化サイズ
CROP_MARGIN = 10    # 罫線を巻き込まないための内側マージン(px)

SHEET_META = {"帳票番号": "No.109", "帳票日付": "2026.08.19"}

OUT_COLUMNS = [
    "ソースファイル", "帳票番号", "帳票日付", "ページ", "行",
    "キー",
    "品番", "受入日", "受入日_raw", "KNH社製KF25g",
    "検査タイミング", "検査タイミング_月数", "試験予定日", "試験時期到来",
    "結果", "今回追加分", "備考",
    "確信度", "要確認", "警告", "抽出日時",
]


# ================================================================ Stage 0 前処理

def load_page_image(pdf_path: str, page_no: int = 0) -> np.ndarray:
    """埋め込み画像を可能なら無劣化で取り出し、/Rotate を適用して BGR で返す。"""
    doc = fitz.open(pdf_path)
    try:
        page = doc.load_page(page_no)
        imgs = page.get_images(full=True)
        if len(imgs) == 1:
            # 単一のスキャン画像 → 再ラスタライズせず原本をそのまま使う(最高画質・最速)
            blob = doc.extract_image(imgs[0][0])["image"]
        else:
            # 複数画像やベクタ混在 → 原本と同等の解像度で描画(ここを絞ってはいけない)
            blob = page.get_pixmap(dpi=400).tobytes("png")
        rot = page.rotation % 360
    finally:
        doc.close()

    arr = cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_COLOR)
    if arr is None:
        raise RuntimeError("画像のデコードに失敗しました。")
    if rot:
        code = {90: cv2.ROTATE_90_CLOCKWISE,
                180: cv2.ROTATE_180,
                270: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(rot)
        if code is not None:
            arr = cv2.rotate(arr, code)
    return arr


def normalize_contrast(img: np.ndarray) -> np.ndarray:
    """スキャナごとの黒レベル差を吸収する。
    実測: 同じ帳票でも最暗部が 6 のスキャンと 91 のスキャンがあり、
    絶対値ベースの特徴量(濃さ・インク量)がそのままでは比較できない。
    紙白と最暗部を [255, 0] に線形に伸長して揃える。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    lo = float(np.percentile(gray, 0.5))     # 最も濃い印字
    hi = float(np.percentile(gray, 90))      # 紙白
    if hi - lo < 30:
        return img
    out = (img.astype(np.float32) - lo) * (255.0 / (hi - lo))
    log.info("  コントラスト正規化: 黒レベル %.0f / 白レベル %.0f", lo, hi)
    return np.clip(out, 0, 255).astype(np.uint8)


def prepare(pdf_path: str, page_no: int = 0) -> np.ndarray:
    """PDF読み込み → 回転補正 → 傾き補正 → コントラスト正規化。
    calibrate と run で必ず同じ前処理を通すこと(特徴量の互換性が崩れるため)。"""
    return normalize_contrast(deskew(load_page_image(pdf_path, page_no)))


def deskew(img: np.ndarray, max_deg: float = 3.0) -> np.ndarray:
    """長い横罫線の角度中央値から傾きを補正する。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 1800, 400,
                            minLineLength=img.shape[1] // 3, maxLineGap=20)
    if lines is None:
        return img

    # HoughLinesP の戻り値は環境により (N,1,4) / (N,4) と揺れるため形状を正規化する
    segs = np.asarray(lines).reshape(-1, 4)
    angs = [np.degrees(np.arctan2(float(y2 - y1), float(x2 - x1)))
            for x1, y1, x2, y2 in segs]
    angs = [a for a in angs if abs(a) < max_deg]
    if not angs:
        return img

    a = float(np.median(angs))
    if abs(a) < 0.05:
        return img
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), a, 1.0)
    log.info("  deskew: %.2f 度補正", a)
    return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


# ================================================================ Stage 1 格子確定

def _peaks(proj: np.ndarray, ratio: float, gap: int = 15) -> List[int]:
    if proj.max() <= 0:
        return []
    idx = np.where(proj > proj.max() * ratio)[0]
    if len(idx) == 0:
        return []
    groups = [[int(idx[0])]]
    for i in idx[1:]:
        i = int(i)
        (groups[-1].append(i) if i - groups[-1][-1] <= gap else groups.append([i]))
    return [int(np.mean(g)) for g in groups]


def _find_header_bottom(ver: np.ndarray, ys: List[int]) -> int:
    """品番列の縦罫線が始まる位置＝ヘッダ下端。"""
    col = ver[:, :ver.shape[1] // 4].sum(1)
    on = np.where(col > col.max() * 0.3)[0] if col.max() > 0 else []
    top = int(on[0]) if len(on) else ys[0]
    cands = [y for y in ys if y >= top]
    return min(cands) if cands else ys[0]


def detect_grid(img: np.ndarray) -> Tuple[List[int], List[int], set]:
    """縦罫線x座標・行境界y座標・境界を補完した行indexを返す。"""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY_INV, 25, 15)
    hor = cv2.morphologyEx(
        bw, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 25, 3), 1)))
    ver = cv2.morphologyEx(
        bw, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(h // 25, 3))))

    xs = _peaks(ver.sum(0) / 255, 0.35)
    if len(xs) != N_COLS + 1:
        raise RuntimeError(
            f"縦罫線が {len(xs)} 本しか取れません(期待 {N_COLS + 1} 本)。"
            "様式が異なるか、スキャン品質に問題があります。")

    ys_all = _peaks(hor.sum(1) / 255, 0.20)
    if len(ys_all) < 3:
        raise RuntimeError("横罫線が検出できません。")

    # 表の下端は「保管期間列の縦罫線が存在する範囲」で決める。
    # これをしないと、表の下の凡例・押印欄・余白まで等ピッチで格子が延長されてしまう。
    inner = ver[:, xs[COL_FIRST_RESULT]:xs[COL_FIRST_RESULT + 1]].sum(1)
    on = np.where(inner > inner.max() * 0.3)[0] if inner.max() > 0 else []
    tbl_bottom = int(on[-1]) if len(on) else ys_all[-1]

    head_bottom = _find_header_bottom(ver, ys_all)
    body = [y for y in ys_all if head_bottom <= y <= tbl_bottom + 5]
    if len(body) < 2:
        raise RuntimeError("表本体の行境界が特定できません。")

    d = np.diff(body)
    inner = d[d < 200]
    pitch = float(np.median(inner)) if len(inner) else float(np.median(d))

    ys: List[int] = [body[0]]
    interpolated: set = set()
    for a, b in zip(body, body[1:]):
        k = max(1, int(round((b - a) / pitch)))
        for j in range(1, k):
            ys.append(a + int(round((b - a) * j / k)))
            interpolated.add(len(ys) - 1)   # 罫線を補完した=境界が不確実な行
        ys.append(b)

    log.info("  格子: %d列 / %d行 / ピッチ %.0fpx / 補完 %d本",
             len(xs) - 1, len(ys) - 1, pitch, len(interpolated))
    return xs, ys, interpolated


def crop(img: np.ndarray, xs, ys, r: int, c: int, m: int = CROP_MARGIN) -> np.ndarray:
    y0, y1 = ys[r] + m, ys[r + 1] - m
    x0, x1 = xs[c] + m, xs[c + 1] - m
    if y1 <= y0 or x1 <= x0:            # マージンでセルが潰れる場合は無マージン
        y0, y1, x0, x1 = ys[r], ys[r + 1], xs[c], xs[c + 1]
    return img[y0:y1, x0:x1]


def crop_span(img: np.ndarray, xs, ys, r0: int, r1: int, c: int,
              m: int = CROP_MARGIN) -> np.ndarray:
    """行 r0〜r1(inclusive) にまたがる縦結合セルをまとめて切り出す。"""
    y0, y1 = ys[r0] + m, ys[r1 + 1] - m
    x0, x1 = xs[c] + m, xs[c + 1] - m
    if y1 <= y0 or x1 <= x0:
        y0, y1, x0, x1 = ys[r0], ys[r1 + 1], xs[c], xs[c + 1]
    return img[y0:y1, x0:x1]


def has_rule_below(img: np.ndarray, xs, ys, r: int, c: int) -> bool:
    """行 r と r+1 の境界に罫線があるか。
    黒画素の「量」ではなく「列方向のカバー率」で判定するのが要点。
    罫線はセル幅全体を覆う(実測1.00)が、境界にかかった文字は局所的(実測0.32以下)
    なので確実に分離できる。量で見ると文字を罫線と誤認する。"""
    if r + 1 >= len(ys) - 1:
        return True
    # 帯幅・左右マージンは解像度で変わるので行ピッチに比例させる(固定pxだと低解像度で取りこぼす)
    pitch = ys[r + 1] - ys[r]
    hb = max(3, int(round(pitch * 0.06)))
    mx = max(6, int(round((xs[c + 1] - xs[c]) * 0.08)))
    band = cv2.cvtColor(img[ys[r + 1] - hb:ys[r + 1] + hb, xs[c] + mx:xs[c + 1] - mx],
                        cv2.COLOR_BGR2GRAY)
    if band.size == 0:
        return True
    return float(((band < 160).any(axis=0)).mean()) > 0.7


def detect_merged_blocks(img: np.ndarray, xs, ys, c: int) -> List[Tuple[int, int]]:
    """列 c の縦結合ブロックを (開始行, 終了行) のリストで返す。
    品番は結合セルの中央に配置されるため、結合行数が偶数だと文字が行境界を
    またぐ。1行ずつOCRすると上下に割れて読めないので、ブロック単位で読む。"""
    blocks: List[Tuple[int, int]] = []
    start = 0
    for r in range(len(ys) - 1):
        if r == len(ys) - 2 or has_rule_below(img, xs, ys, r, c):
            blocks.append((start, r))
            start = r + 1
    return blocks


# ================================================================ Stage 2 左2列OCR

_ocr = None


def ocr_cell(cell: np.ndarray) -> str:
    """macOS Vision Framework で1セルを読む。印字なので極めて高精度。"""
    global _ocr
    if _ocr is None:
        try:
            from ocrmac import ocrmac as _m
            _ocr = _m
        except ImportError:
            log.error("ocrmac が未導入です: pip install ocrmac")
            _ocr = False
    if not _ocr or cell.size == 0:
        return ""

    from PIL import Image
    pil = Image.fromarray(cv2.cvtColor(cell, cv2.COLOR_BGR2RGB))
    try:
        res = _ocr.OCR(pil,
                       language_preference=["ja-JP", "en-US"],
                       recognition_level="accurate").recognize()
    except Exception as e:
        log.debug("OCR失敗: %s", e)
        return ""
    # 戻り値の要素数はバージョンで変わりうるので固定長アンパックはしない
    return " ".join(str(item[0]) for item in res).strip()


DATE_RE = re.compile(r"(20\d{2})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})")


def parse_date(raw: str) -> Optional[datetime]:
    """LLMに推測させず決定的に正規化する。読めなければ None(=要確認)。"""
    if not raw:
        return None
    s = raw.translate(str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1",
                                     "°": "", " ": "", "　": ""}))
    m = DATE_RE.search(s)
    if not m:
        return None
    try:
        return datetime(*(int(g) for g in m.groups()))
    except ValueError:
        return None


def normalize_item(raw: str) -> str:
    """品番の表記揺れ(全角・長音・空白)を吸収して No.1700-1 形式に寄せる。"""
    if not raw:
        return ""
    # 変換元と変換先は必ず同じ長さにする(全角数字10 + ハイフン類5 + ピリオド1 = 16)
    src = "０１２３４５６７８９" "－―ー‐−" "．"
    dst = "0123456789" "-----" "."
    assert len(src) == len(dst), (len(src), len(dst))
    s = re.sub(r"[\s　]+", "", raw.translate(str.maketrans(src, dst)))
    m = re.search(r"(\d{4})-?(\d)?", s)
    if not m:
        return s
    return f"No.{m.group(1)}" + (f"-{m.group(2)}" if m.group(2) else "")


# ================================================================ Stage 3 記号分類

def red_ratio(cell: np.ndarray) -> float:
    """赤画素比率。受入日末尾の●(KNH社製KF25g使用品)の検出に使う。"""
    if cell.size == 0:
        return 0.0
    b, g, r = (cell[:, :, i].astype(np.int16) for i in range(3))
    ink = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY) < 230
    hit = ((r - b > 45) & (r - g > 45) & ink).sum()
    return float(hit) / max(cell.shape[0] * cell.shape[1], 1)


def darkness(cell: np.ndarray) -> float:
    if cell.size == 0:
        return 0.0
    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    ink = gray < 228
    return float(255 - gray[ink].mean()) if ink.any() else 0.0


def features(cell: np.ndarray) -> np.ndarray:
    """32x32 濃度マップ + インク量 + 濃さ + 赤成分。
    字形が機械印字で個体差ゼロに近いため、少数データで十分な精度が出る。"""
    if cell.size == 0:
        return np.zeros(THUMB * THUMB + 3, np.float32)
    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    norm = cv2.resize(255 - gray, (THUMB, THUMB)).astype(np.float32) / 255.0
    ink = gray < 228
    dark = (255 - gray[ink].mean()) / 255.0 if ink.any() else 0.0
    return np.concatenate(
        [norm.ravel(), [float(ink.mean()), float(dark), red_ratio(cell)]]
    ).astype(np.float32)


LABEL_ALIASES = {
    # 見た目が同じでコードポイントが異なる文字を正規のクラス名に寄せる
    "○": "○", "◯": "○", "〇": "○", "O": "○", "o": "○", "０": "○",
    "◎": "◎", "@": "◎", "●": "◎",
    "×": "×", "✕": "×", "✖": "×", "☓": "×", "x": "×", "X": "×", "ｘ": "×",
    "—": "—", "―": "—", "−": "—", "-": "—", "ー": "—", "‐": "—", "─": "—", "ｰ": "—",
    "在庫なし": "在庫なし", "在庫無し": "在庫なし", "なし": "在庫なし",
    "空欄": "空欄", "空": "空欄", "": "空欄",
}


def normalize_label(v) -> Optional[str]:
    """人が入力したラベルを正規のクラス名に寄せる。判別不能なら None。"""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    s = str(v).strip()
    if s in ("", "nan", "NaN", "None"):
        return None
    if s in CLASSES:
        return s
    return LABEL_ALIASES.get(s)


def guess_label(cell: np.ndarray) -> str:
    """ラベル付けの「下書き」を作るための粗いヒューリスティック。
    8割程度しか当たらないので、必ず人が cells/ の画像と突き合わせて直すこと。
    最終的な精度は k-NN 分類器が担保する。"""
    if cell.size == 0:
        return "空欄"
    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    m = (gray < 228).astype(np.uint8)
    m[:6, :] = m[-6:, :] = m[:, :6] = m[:, -6:] = 0   # 罫線の残りを除去
    if m.sum() < 50:
        return "空欄"
    ys_, xs_ = np.where(m > 0)
    h_ = int(ys_.max() - ys_.min()) + 1
    w_ = int(xs_.max() - xs_.min()) + 1
    if w_ > cell.shape[1] * 0.5 and h_ < cell.shape[0] * 0.5:
        return "在庫なし"
    if h_ < cell.shape[0] * 0.15:
        return "—"
    _cnts, hier = cv2.findContours(m * 255, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    has_hole = hier is not None and bool((hier[0][:, 3] >= 0).any())
    if not has_hole:
        return "×"
    return "◎" if darkness(cell) > 105 else "○"


DARK_BOLD = 100.0   # これを超える濃さの○は「今回追加分(◎)」。実測: ○≦47 / ◎≧189


class SymbolClassifier:
    """形状5クラス(空欄/○/×/—/在庫なし)を k-NN で分類し、
    ◎(今回追加分)は濃さの閾値で決定的に切り分ける。
    ◎は出現数が少なくk-NNでは安定しないため、学習対象から外すのが要点。"""

    def __init__(self, model=None):
        self.model = model

    @staticmethod
    def train(X: np.ndarray, y: np.ndarray) -> "SymbolClassifier":
        from sklearn.neighbors import KNeighborsClassifier
        from sklearn.model_selection import LeaveOneOut, cross_val_predict
        y_shape = np.array(["○" if v == "◎" else v for v in y])
        counts = pd.Series(y_shape).value_counts()
        k = max(int(min(3, counts.min())), 1)
        clf = KNeighborsClassifier(n_neighbors=k, weights="distance")
        if len(counts) > 1 and len(y_shape) <= 3000:
            pred = cross_val_predict(clf, X, y_shape, cv=LeaveOneOut())
            acc = float((pred == y_shape).mean())
            log.info("Leave-One-Out精度: %.4f (%d/%d)",
                     acc, int((pred == y_shape).sum()), len(y_shape))
            for f, a, b in zip(range(len(y_shape)), y_shape, pred):
                if a != b:
                    log.warning("  誤分類: index=%d 正解=%s 予測=%s", f, a, b)
        clf.fit(X, y_shape)
        return SymbolClassifier(clf)

    def predict(self, cell: np.ndarray) -> Tuple[str, float]:
        f = features(cell).reshape(1, -1)
        proba = self.model.predict_proba(f)[0]
        i = int(proba.argmax())
        label = str(self.model.classes_[i])
        if label == "○" and darkness(cell) > DARK_BOLD:
            label = "◎"          # 濃い○ = 今回追加分
        return label, float(proba[i])

    def save(self, path: str) -> None:
        with open(path, "wb") as fp:
            pickle.dump(self.model, fp)

    @staticmethod
    def load(path: str) -> "SymbolClassifier":
        with open(path, "rb") as fp:
            return SymbolClassifier(pickle.load(fp))


# ================================================================ Stage 4 検証ルール

EARLIEST_DATE = datetime(2017, 1, 1)   # 帳票の運用開始(2017.7.31打合せ)より前の日付はありえない


def add_months(d: datetime, months: int) -> datetime:
    """暦上のNヶ月後。日数換算(30.44日)ではなく月末を正しく丸める。"""
    y, m = divmod((d.year * 12 + d.month - 1) + months, 12)
    m += 1
    last = [31, 29 if (y % 4 == 0 and y % 100 != 0) or y % 400 == 0 else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return datetime(y, m, min(d.day, last))


def due_date(d: Optional[datetime], months: int) -> Optional[datetime]:
    """試験予定日 = 受入日 + 保管期間。受入日が読めていなければ None。"""
    return add_months(d, months) if d else None


def make_key(item: str, d: Optional[datetime], months: int) -> str:
    """品番+受入日+月数 の一意キー。帳票をまたいだ重複判定・突合に使う。
    受入日が読めていない行はキーを作らない(誤った名寄せを防ぐため)。"""
    if not item or d is None:
        return ""
    return f"{item}|{d:%Y-%m-%d}|{months:02d}"


def validate(rec: dict, sheet_date: Optional[datetime]) -> List[str]:
    """帳票の運用ルールで機械的に検算する。VLM丸投げでは作れない防御線。"""
    w: List[str] = []
    d = rec.get("_date")
    months = TIMING_MONTHS[TIMINGS.index(rec["検査タイミング"])]

    if d:
        if d < EARLIEST_DATE:
            w.append(f"受入日({d:%Y-%m-%d})が運用開始前。OCR誤読の疑い")
        if sheet_date and d > sheet_date:
            w.append(f"受入日({d:%Y-%m-%d})が帳票日付より後。OCR誤読の疑い")
        if sheet_date:
            due = add_months(d, months)
            if due > sheet_date and rec["結果"] in ("○", "×"):
                w.append(f"試験予定日({due:%Y-%m-%d})が帳票日付より後なのに結果あり")

    if rec.get("_stopped_before") and rec["結果"] == "○":
        w.append("先行列で在庫なし(試験終了)なのに結果あり")
    if rec.get("_date_out_of_order"):
        w.append("同一品番内で受入日が昇順でない。OCR誤読の疑い")
    return w


# ================================================================ 抽出本体

def extract_pdf(pdf_path: str, clf: Optional[SymbolClassifier],
                meta: dict) -> List[dict]:
    img = prepare(pdf_path)
    xs, ys, interp = detect_grid(img)

    src = os.path.basename(pdf_path)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sheet_date = parse_date(meta.get("帳票日付", ""))

    # 品番は縦結合セル。行ごとではなくブロック単位で一度だけ読む。
    item_of_row: dict = {}
    blocks = detect_merged_blocks(img, xs, ys, COL_ITEM)
    for r0, r1 in blocks:
        name = normalize_item(ocr_cell(crop_span(img, xs, ys, r0, r1, COL_ITEM)))
        for r in range(r0, r1 + 1):
            item_of_row[r] = name
        if name:
            log.info("  品番ブロック 行%d-%d: %s", r0 + 1, r1 + 1, name)

    # 受入日を先に全行読み、品番ブロック内で昇順かを検査する。
    # 帳票はロットを受入順に追記していくため、昇順が崩れる = OCR誤読(1↔7, 8↔3 等)。
    # 「帳票日付より前だが年が違う」誤読はこの検査でしか捕まらない。
    date_raw_of_row: dict = {}
    date_of_row: dict = {}
    for r in range(len(ys) - 1):
        raw = ocr_cell(crop(img, xs, ys, r, COL_DATE))
        date_raw_of_row[r] = raw
        date_of_row[r] = parse_date(raw) if re.search(r"\d", raw) else None

    out_of_order: set = set()
    for r0, r1 in blocks:
        prev = None
        for r in range(r0, r1 + 1):
            d = date_of_row.get(r)
            if d is None:
                continue
            if prev is not None and d < prev:
                out_of_order.add(r)
                log.warning("  行%d: 受入日 %s が直前(%s)より過去です",
                            r + 1, f"{d:%Y-%m-%d}", f"{prev:%Y-%m-%d}")
            # 違反行も prev を更新する。更新しないと外れ値1件が後続行すべてを
            # 巻き添えにして誤検出が連鎖する。
            prev = d

    rows: List[dict] = []

    for r in range(len(ys) - 1):
        current_item = item_of_row.get(r, "")

        date_cell = crop(img, xs, ys, r, COL_DATE)
        date_raw = date_raw_of_row[r]          # 上の一括読み取りを再利用(OCRを二度走らせない)
        # 数字が1文字も無い行 = ヘッダ行・凡例行・押印欄。表外として捨てる。
        # 数字はあるが日付として読めない行は捨てずに残し、要確認を立てる。
        if not re.search(r"\d", date_raw):
            continue
        d = date_of_row[r]
        knh = red_ratio(date_cell) > 0.0008

        stopped = False
        for ci, timing in enumerate(TIMINGS):
            cell = crop(img, xs, ys, r, COL_FIRST_RESULT + ci)
            if clf:
                label, conf = clf.predict(cell)
            else:
                label, conf = ("空欄" if darkness(cell) < 5 else "要判定"), 0.0
            if label == "空欄":
                continue

            months = TIMING_MONTHS[ci]
            due = due_date(d, months)
            rec = {
                "ソースファイル": src,
                "帳票番号": meta.get("帳票番号", ""),
                "帳票日付": meta.get("帳票日付", ""),
                "ページ": 1,
                "行": r + 1,
                "キー": make_key(current_item, d, months),
                "品番": current_item,
                "受入日": d.strftime("%Y-%m-%d") if d else "",
                "受入日_raw": date_raw,
                "KNH社製KF25g": "●" if knh else "",
                "検査タイミング": timing,
                "検査タイミング_月数": months,
                "試験予定日": due.strftime("%Y-%m-%d") if due else "",
                "試験時期到来": ("" if (due is None or sheet_date is None)
                             else ("到来" if due <= sheet_date else "未到来")),
                "結果": "○" if label == "◎" else label,
                "今回追加分": "★" if label == "◎" else "",
                "備考": "在庫なし" if label == "在庫なし" else "",
                "確信度": round(conf, 3),
                "抽出日時": now,
                "_date": d,
                "_stopped_before": stopped,
                "_date_out_of_order": r in out_of_order,
            }
            rec["警告"] = " / ".join(validate(rec, sheet_date))
            rec["要確認"] = "★" if (conf < 0.90 or d is None or not current_item
                                   or r in interp or rec["警告"]) else ""
            rows.append(rec)
            if label == "在庫なし":
                stopped = True

    return rows


# ================================================================ コマンド

def cmd_calibrate(pdf_paths: List[str]) -> None:
    """格子を可視化し、全セルを画像として書き出してラベル付けを促す。
    複数のPDFを渡すと学習データをまとめて増やせる(薄い記号の取りこぼし対策)。"""
    os.makedirs(CELL_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    recs = []
    for pdf_path in pdf_paths:
        stem = re.sub(r"[^0-9A-Za-z_-]", "_", os.path.splitext(
            os.path.basename(pdf_path))[0])
        log.info("キャリブレーション: %s", os.path.basename(pdf_path))
        img = prepare(pdf_path)
        xs, ys, _ = detect_grid(img)

        dbg = img.copy()
        for x in xs:
            cv2.line(dbg, (x, ys[0]), (x, ys[-1]), (0, 200, 0), 4)
        for y in ys:
            cv2.line(dbg, (xs[0], y), (xs[-1], y), (255, 0, 0), 4)
        path = os.path.join(OUTPUT_DIR, f"grid_check_{stem}.png")
        cv2.imwrite(path, cv2.resize(dbg, (img.shape[1] // 3, img.shape[0] // 3)))
        log.info("  格子確認画像: %s", path)

        for r in range(len(ys) - 1):
            for ci, timing in enumerate(TIMINGS):
                cell = crop(img, xs, ys, r, COL_FIRST_RESULT + ci)
                name = f"{stem}_r{r + 1:02d}_c{ci + 1}.png"
                cv2.imwrite(os.path.join(CELL_DIR, name), cell)
                recs.append({"file": name, "帳票": stem, "行": r + 1, "列": timing,
                             "濃さ": round(darkness(cell), 1),
                             "推定": guess_label(cell), "label": ""})

    log.info("← 先に必ず grid_check_*.png を目視し、線が罫線に乗っているか確認してください")
    new = pd.DataFrame(recs)

    # 既存の手入力ラベルを絶対に消さない
    if os.path.exists(LABEL_CSV):
        old = pd.read_csv(LABEL_CSV, encoding="utf-8-sig")
        if "label" in old.columns and "file" in old.columns:
            kept = dict(zip(old["file"], old["label"]))
            new["label"] = new["file"].map(kept).fillna("")
            n = int((new["label"].astype(str).str.strip() != "").sum())
            if n:
                log.info("既存の label を %d件 引き継ぎました。", n)
        bak = LABEL_CSV + f".bak{datetime.now():%Y%m%d%H%M%S}"
        os.replace(LABEL_CSV, bak)
        log.info("旧 %s を %s に退避しました。", LABEL_CSV, bak)

    new.to_csv(LABEL_CSV, index=False, encoding="utf-8-sig")

    log.info("%s に %d セルを書き出しました。", CELL_DIR, len(recs))
    log.info("%s の label 列を次のいずれかで埋めてください: %s",
             LABEL_CSV, " / ".join(CLASSES))
    log.info("「推定」列に下書きが入っています。label 列にコピーしたうえで、")
    log.info("cells/ の画像と見比べて誤りを直してください(推定の的中率は約8割です)。")
    log.info("「濃さ」でソートすると 空欄→淡い○×→濃い◎ の順に並び、作業が速く済みます。")


def cmd_train(use_guess: bool = False) -> None:
    if not os.path.exists(LABEL_CSV):
        log.error("%s がありません。先に calibrate を実行してください。", LABEL_CSV)
        return

    df = pd.read_csv(LABEL_CSV, encoding="utf-8-sig")
    df.columns = [str(c).strip() for c in df.columns]
    log.info("%s: %d行 / 列 %s", LABEL_CSV, len(df), df.columns.tolist())

    col = "推定" if use_guess else "label"
    if col not in df.columns:
        log.error("列 '%s' がありません。calibrate を実行し直してください。", col)
        return
    if use_guess:
        log.warning("=" * 60)
        log.warning("推定列(ヒューリスティックの下書き)で学習します。的中率は約8割です。")
        log.warning("動作確認用であり、この分類器の出力を検収に使ってはいけません。")
        log.warning("=" * 60)

    raw = df[col]
    df["_label"] = raw.map(normalize_label)

    bad = raw[df["_label"].isna() & raw.notna() &
              (raw.astype(str).str.strip() != "")].astype(str).str.strip()
    if len(bad):
        log.error("解釈できない値が %d件あります: %s",
                  len(bad), dict(bad.value_counts().head(10)))
        log.error("使用できる値: %s", " / ".join(CLASSES))

    df = df[df["_label"].notna()]
    if len(df) < 30:
        log.error("有効なラベルが %d 件しかありません。", len(df))
        log.error("'%s' 列を %s のいずれかで埋めてください。", col, " / ".join(CLASSES))
        log.error("動作だけ先に確かめたい場合: python extract.py train --use-guess")
        return

    X, y = [], []
    for f, lab in zip(df["file"], df["_label"]):
        im = cv2.imread(os.path.join(CELL_DIR, f))
        if im is None:
            log.warning("読み込めません: %s", f)
            continue
        X.append(features(im))
        y.append(lab)

    X, y = np.array(X), np.array(y)
    log.info("学習データ %d件 / 内訳 %s", len(y), dict(pd.Series(y).value_counts()))
    SymbolClassifier.train(X, y).save(MODEL_PATH)
    log.info("分類器を %s に保存しました。", MODEL_PATH)


def cmd_run(meta: dict) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    clf = SymbolClassifier.load(MODEL_PATH) if os.path.exists(MODEL_PATH) else None
    if clf is None:
        log.warning("分類器が未作成です。calibrate → train を先に実行してください。")

    pdfs = sorted(glob.glob(os.path.join(INPUT_DIR, "*.pdf")))
    if not pdfs:
        log.error("%s にPDFがありません。", INPUT_DIR)
        return

    rows: List[dict] = []
    for p in pdfs:
        log.info("処理中: %s", os.path.basename(p))
        try:
            rows.extend(extract_pdf(p, clf, meta))
        except Exception as e:
            log.error("  失敗: %s", e, exc_info=log.isEnabledFor(logging.DEBUG))

    if not rows:
        log.error("抽出0件でした。")
        return

    df = pd.DataFrame(rows).reindex(columns=OUT_COLUMNS)
    out = os.path.join(OUTPUT_DIR, f"extracted_{datetime.now():%Y%m%d_%H%M}.xlsx")
    df.to_excel(out, index=False)

    n_chk = int((df["要確認"] == "★").sum())
    n_warn = int((df["警告"].fillna("") != "").sum())
    log.info("完了: %s", out)
    log.info("総レコード %d件 / 要確認 %d件 (%.1f%%) / 警告あり %d件",
             len(df), n_chk, 100 * n_chk / len(df), n_warn)

    # 帳票は過去分を再掲する構造なので、同じキーが複数の帳票に現れる。
    # 結果が食い違っていればどちらかがOCR誤読 = 突合による自動検証になる。
    keyed = df[df["キー"] != ""]
    if len(keyed):
        g = keyed.groupby("キー")["結果"].nunique()
        conflicts = g[g > 1]
        n_uniq = int(keyed["キー"].nunique())
        log.info("一意レコード %d件 / 重複 %d件 / 結果が食い違うキー %d件",
                 n_uniq, len(keyed) - n_uniq, len(conflicts))
        for k in list(conflicts.index)[:10]:
            vals = sorted(set(keyed.loc[keyed["キー"] == k, "結果"]))
            log.warning("  矛盾: %s → %s", k, " vs ".join(map(str, vals)))

    if n_chk:
        log.info("要確認★の行は原本と突合してください。")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "calibrate":
        if len(sys.argv) < 3:
            log.error("使い方: python extract.py calibrate <PDFパス> [PDFパス...]")
            return
        cmd_calibrate(sys.argv[2:])
    elif cmd == "train":
        cmd_train(use_guess="--use-guess" in sys.argv)
    elif cmd == "run":
        cmd_run(SHEET_META)
    else:
        log.error("不明なコマンド: %s (calibrate / train / run)", cmd)


if __name__ == "__main__":
    main()

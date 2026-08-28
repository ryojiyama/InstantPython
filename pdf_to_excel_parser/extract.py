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
REVIEW_CSV = "review.csv"      # 交差検証で誤分類したセルの一覧

TIMINGS = ["2ヶ月", "4ヶ月", "8ヶ月", "1年", "1年4ヶ月", "1年8ヶ月", "2年"]
TIMING_MONTHS = [2, 4, 8, 12, 16, 20, 24]


def timings_for(n_cols: int):
    """列数から保管期間の見出しを決める。
    6列の旧様式は先頭の (2ヶ月) が無い形なので、2列目以降を割り当てる。"""
    n = n_cols - 2                      # 品番・受入日を除いた列数
    if n == len(TIMINGS):
        return TIMINGS, TIMING_MONTHS
    if n == len(TIMINGS) - 1:
        return TIMINGS[1:], TIMING_MONTHS[1:]
    raise RuntimeError(f"保管期間の列数 {n} に対応する見出しが定義されていません。")
CLASSES = ["空欄", "○", "◎", "×", "—", "在庫なし"]

# 保管期間の列数は様式により異なる。運用開始直後の旧様式には (2ヶ月) 欄が無い。
# 実測: 新様式 = 品番 + 受入日 + 7列 = 9列 / 旧様式 = 品番 + 受入日 + 6列 = 8列
VALID_N_COLS = (8, 9)
MIN_LINE_LENGTH_RATIO = 0.55   # 最長の縦罫線に対する比。押印欄の仕切り線を除くため
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


def _columns_are_uniform(xs: List[int], tol: float = 0.12) -> bool:
    """保管期間の列が等幅かを確かめる。実測では誤差1px以内で揃っている。
    押印欄の仕切り線などが混ざった組み合わせを弾くための構造チェック。"""
    widths = [xs[i + 1] - xs[i] for i in range(COL_FIRST_RESULT, len(xs) - 1)]
    if len(widths) < 3:
        return False
    med = float(np.median(widths))
    return med > 0 and all(abs(w - med) <= med * tol for w in widths)


def _select_column_lines(ver: np.ndarray) -> List[int]:
    """縦罫線を「線の長さ」で選ぶ。

    押印欄の仕切り線は表の罫線の半分以下しかない(実測 255px 対 600px)ので、
    濃さの相対閾値ではなく長さで切るのが本筋。ただし付箋などで罫線が
    途切れたページでは長さがばらつくため、比率を段階的に緩めながら
    「列数が妥当で、保管期間の列が等幅」になる組み合わせを探す。"""
    proj = ver.sum(0) / 255.0
    cand = _peaks(proj, 0.20)
    if not cand:
        raise RuntimeError("縦罫線が検出できません。")

    longest = max(proj[x] for x in cand)
    tried = []
    for ratio in (0.55, 0.45, 0.38, 0.32, 0.26, 0.20):
        xs = [x for x in cand if proj[x] >= longest * ratio]
        tried.append((ratio, len(xs) - 1))
        if len(xs) - 1 in VALID_N_COLS and _columns_are_uniform(xs):
            if ratio != 0.55:
                log.info("  縦罫線の選別を %.2f まで緩めました(罫線のかすれ or 遮蔽)", ratio)
            return xs

    raise RuntimeError(
        f"表の列数を確定できません(期待 {VALID_N_COLS} 列)。試行: {tried}。"
        " 'python extract.py diagnose <PDF> <ページ>' で確認してください。")


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

    xs = _select_column_lines(ver)

    ys_all = _peaks(hor.sum(1) / 255, 0.20)
    if len(ys_all) < 3:
        raise RuntimeError("横罫線が検出できません。")

    # 表の下端は「表の全幅にわたる横罫線」の最後で決める。
    # 押印欄の罫線は全幅に届かない(実測カバー率0.78)ので、表の罫線(1.00)と区別できる。
    # 縦罫線の範囲で判定すると、押印欄の縦線がx方向で重なって誤判定する。
    tbl_bottom = ys_all[-1]
    for y in reversed(ys_all):
        band = hor[max(0, y - 3):y + 4, xs[0]:xs[-1]]
        if band.size and float((band > 0).any(axis=0).mean()) > 0.95:
            tbl_bottom = y
            break

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


def crop(img: np.ndarray, xs, ys, r: int, c: int, m: Optional[int] = None) -> np.ndarray:
    """セルを罫線を巻き込まずに切り出す。
    マージンは固定pxではなくセル寸法比。行ピッチは帳票により 44〜129px と幅があり、
    固定10pxだと低ピッチのページでセルの内容まで削ってしまう。"""
    mh = m if m is not None else max(2, int(round((ys[r + 1] - ys[r]) * 0.08)))
    mw = m if m is not None else max(2, int(round((xs[c + 1] - xs[c]) * 0.04)))
    y0, y1 = ys[r] + mh, ys[r + 1] - mh
    x0, x1 = xs[c] + mw, xs[c + 1] - mw
    if y1 <= y0 or x1 <= x0:            # マージンでセルが潰れる場合は無マージン
        y0, y1, x0, x1 = ys[r], ys[r + 1], xs[c], xs[c + 1]
    return img[y0:y1, x0:x1]


def crop_span(img: np.ndarray, xs, ys, r0: int, r1: int, c: int,
              m: Optional[int] = None) -> np.ndarray:
    """行 r0〜r1(inclusive) にまたがる縦結合セルをまとめて切り出す。"""
    mh = m if m is not None else max(2, int(round((ys[r0 + 1] - ys[r0]) * 0.08)))
    mw = m if m is not None else max(2, int(round((xs[c + 1] - xs[c]) * 0.04)))
    y0, y1 = ys[r0] + mh, ys[r1 + 1] - mh
    x0, x1 = xs[c] + mw, xs[c + 1] - mw
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


def _clean_ink(gray: np.ndarray) -> np.ndarray:
    """インクのマスクを作り、スキャンのゴミ(数画素の孤立点)を除去する。
    ゴミが残ると外接矩形が不当に広がり、薄い — や ○ の形が壊れる。"""
    ink = (gray < 228).astype(np.uint8)
    if ink.sum() == 0:
        return ink
    n, lbl, stats, _ = cv2.connectedComponentsWithStats(ink, 8)
    min_area = max(3, int(0.0006 * gray.size))
    keep = np.zeros(n, bool)
    for i in range(1, n):
        keep[i] = stats[i, cv2.CC_STAT_AREA] >= min_area
    return keep[lbl].astype(np.uint8)


def features(cell: np.ndarray) -> np.ndarray:
    """記号の形を、セルの縦横比とスキャナ濃度に依存しない形で表現する。

    セルの縦横比は帳票により大きく変わる(行ピッチ 44〜129px の実績)。
    セル全体をそのまま32x32に潰すと、同じ○が別物に見えてしまう。
    そこでインクの外接矩形を正方形にパディングしてから正規化し、
    「セル内でどれだけの幅・高さを占めるか」は別のスカラー特徴として渡す。

    薄い記号を空欄として切り捨てないこと。以前は少量のインクをゼロベクトルに
    潰していたため、薄い — や ○ が空欄と区別できなくなっていた。
    判断は分類器に任せ、ここではゴミ除去だけを行う。"""
    n = THUMB * THUMB + 8
    if cell.size == 0:
        return np.zeros(n, np.float32)

    gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    ink = _clean_ink(gray)
    n_ink = int(ink.sum())
    if n_ink == 0:
        return np.zeros(n, np.float32)

    ys_, xs_ = np.where(ink > 0)
    y0, y1 = int(ys_.min()), int(ys_.max()) + 1
    x0, x1 = int(xs_.min()), int(xs_.max()) + 1
    patch = ((255 - gray[y0:y1, x0:x1]).astype(np.float32)
             * ink[y0:y1, x0:x1])

    bh, bw = patch.shape
    side = max(bh, bw)
    canvas = np.zeros((side, side), np.float32)
    canvas[(side - bh) // 2:(side - bh) // 2 + bh,
           (side - bw) // 2:(side - bw) // 2 + bw] = patch
    peak = float(patch.max()) or 1.0
    norm = cv2.resize(canvas, (THUMB, THUMB)) / peak      # 濃さで割り、形だけを見る

    n_comp = int(cv2.connectedComponentsWithStats(ink, 8)[0]) - 1
    return np.concatenate([
        norm.ravel(),
        [n_ink / gray.size,            # セルに占めるインクの量
         (255 - gray[ink > 0].mean()) / 255.0,   # 平均の濃さ
         peak / 255.0,                 # 最も濃い画素(かすれの判定に効く)
         red_ratio(cell),
         bw / w,                       # セル幅に対する記号の幅(在庫なし=広い)
         bh / h,                       # セル高に対する記号の高さ(—=低い)
         bw / max(bh, 1),              # 記号自体の縦横比
         min(n_comp, 8) / 8.0],        # 連結成分の数(在庫なし=複数文字)
    ]).astype(np.float32)


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


def otsu_threshold(values: List[float]) -> Optional[float]:
    """1次元Otsu法。クラス間分散が最大になる境界を返す。"""
    v = np.sort(np.asarray(values, dtype=float))
    if len(v) < 4 or v[-1] - v[0] < 1e-6:
        return None
    best_score, best_t = -1.0, None
    for i in range(1, len(v)):
        a, b = v[:i], v[i:]
        score = len(a) * len(b) * (a.mean() - b.mean()) ** 2
        if score > best_score:
            best_score, best_t = score, (a.max() + b.min()) / 2.0
    return best_t


# 太字と細字の濃さの比がこれ未満なら、そのページには片方しか無いと判断する
BOLD_SEPARATION_RATIO = 1.35


def _confusion_summary(true, pred, top: int = 8) -> str:
    """誤分類の内訳を「正解→予測 件数」の形で多い順に並べる。"""
    from collections import Counter
    c = Counter((a, b) for a, b in zip(true, pred) if a != b)
    if not c:
        return "誤分類なし"
    return " / ".join(f"{a}→{b} {n}" for (a, b), n in c.most_common(top))


class SymbolClassifier:
    """形状5クラス(空欄/○/×/—/在庫なし)を k-NN で分類し、
    ◎(今回追加分)は濃さの閾値で決定的に切り分ける。
    ◎は出現数が少なくk-NNでは安定しないため、学習対象から外すのが要点。"""

    def __init__(self, model=None):
        self.model = model
        self.cv_pred = None      # 交差検証の予測(誤分類の書き出しに使う)
        self.cv_true = None
        self.cv_names = None

    @staticmethod
    def train(X: np.ndarray, y: np.ndarray,
              names: Optional[List[str]] = None) -> "SymbolClassifier":
        from sklearn.neighbors import KNeighborsClassifier
        from sklearn.model_selection import LeaveOneOut, cross_val_predict
        y_shape = np.array(["○" if v == "◎" else v for v in y])
        counts = pd.Series(y_shape).value_counts()
        k = max(int(min(3, counts.min())), 1)
        clf = KNeighborsClassifier(n_neighbors=k, weights="distance")

        pred = None
        if len(counts) > 1 and len(y_shape) <= 4000:
            pred = cross_val_predict(clf, X, y_shape, cv=LeaveOneOut())
            log.info("Leave-One-Out精度: %.4f (%d/%d)",
                     float((pred == y_shape).mean()),
                     int((pred == y_shape).sum()), len(y_shape))
            log.info("混同(正解→予測): %s", _confusion_summary(y_shape, pred))

        clf.fit(X, y_shape)
        model = SymbolClassifier(clf)
        model.cv_pred = pred
        model.cv_true = y_shape
        model.cv_names = names
        return model

    def predict(self, cell: np.ndarray,
                bold_threshold: Optional[float] = None) -> Tuple[str, float]:
        """形状を分類し、○は濃さで ○(過去分) / ◎(今回追加分) に分ける。
        濃さの絶対値は帳票ごとに大きく違う(○が41〜50の帳票と104〜120の帳票が実在)ため、
        閾値は固定値ではなくページごとに算出したものを渡すこと。"""
        f = features(cell).reshape(1, -1)
        proba = self.model.predict_proba(f)[0]
        i = int(proba.argmax())
        label = str(self.model.classes_[i])
        if label == "○" and bold_threshold is not None \
                and darkness(cell) > bold_threshold:
            label = "◎"
        return label, float(proba[i])

    def save(self, path: str) -> None:
        with open(path, "wb") as fp:
            pickle.dump(self.model, fp)

    @staticmethod
    def load(path: str) -> "SymbolClassifier":
        with open(path, "rb") as fp:
            return SymbolClassifier(pickle.load(fp))


# ================================================================ Stage 4 検証ルール

SHEET_NO_RE = re.compile(r"No\s*[.．]?\s*(\d{1,4})")


def read_sheet_meta(img: np.ndarray, xs, ys, fallback: dict) -> dict:
    """帳票日付(表の上・右寄り)と帳票番号(表の下・右下)をページごとに読む。
    1つのPDFに複数の帳票が綴じられており、ページごとに値が異なるため必須。"""
    h, w = img.shape[:2]
    meta = dict(fallback)

    top = img[0:max(ys[0] - 5, 1), int(w * 0.50):w]
    d = parse_date(ocr_cell(top))
    if d:
        meta["帳票日付"] = d.strftime("%Y.%m.%d")

    bottom = img[min(ys[-1] + 5, h - 1):h, int(w * 0.55):w]
    m = SHEET_NO_RE.search(ocr_cell(bottom))
    if m:
        meta["帳票番号"] = f"No.{m.group(1)}"

    return meta


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
    months = TIMING_MONTHS[TIMINGS.index(rec["検査タイミング"])]  # 全体表で引く

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

def _page_bold_threshold(img: np.ndarray, xs, ys,
                         clf: Optional[SymbolClassifier],
                         data_rows: List[int], n_timings: int) -> Optional[float]:
    """ページ内の記入済みセルの濃さから、太字(今回追加分)と細字(過去分)の境界を求める。
    見出し行の印字は濃いので必ず除外すること。含めると閾値が高く出て◎を取り逃がす。"""
    if clf is None:
        return None
    vals = []
    for r in data_rows:
        for ci in range(n_timings):
            cell = crop(img, xs, ys, r, COL_FIRST_RESULT + ci)
            label, _ = clf.predict(cell)          # 閾値なし = 形状のみ
            if label != "空欄":
                vals.append(darkness(cell))

    t = otsu_threshold(vals)
    if t is None:
        return None
    v = np.asarray(vals)
    lo, hi = v[v <= t], v[v > t]
    # 片方のクラスしか無いページでは、Otsuが無意味な位置で分割してしまう。
    # 境界値どうしではなく各クラスの平均で比べる(境界付近に値が連続していても
    # 二峰性は成り立つため、境界値の比では分離を過小評価してしまう)。
    if len(lo) < 2 or len(hi) < 2 \
            or hi.mean() / max(lo.mean(), 1e-6) < BOLD_SEPARATION_RATIO:
        log.info("  太字/細字の分離が不明瞭なため、すべて過去分として扱います")
        return None
    log.info("  太字判定のしきい値: 濃さ %.1f (細字%d件 平均%.0f / 太字%d件 平均%.0f)",
             t, len(lo), lo.mean(), len(hi), hi.mean())
    return float(t)


def extract_pdf(pdf_path: str, clf: Optional[SymbolClassifier],
                meta: dict) -> List[dict]:
    """PDF内の全ページを処理する。1PDF=1帳票とは限らず、
    複数の帳票(No.10〜No.1 など)が綴じられている場合がある。"""
    rows: List[dict] = []
    doc = fitz.open(pdf_path)
    n_pages = len(doc)
    doc.close()

    for page_no in range(n_pages):
        try:
            rows.extend(extract_page(pdf_path, page_no, clf, meta))
        except RuntimeError as e:
            # 様式違い(列数が合わない等)はページ単位でスキップし、処理は続行する。
            log.warning("  p%d/%d をスキップ: %s", page_no + 1, n_pages, e)
    return rows


def extract_page(pdf_path: str, page_no: int, clf: Optional[SymbolClassifier],
                 fallback_meta: dict) -> List[dict]:
    img = prepare(pdf_path, page_no)
    xs, ys, interp = detect_grid(img)

    timings, timing_months = timings_for(len(xs) - 1)
    src = os.path.basename(pdf_path)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta = read_sheet_meta(img, xs, ys, fallback_meta)
    sheet_date = parse_date(meta.get("帳票日付", ""))
    log.info("  p%d: %s / %s / %d行 / 保管期間%d列",
             page_no + 1, meta.get("帳票番号", "?"), meta.get("帳票日付", "?"),
             len(ys) - 1, len(timings))

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

    # 「今回追加分は太字」という規則は○だけでなく全記号に適用されている。
    # ページ内の記入済みセルすべての濃さからOtsu法で太字/細字の境界を決める。
    # 濃さの絶対値はスキャナと様式で大きく変わるので、固定閾値は使えない。
    data_rows = [r for r in range(len(ys) - 1)
                 if re.search(r"\d", date_raw_of_row.get(r, ""))]
    bold_threshold = _page_bold_threshold(img, xs, ys, clf, data_rows, len(timings))

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
        for ci, timing in enumerate(timings):
            cell = crop(img, xs, ys, r, COL_FIRST_RESULT + ci)
            if clf:
                label, conf = clf.predict(cell, bold_threshold)
            else:
                label, conf = ("空欄" if darkness(cell) < 5 else "要判定"), 0.0
            if label == "空欄":
                continue

            months = timing_months[ci]
            due = due_date(d, months)
            rec = {
                "ソースファイル": src,
                "帳票番号": meta.get("帳票番号", ""),
                "帳票日付": meta.get("帳票日付", ""),
                "ページ": page_no + 1,
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
        doc = fitz.open(pdf_path)
        n_pages = len(doc)
        doc.close()

        for page_no in range(n_pages):
            tag = stem if n_pages == 1 else f"{stem}_p{page_no + 1:02d}"
            try:
                img = prepare(pdf_path, page_no)
                xs, ys, _ = detect_grid(img)
            except RuntimeError as e:
                log.warning("  p%d/%d をスキップ: %s", page_no + 1, n_pages, e)
                continue

            dbg = img.copy()
            for x in xs:
                cv2.line(dbg, (x, ys[0]), (x, ys[-1]), (0, 200, 0), 4)
            for y in ys:
                cv2.line(dbg, (xs[0], y), (xs[-1], y), (255, 0, 0), 4)
            path = os.path.join(OUTPUT_DIR, f"grid_check_{tag}.png")
            cv2.imwrite(path, cv2.resize(dbg, (img.shape[1] // 3, img.shape[0] // 3)))
            log.info("  p%d: %d行 / 格子確認画像 %s", page_no + 1, len(ys) - 1, path)

            timings, _ = timings_for(len(xs) - 1)
            for r in range(len(ys) - 1):
                for ci, timing in enumerate(timings):
                    cell = crop(img, xs, ys, r, COL_FIRST_RESULT + ci)
                    name = f"{tag}_r{r + 1:02d}_c{ci + 1}.png"
                    cv2.imwrite(os.path.join(CELL_DIR, name), cell)
                    recs.append({"file": name, "帳票": tag, "行": r + 1, "列": timing,
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

    X, y, names = [], [], []
    for f, lab in zip(df["file"], df["_label"]):
        im = cv2.imread(os.path.join(CELL_DIR, f))
        if im is None:
            log.warning("読み込めません: %s", f)
            continue
        X.append(features(im))
        y.append(lab)
        names.append(f)

    X, y = np.array(X), np.array(y)
    log.info("学習データ %d件 / 内訳 %s", len(y), dict(pd.Series(y).value_counts()))
    model = SymbolClassifier.train(X, y, names=names)
    model.save(MODEL_PATH)
    log.info("分類器を %s に保存しました。", MODEL_PATH)

    # 誤分類したセルを review.csv に書き出す。
    # ラベルの付け間違いか、本当に難しいセルかを目で確かめるための入口。
    if model.cv_pred is not None:
        bad = [(nm, t, p) for nm, t, p in
               zip(names, model.cv_true, model.cv_pred) if t != p]
        if bad:
            rv = pd.DataFrame(bad, columns=["file", "現在のlabel", "予測"])
            rv = rv.merge(df[["file", "帳票", "行", "列", "濃さ"]], on="file", how="left")
            rv["確信度"] = ""
            rv = rv[["file", "帳票", "行", "列", "濃さ", "予測", "確信度",
                     "現在のlabel"]].sort_values(["帳票", "行", "列"])
            rv.to_csv(REVIEW_CSV, index=False, encoding="utf-8-sig")
            log.info("誤分類 %d件を %s に書き出しました。", len(rv), REVIEW_CSV)
            log.info("確認するには: python make_label_ui.py %s", REVIEW_CSV)


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


def cmd_diagnose(pdf_path: str, page_no: int = 0) -> None:
    """格子検出が失敗するページを調べる。縦罫線の候補を強度つきで並べ、
    検出した線を重ねた画像を出力する。様式違いなのか閾値の問題なのかを切り分ける。"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    img = prepare(pdf_path, page_no)
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    bw = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C,
                               cv2.THRESH_BINARY_INV, 25, 15)
    ver = cv2.morphologyEx(
        bw, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(h // 25, 3))))
    hor = cv2.morphologyEx(
        bw, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 25, 3), 1)))

    proj = ver.sum(0) / 255.0
    log.info("画像サイズ: %dx%d", w, h)
    log.info("しきい値ごとの縦罫線の本数:")
    for t in (0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60):
        xs = _peaks(proj, t)
        log.info("  %.2f -> %2d本  %s", t, len(xs), xs)

    xs = _peaks(proj, 0.35)
    log.info("各候補の高さ(縦方向の長さ, px):")
    for x in xs:
        log.info("  x=%5d  長さ=%5d  (画像高の%.0f%%)",
                 x, int(proj[x]), 100 * proj[x] / h)

    dbg = img.copy()
    for x in xs:
        cv2.line(dbg, (x, 0), (x, h), (0, 0, 255), 3)
    for y in _peaks(hor.sum(1) / 255.0, 0.20):
        cv2.line(dbg, (0, y), (w, y), (255, 0, 0), 2)
    name = re.sub(r"[^0-9A-Za-z_-]", "_",
                  os.path.splitext(os.path.basename(pdf_path))[0])
    out = os.path.join(OUTPUT_DIR, f"diagnose_{name}_p{page_no + 1:02d}.png")
    cv2.imwrite(out, cv2.resize(dbg, (w // 3, h // 3)))
    log.info("診断画像: %s", out)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "calibrate":
        if len(sys.argv) < 3:
            log.error("使い方: python extract.py calibrate <PDFパス> [PDFパス...]")
            return
        cmd_calibrate(sys.argv[2:])
    elif cmd == "diagnose":
        if len(sys.argv) < 3:
            log.error("使い方: python extract.py diagnose <PDFパス> [ページ番号]")
            return
        cmd_diagnose(sys.argv[2],
                     int(sys.argv[3]) - 1 if len(sys.argv) > 3 else 0)
    elif cmd == "train":
        cmd_train(use_guess="--use-guess" in sys.argv)
    elif cmd == "run":
        cmd_run(SHEET_META)
    else:
        log.error("不明なコマンド: %s (calibrate / train / run / diagnose)", cmd)


if __name__ == "__main__":
    main()

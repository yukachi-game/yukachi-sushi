import os
import sys

try:
    if hasattr(sys, "_MEIPASS"):
        # PyInstaller --onefile の自己展開先
        os.chdir(sys._MEIPASS)
    elif getattr(sys, "frozen", False):
        # Frozen (onefolder 等) の場合は exe 置き場
        os.chdir(os.path.dirname(sys.executable))
    else:
        # ソース実行時はこのファイルの場所
        os.chdir(os.path.dirname(os.path.abspath(__file__)))
except Exception:
    # 失敗してもクラッシュはさせない
    pass

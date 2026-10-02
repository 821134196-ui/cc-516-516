"""全局配置与路径。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FARM_DATA_DIR", BASE_DIR / "data"))
STATIC_DIR = BASE_DIR / "static"
DB_PATH = DATA_DIR / "farm.db"
IMG_DIR = STATIC_DIR / "img"

# 界面配色（白 / 果园绿 / 金黄）
COLORS = {
    "green": "#3f7d3a",
    "green_dark": "#2c5a28",
    "green_light": "#e8f2e2",
    "gold": "#e8a51c",
    "gold_light": "#fbf0d4",
    "ink": "#26301f",
    "gray": "#8a9383",
}

# 每日时段
SLOTS = ("08:30-11:00", "11:00-13:30", "13:30-16:00", "16:00-18:00")
SLOT_CAPACITY = 40            # 每个园区时段默认名额
CHILD_FREE_KG = 0.5           # 每名儿童免费重量
GRACE_MINUTES = 120           # 入园宽限（迟到 2 小时仍可核销）
PRICE_PER_KG = 18             # 超额部分每公斤单价（元）

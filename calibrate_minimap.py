import os
import time

import cv2
import numpy as np
import pyautogui
import win32con
import win32gui

data_location = os.path.dirname(os.path.abspath(__file__))
window_title = "MapleStory Worlds-Artale (繁體中文版)"

# === 在這裡調整猜測的小地圖區域，改完重跑看紅框有沒有貼齊 ===
minimap_region = (0, 80, 230, 120)  # (x, y, w, h) 相對於視窗左上角


def bring_window_to_front(title):
    hwnd = win32gui.FindWindow(None, title)
    if hwnd:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return hwnd
    raise Exception(f"❌ 找不到視窗：{title}")


def get_abs_region(hwnd, rel_region):
    rel_x, rel_y, w, h = rel_region
    abs_x, abs_y = win32gui.ClientToScreen(hwnd, (rel_x, rel_y))
    return (abs_x, abs_y, w, h)


hwnd = bring_window_to_front(window_title)
time.sleep(0.5)

full_abs = get_abs_region(hwnd, (0, 0, 1280, 800))
full_img = cv2.cvtColor(np.array(pyautogui.screenshot(region=full_abs)), cv2.COLOR_RGB2BGR)

x, y, w, h = minimap_region
overlay = full_img.copy()
cv2.rectangle(overlay, (x, y), (x + w, y + h), (0, 0, 255), 2)
cv2.imwrite(os.path.join(data_location, "minimap_calibration.png"), overlay)

crop = full_img[y:y + h, x:x + w]
cv2.imwrite(os.path.join(data_location, "minimap_crop.png"), crop)

print(f"✅ 已輸出 minimap_calibration.png（紅框標記位置）與 minimap_crop.png（裁切結果）")
print(f"目前猜測區域：{minimap_region}")
print("打開這兩張圖檢查：紅框有沒有剛好框住小地圖？裁切結果是不是乾淨的小地圖？")
print("沒對齊的話，修改本檔案最上面的 minimap_region 數字再重跑一次。")

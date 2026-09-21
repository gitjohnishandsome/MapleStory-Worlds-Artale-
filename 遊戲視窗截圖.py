import pyautogui
import win32gui
import win32con
import os
import time

data_location = os.path.dirname(os.path.abspath(__file__))#__file__代表「這支 .py 檔案本身的完整檔名（含路徑）

window_title = "MapleStory Worlds-Artale (繁體中文版)"
region = (0, 0, 1280, 800)  # 螢幕上的截圖區域

shot_count = 5
shot_interval_sec = 1

def bring_window_to_front(title):
    hwnd = win32gui.FindWindow(None, title)
    if hwnd:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return hwnd
    else:
        raise Exception("找不到 MapleStory 視窗")

def get_abs_region(hwnd, rel_region):
    rel_x, rel_y, w, h = rel_region
    abs_x, abs_y = win32gui.ClientToScreen(hwnd, (rel_x, rel_y))
    return (abs_x, abs_y, w, h)

# 執行截圖：每隔 shot_interval_sec 秒截一次，共 shot_count 張
hwnd = bring_window_to_front(window_title)
time.sleep(0.5)
region_abs = get_abs_region(hwnd, region)

for i in range(1, shot_count + 1):
    img = pyautogui.screenshot(region=region_abs)
    output_path = os.path.join(data_location, f"screenshot_{i}.png")
    try:
        img.save(output_path)
        print(f"✅ 已儲存第 {i} 張：{os.path.basename(output_path)}")
    except OSError as e:
        print(f"❌ 第 {i} 張存檔失敗：{e}")
    if i < shot_count:
        time.sleep(shot_interval_sec)
"""
撿錢路線錄製工具

手動操作角色走一段「撿錢徘徊路線」，工具會記錄：
  - 錄製開始那一刻，角色在小地圖上的座標（當作這條路線的起點）
  - 完整的按鍵事件時間軸（幾秒的時候按下/放開哪個鍵），可以正確重現同時按鍵的瞬移動作

存成一個 JSON 檔案，之後 main_v6.py 可以讀這個檔案重播。
"""

import os
import time
import json
import glob

import cv2
import numpy as np
import pyautogui
import yaml
import keyboard
import win32con
import win32gui

data_location = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(data_location, "config.yaml"), "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

window_title = config["paths"]["window_title"]
minimap_region = tuple(config["minimap"]["region"])
yellow_dot_template_path = os.path.join(data_location, config["minimap"]["yellow_dot_template"])
dot_match_threshold = config["minimap"]["match_threshold"]

routes_dir = os.path.join(data_location, config["pickup_route"]["routes_dir"])

# 錄製時監聽的按鍵：跟 main_v6.py 的 keys 設定一致，攻擊鍵不追蹤
KEY_LEFT = config["keys"]["left"]
KEY_RIGHT = config["keys"]["right"]
KEY_UP = config["keys"]["up"]
KEY_DOWN = config["keys"]["down"]
KEY_TELEPORT = config["keys"]["teleport"]

TRACKED_KEYS = {KEY_LEFT, KEY_RIGHT, KEY_UP, KEY_DOWN, KEY_TELEPORT}


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


def capture_region(hwnd, rel_region):
    abs_region = get_abs_region(hwnd, rel_region)
    screenshot = pyautogui.screenshot(region=abs_region)
    return cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)


def load_yellow_dot_template():
    template = cv2.imread(yellow_dot_template_path, cv2.IMREAD_COLOR)
    if template is None:
        raise ValueError(f"❌ 找不到黃點樣板圖：{yellow_dot_template_path}")
    return template


def find_dot_position(hwnd, template):
    minimap_img = capture_region(hwnd, minimap_region)
    result = cv2.matchTemplate(minimap_img, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < dot_match_threshold:
        return None
    th, tw = template.shape[:2]
    return max_loc[0] + tw // 2, max_loc[1] + th // 2


class Recorder:
    def __init__(self):
        self.recording = False
        self.start_pos = None
        self.start_time = None
        self.events = []  # [{"key": "left", "event": "down"/"up", "t": 0.32}, ...]
        self.held_keys = set()  # 目前正按著的追蹤鍵，避免系統重複觸發 down 事件

    def start(self, hwnd, yellow_dot_template):
        if self.recording:
            print("⚠ 已經在錄製中了")
            return

        pos = find_dot_position(hwnd, yellow_dot_template)
        if pos is None:
            print("❌ 小地圖上找不到黃點，無法開始錄製（確認角色/小地圖都正常顯示）")
            return

        self.start_pos = pos
        self.events = []
        self.held_keys = set()
        self.start_time = time.time()
        self.recording = True
        print(f"🔴 開始錄製，起點座標 = {pos}，開始操作角色吧")

    def on_key_event(self, event):
        if not self.recording or event.name not in TRACKED_KEYS:
            return

        if event.event_type == "down":
            if event.name in self.held_keys:
                return  # 系統的按鍵重複觸發，忽略
            self.held_keys.add(event.name)
        else:
            if event.name not in self.held_keys:
                return
            self.held_keys.discard(event.name)

        t = round(time.time() - self.start_time, 3)
        self.events.append({"key": event.name, "event": event.event_type, "t": t})
        print(f"  {event.event_type}: {event.name} @ {t:.2f}s")

    def stop_and_save(self):
        if not self.recording:
            print("⚠ 目前沒有在錄製")
            return

        self.recording = False
        if not self.events:
            print("⚠ 沒有錄到任何按鍵，不存檔")
            return

        os.makedirs(routes_dir, exist_ok=True)

        # 用「現有檔名裡最大的編號」而不是「現有檔案數量」算下一個編號，
        # 避免中間刪過檔案時，數量對不上編號，導致算出一個已經存在的檔名把舊路線蓋掉。
        existing_indices = []
        for p in glob.glob(os.path.join(routes_dir, "route_*.json")):
            stem = os.path.splitext(os.path.basename(p))[0]  # "route_3"
            suffix = stem[len("route_"):]
            if suffix.isdigit():
                existing_indices.append(int(suffix))
        next_index = max(existing_indices, default=0) + 1

        path = os.path.join(routes_dir, f"route_{next_index}.json")
        while os.path.exists(path):  # 保險：萬一還是撞到現有檔案，繼續往上加
            next_index += 1
            path = os.path.join(routes_dir, f"route_{next_index}.json")

        data = {
            "start_x": self.start_pos[0],
            "start_y": self.start_pos[1],
            "events": self.events,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"💾 已存檔：{os.path.basename(path)}（共 {len(self.events)} 個按鍵事件）")


def main():
    hwnd = bring_window_to_front(window_title)
    yellow_dot_template = load_yellow_dot_template()
    recorder = Recorder()

    keyboard.hook(recorder.on_key_event)
    keyboard.add_hotkey("f5", lambda: recorder.start(hwnd, yellow_dot_template), suppress=True)
    keyboard.add_hotkey("f6", recorder.stop_and_save, suppress=True)
    keyboard.add_hotkey("f7", lambda: os._exit(0), suppress=True)

    print("🎬 撿錢路線錄製工具")
    print("   F5：開始錄製（會記下當下小地圖座標當起點）")
    print(f"   F6：停止並存檔（存到 {routes_dir}/route_N.json）")
    print("   F7：結束程式")
    print(f"   錄製時只會記錄方向鍵({KEY_LEFT}/{KEY_RIGHT}/{KEY_UP}/{KEY_DOWN})跟瞬移鍵({KEY_TELEPORT})，攻擊鍵不會被記錄")
    print("   同時按住多個鍵（例如瞬移組合）會被完整記錄下來，重播時會正確重現")

    keyboard.wait()


if __name__ == "__main__":
    main()

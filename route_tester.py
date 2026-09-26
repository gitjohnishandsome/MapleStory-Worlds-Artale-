"""
撿錢路線測試/錄製/管理工具

從 pickup_routes/ 選一條路線可以測試（走到起點 + 重播按鍵動作）或刪除，
也可以直接在這裡錄製新路線，不用另外開 route_recorder.py。
"""

import os
import time
import json
import glob
import tkinter as tk
from tkinter import ttk, messagebox

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

KEY_LEFT = config["keys"]["left"]
KEY_RIGHT = config["keys"]["right"]
KEY_UP = config["keys"]["up"]
KEY_DOWN = config["keys"]["down"]
KEY_TELEPORT = config["keys"]["teleport"]
DIRECTION_KEYS = {"left": KEY_LEFT, "right": KEY_RIGHT, "up": KEY_UP, "down": KEY_DOWN}
TRACKED_KEYS = {KEY_LEFT, KEY_RIGHT, KEY_UP, KEY_DOWN, KEY_TELEPORT}

navigate_tolerance = config["pickup_route"]["navigate_tolerance"]
navigate_max_attempts = config["pickup_route"]["navigate_max_attempts"]
navigate_walk_ms = config["pickup_route"]["navigate_walk_ms"]
navigate_teleport_ms = config["pickup_route"]["navigate_teleport_ms"]
drop_to_ground_attempts = config["pickup_route"]["drop_to_ground_attempts"]


def bring_window_to_front(title):
    hwnd = win32gui.FindWindow(None, title)
    if hwnd:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return hwnd
    raise Exception(f"❌ 找不到視窗：{title}")


def focus_window(hwnd):
    win32gui.SetForegroundWindow(hwnd)


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


def walk(hwnd, direction, hold_ms):
    focus_window(hwnd)
    key = DIRECTION_KEYS[direction]
    pyautogui.keyDown(key)
    time.sleep(hold_ms / 1000)
    pyautogui.keyUp(key)


def teleport(hwnd, direction, hold_ms):
    focus_window(hwnd)
    key = DIRECTION_KEYS[direction]
    pyautogui.keyDown(KEY_TELEPORT)
    time.sleep(hold_ms / 1000)
    pyautogui.keyDown(key)
    time.sleep(hold_ms / 1000)
    pyautogui.keyUp(KEY_TELEPORT)
    time.sleep(0.1)
    pyautogui.keyUp(key)


def navigate_to_point(hwnd, yellow_dot_template, target_x, log):
    for _ in range(drop_to_ground_attempts):
        teleport(hwnd, "down", navigate_teleport_ms)

    for attempt in range(1, navigate_max_attempts + 1):
        dot_pos = find_dot_position(hwnd, yellow_dot_template)
        if dot_pos is None:
            log(f"⚠ [{attempt}/{navigate_max_attempts}] 小地圖上找不到黃點，放棄移動")
            return False

        dot_x, _ = dot_pos
        dx = target_x - dot_x
        if abs(dx) <= navigate_tolerance:
            log(f"✅ 已抵達起點附近（黃點 x={dot_x}）")
            return True

        direction = "right" if dx > 0 else "left"
        log(f"  [{attempt}/{navigate_max_attempts}] 黃點 x={dot_x}，往{direction}修正")
        walk(hwnd, direction, navigate_walk_ms)

    log(f"⚠ 移動到起點失敗（已達 {navigate_max_attempts} 次上限）")
    return False


def next_route_path():
    """用「現有檔名裡最大的編號」而不是「檔案數量」算下一個檔名，避免中間刪過檔案時算出撞名覆蓋舊檔。"""
    existing_indices = []
    for p in glob.glob(os.path.join(routes_dir, "route_*.json")):
        stem = os.path.splitext(os.path.basename(p))[0]
        suffix = stem[len("route_"):]
        if suffix.isdigit():
            existing_indices.append(int(suffix))
    next_index = max(existing_indices, default=0) + 1

    path = os.path.join(routes_dir, f"route_{next_index}.json")
    while os.path.exists(path):
        next_index += 1
        path = os.path.join(routes_dir, f"route_{next_index}.json")
    return path


def play_route(hwnd, route, log):
    focus_window(hwnd)
    events = sorted(route["events"], key=lambda e: e["t"])

    last_t = 0.0
    for ev in events:
        wait_s = ev["t"] - last_t
        if wait_s > 0:
            time.sleep(wait_s)
        last_t = ev["t"]
        if ev["event"] == "down":
            pyautogui.keyDown(ev["key"])
        else:
            pyautogui.keyUp(ev["key"])
        log(f"  {ev['event']}: {ev['key']} @ {ev['t']:.2f}s")

    for key in (KEY_LEFT, KEY_RIGHT, KEY_UP, KEY_DOWN, KEY_TELEPORT):
        pyautogui.keyUp(key)


class RouteTesterApp:
    def __init__(self, root):
        self.root = root
        root.title("撿錢路線測試/錄製工具")

        control_frame = tk.Frame(root)
        control_frame.pack(side="top", fill="x", padx=8, pady=8)

        tk.Label(control_frame, text="選擇路線：").pack(side="left")
        self.route_var = tk.StringVar()
        self.route_menu = ttk.Combobox(
            control_frame, textvariable=self.route_var, state="readonly", width=30
        )
        self.route_menu.pack(side="left", padx=4)
        self.route_menu.bind("<<ComboboxSelected>>", lambda e: self.show_route_info())

        self.reload_button = tk.Button(control_frame, text="重新整理清單", command=self.reload_routes)
        self.reload_button.pack(side="left", padx=4)
        self.run_button = tk.Button(control_frame, text="▶ 執行測試", command=self.run_test)
        self.run_button.pack(side="left", padx=4)
        self.delete_button = tk.Button(control_frame, text="🗑 刪除路線", command=self.delete_route)
        self.delete_button.pack(side="left", padx=4)

        record_frame = tk.Frame(root)
        record_frame.pack(side="top", fill="x", padx=8, pady=(0, 8))

        self.record_button = tk.Button(record_frame, text="🔴 開始錄製", command=self.toggle_recording)
        self.record_button.pack(side="left")
        self.record_status_label = tk.Label(record_frame, text="", anchor="w", fg="#a00")
        self.record_status_label.pack(side="left", padx=8)

        self.info_label = tk.Label(root, text="", anchor="w", justify="left")
        self.info_label.pack(side="top", fill="x", padx=8)

        self.log_text = tk.Text(root, height=22, width=90)
        self.log_text.pack(side="top", padx=8, pady=8)

        self.routes = {}  # 檔名 -> 完整路徑

        # 錄製狀態
        self.recording = False
        self.rec_start_pos = None
        self.rec_events = []
        self.rec_held_keys = set()
        self.rec_start_time = None

        keyboard.hook(self.on_key_event)

        self.reload_routes()

    def log(self, msg):
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.update()

    def reload_routes(self):
        self.routes = {}
        for p in sorted(glob.glob(os.path.join(routes_dir, "route_*.json"))):
            self.routes[os.path.basename(p)] = p

        self.route_menu["values"] = list(self.routes.keys())
        if self.routes and not self.route_var.get():
            self.route_var.set(list(self.routes.keys())[0])
            self.show_route_info()

        self.log(f"找到 {len(self.routes)} 條路線：{', '.join(self.routes.keys()) or '（無）'}")

    def show_route_info(self):
        name = self.route_var.get()
        if not name:
            return
        with open(self.routes[name], "r", encoding="utf-8") as f:
            route = json.load(f)
        duration = route["events"][-1]["t"] if route["events"] else 0
        self.info_label.config(
            text=f"起點：(x={route['start_x']}, y={route['start_y']})　"
                 f"按鍵事件數：{len(route['events'])}　預估時長：{duration:.2f} 秒"
        )

    def run_test(self):
        name = self.route_var.get()
        if not name:
            self.log("❌ 請先選一條路線")
            return

        self.run_button.config(state="disabled")
        try:
            with open(self.routes[name], "r", encoding="utf-8") as f:
                route = json.load(f)

            self.log(f"\n=== 開始測試：{name} ===")
            hwnd = bring_window_to_front(window_title)
            yellow_dot_template = load_yellow_dot_template()

            self.log("步驟1：先強制往下瞬移到地平線，再水平移動到起點...")
            reached = navigate_to_point(hwnd, yellow_dot_template, route["start_x"], self.log)
            if not reached:
                self.log("❌ 測試失敗：沒能走到起點")
                return

            self.log("步驟2：重播路線按鍵動作...")
            play_route(hwnd, route, self.log)
            self.log("✅ 測試完成，路線執行完畢")
        except Exception as e:
            self.log(f"❌ 執行時發生錯誤：{e}")
        finally:
            self.run_button.config(state="normal")

    def delete_route(self):
        name = self.route_var.get()
        if not name:
            self.log("❌ 請先選一條路線")
            return

        if not messagebox.askyesno("確認刪除", f"確定要刪除路線「{name}」嗎？這個動作無法復原。"):
            return

        try:
            os.remove(self.routes[name])
            self.log(f"🗑 已刪除：{name}")
        except OSError as e:
            self.log(f"❌ 刪除失敗：{e}")
            return

        self.route_var.set("")
        self.info_label.config(text="")
        self.reload_routes()

    # === 錄製 ===
    def on_key_event(self, event):
        if not self.recording or event.name not in TRACKED_KEYS:
            return

        if event.event_type == "down":
            if event.name in self.rec_held_keys:
                return
            self.rec_held_keys.add(event.name)
        else:
            if event.name not in self.rec_held_keys:
                return
            self.rec_held_keys.discard(event.name)

        t = round(time.time() - self.rec_start_time, 3)
        self.rec_events.append({"key": event.name, "event": event.event_type, "t": t})
        self.record_status_label.config(text=f"🔴 錄製中...已記錄 {len(self.rec_events)} 個按鍵事件")

    def toggle_recording(self):
        if self.recording:
            self.stop_recording_and_save()
        else:
            self.start_recording()

    def start_recording(self):
        try:
            hwnd = bring_window_to_front(window_title)
            yellow_dot_template = load_yellow_dot_template()
        except Exception as e:
            self.log(f"❌ 無法開始錄製：{e}")
            return

        pos = find_dot_position(hwnd, yellow_dot_template)
        if pos is None:
            self.log("❌ 小地圖上找不到黃點，無法開始錄製")
            return

        self.rec_start_pos = pos
        self.rec_events = []
        self.rec_held_keys = set()
        self.rec_start_time = time.time()
        self.recording = True

        self.record_button.config(text="⏹ 停止並存檔")
        self.run_button.config(state="disabled")
        self.delete_button.config(state="disabled")
        self.reload_button.config(state="disabled")
        self.record_status_label.config(text="🔴 錄製中...已記錄 0 個按鍵事件")
        self.log(f"\n🔴 開始錄製，起點座標 = {pos}，切到遊戲視窗開始操作角色吧")

    def stop_recording_and_save(self):
        self.recording = False
        self.record_button.config(text="🔴 開始錄製")
        self.run_button.config(state="normal")
        self.delete_button.config(state="normal")
        self.reload_button.config(state="normal")
        self.record_status_label.config(text="")

        if not self.rec_events:
            self.log("⚠ 沒有錄到任何按鍵，不存檔")
            return

        path = next_route_path()
        data = {
            "start_x": self.rec_start_pos[0],
            "start_y": self.rec_start_pos[1],
            "events": self.rec_events,
        }
        os.makedirs(routes_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        self.log(f"💾 已存檔：{os.path.basename(path)}（共 {len(self.rec_events)} 個按鍵事件）")
        self.reload_routes()


def main():
    root = tk.Tk()
    RouteTesterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

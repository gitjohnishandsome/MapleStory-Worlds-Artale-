import threading
import time
import random
import glob
import json
import os
import sys
import winsound
import tkinter as tk
from tkinter import messagebox
from datetime import datetime

import cv2
import numpy as np
import pyautogui
import yaml
import keyboard
import win32con
import win32gui

# === 讀取設定檔 ===
data_location = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(data_location, "config.yaml"), "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)


# === 執行紀錄存檔：把終端機輸出同時複製一份到 log 檔案，方便事後回溯 ===
class TeeLogger:
    def __init__(self, log_path):
        self.terminal = sys.stdout
        self.log_file = open(log_path, "a", encoding="utf-8")
        self.lock = threading.Lock()
        self._line_buffer = ""

    def write(self, message):
        self.terminal.write(message)
        with self.lock:
            self._line_buffer += message
            while "\n" in self._line_buffer:
                line, self._line_buffer = self._line_buffer.split("\n", 1)
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.log_file.write(f"[{timestamp}] {line}\n")
            self.log_file.flush()

    def flush(self):
        self.terminal.flush()
        self.log_file.flush()


if config["logging"]["enabled"]:
    log_dir = os.path.join(data_location, config["logging"]["directory"])
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    sys.stdout = TeeLogger(log_path)
    print(f"📝 本次執行紀錄存到：{log_path}")

window_title = config["paths"]["window_title"]

KEY_LEFT = config["keys"]["left"]
KEY_RIGHT = config["keys"]["right"]
KEY_UP = config["keys"]["up"]
KEY_DOWN = config["keys"]["down"]
KEY_JUMP = config["keys"]["jump"]
KEY_TELEPORT = config["keys"]["teleport"]
KEY_ATTACK = config["keys"]["magic_attack"]

DIRECTION_KEYS = {"left": KEY_LEFT, "right": KEY_RIGHT, "up": KEY_UP, "down": KEY_DOWN}

T = config["timings"]

game_view_region = tuple(config["game_view"]["region"])
screen_center_x = game_view_region[2] // 2  # 偵測不到角色時的備援中心點
screen_center_y = game_view_region[3] // 2  # 偵測不到角色時的備援垂直中心點

team_blood_template_path = os.path.join(data_location, config["character_detection"]["team_blood_template"])
character_hsv_margin = tuple(config["character_detection"]["hsv_margin"])
character_min_area = config["character_detection"]["min_area"]
character_exclude_bottom_px = config["character_detection"]["exclude_bottom_px"]
character_max_width = config["character_detection"]["max_width"]

monster_template_glob = os.path.join(data_location, config["monster_detection"]["template_glob"])
monster_match_threshold = config["monster_detection"]["match_threshold"]
monster_attack_range_x = config["monster_detection"]["attack_range_x"]
vertical_deadzone_y = config["monster_detection"]["vertical_deadzone_y"]

minimap_region = tuple(config["minimap"]["region"])
yellow_dot_template_path = os.path.join(data_location, config["minimap"]["yellow_dot_template"])
minimap_left_ratio = config["minimap"]["left_ratio"]
minimap_right_ratio = config["minimap"]["right_ratio"]
dot_match_threshold = config["minimap"]["match_threshold"]
red_dot_template_path = os.path.join(data_location, config["minimap"]["red_dot_template"])
red_dot_match_threshold = config["minimap"]["red_dot_match_threshold"]

walk_probability = config["behavior"]["walk_probability"]
ignore_monster_probability = config["behavior"]["ignore_monster_probability"]
walk_mode_weights = config["behavior"]["walk_mode_weights"]
loop_delay_range = tuple(config["behavior"]["loop_delay_range"])
detection_interval = config["behavior"]["detection_interval"]

default_runtime_minutes = config["safety"]["max_runtime_hours"] * 60
stop_grace_seconds = config["safety"]["stop_grace_seconds"]
enable_failsafe = config["safety"]["enable_failsafe"]
incident_capture_key = config["safety"]["incident_capture_key"]
incident_screenshot_count = config["safety"]["incident_screenshot_count"]
incident_screenshot_interval = config["safety"]["incident_screenshot_interval"]
incident_dir = os.path.join(data_location, "incident_screenshots")

debug_window_enabled = config["debug_window"]["enabled"]
debug_window_scale = config["debug_window"]["display_scale"]

pickup_routes_dir = os.path.join(data_location, config["pickup_route"]["routes_dir"])
navigate_tolerance = config["pickup_route"]["navigate_tolerance"]
navigate_max_attempts = config["pickup_route"]["navigate_max_attempts"]
navigate_walk_ms = config["pickup_route"]["navigate_walk_ms"]
navigate_teleport_ms = config["pickup_route"]["navigate_teleport_ms"]
drop_to_ground_attempts = config["pickup_route"]["drop_to_ground_attempts"]
attacks_before_pickup_range = tuple(config["pickup_route"]["attacks_before_pickup"])

# 霧氣導致對比度下降時，用 CLAHE 拉回對比，讓樣板比對比較不受影響
clahe_processor = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

# pyautogui 內建安全機制：滑鼠移到螢幕角落會拋出 FailSafeException
pyautogui.FAILSAFE = enable_failsafe

stop_event = threading.Event()
keyboard.add_hotkey(
    "esc", lambda: (print("🛑 偵測到 ESC，準備結束"), stop_event.set()), suppress=True
)


# === 警示音 ===
def play_alert_sound():
    """比系統提示音更明顯：連續三聲高音蜂鳴警報。"""
    for _ in range(3):
        winsound.Beep(1200, 300)
        time.sleep(0.1)


# === 開機提醒：先組隊亮出血條，角色定位才會準 ===
def show_party_reminder():
    root = tk.Tk()
    root.withdraw()  # 不用顯示空白主視窗，只跳提示框
    root.attributes("-topmost", True)
    messagebox.showinfo(
        "開始前確認",
        "請先在遊戲裡建立隊伍（按 P 建立），確保角色頭上有顯示紅色隊伍血條。\n\n"
        "這條血條是用來偵測角色實際位置的依據，沒有顯示的話攻擊方向判斷會退回"
        "「假設角色永遠在畫面中間」的備援模式，準確度會下降。\n\n"
        "確認好之後按「確定」繼續。",
        parent=root,
    )
    root.destroy()


# === 啟動設定視窗：這次要跑幾分鐘 ===
def ask_runtime_minutes(default_minutes):
    result = {"minutes": default_minutes}

    root = tk.Tk()
    root.title("設定執行時間")
    root.attributes("-topmost", True)

    tk.Label(root, text="這次要執行幾分鐘？", padx=20, pady=10).pack()

    entry_var = tk.StringVar(value=str(default_minutes))
    entry = tk.Entry(root, textvariable=entry_var, justify="center", font=("Arial", 14))
    entry.pack(padx=20, pady=5)
    entry.focus()
    entry.select_range(0, "end")

    error_label = tk.Label(root, text="", fg="red")
    error_label.pack()

    def on_start():
        try:
            minutes = float(entry_var.get())
            if minutes <= 0:
                raise ValueError
        except ValueError:
            error_label.config(text="請輸入大於 0 的數字")
            return
        result["minutes"] = minutes
        root.destroy()

    tk.Button(root, text="開始", command=on_start, width=12, height=2).pack(pady=10)
    root.bind("<Return>", lambda e: on_start())
    root.protocol("WM_DELETE_WINDOW", on_start)  # 直接關窗也視為用預設值開始

    root.mainloop()
    return result["minutes"]


# === 視窗 / 截圖 ===
def bring_window_to_front(title):
    hwnd = win32gui.FindWindow(None, title)
    if hwnd:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return hwnd
    else:
        raise Exception(f"❌ 找不到視窗：{title}")
def get_window_rect(title_keyword):
    hwnd = win32gui.FindWindow(None, title_keyword)  # 標題需完全符合
    if hwnd == 0:
        raise Exception("找不到視窗")
    rect = win32gui.GetWindowRect(hwnd)  # (left, top, right, bottom)
    return hwnd, rect

def click_relative_to_window(title, rel_x, rel_y):
    hwnd = win32gui.FindWindow(None, title)
    if hwnd == 0:
        raise Exception("找不到視窗")

    # 還原並帶到前景
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.3)  # 給視窗一點時間反應

    # 取得客戶區域在螢幕上的絕對座標
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    screen_left, screen_top = win32gui.ClientToScreen(hwnd, (left, top))

    abs_x = screen_left + rel_x
    abs_y = screen_top + rel_y

    pyautogui.click(x=abs_x, y=abs_y)


def focus_window():
    win32gui.SetForegroundWindow(hwnd)


def get_abs_region(hwnd, rel_region):
    rel_x, rel_y, w, h = rel_region
    abs_x, abs_y = win32gui.ClientToScreen(hwnd, (rel_x, rel_y))
    return (abs_x, abs_y, w, h)


def capture_region(hwnd, rel_region):
    abs_region = get_abs_region(hwnd, rel_region)
    screenshot = pyautogui.screenshot(region=abs_region)
    return cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)


# === 緊急中斷＋連續截圖（除錯用）===
hwnd = None  # 遊戲視窗還沒抓到前，先給個預設值，避免熱鍵在那之前被按會噴錯


def capture_incident_screenshots():
    if hwnd is None:
        print("❌ 遊戲視窗還沒抓到，無法截圖")
        return

    os.makedirs(incident_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for i in range(1, incident_screenshot_count + 1):
        try:
            frame = capture_region(hwnd, game_view_region)
            path = os.path.join(incident_dir, f"incident_{timestamp}_{i}.png")
            cv2.imwrite(path, frame)
            print(f"📸 已存第 {i} 張：{os.path.basename(path)}")
        except Exception as e:
            print(f"❌ 第 {i} 張截圖失敗：{e}")
        if i < incident_screenshot_count:
            time.sleep(incident_screenshot_interval)


def trigger_incident_capture():
    print(f"🆘 偵測到 {incident_capture_key.upper()}，緊急停止腳本並連續截圖...")
    stop_event.set()
    capture_incident_screenshots()


keyboard.add_hotkey(incident_capture_key, trigger_incident_capture, suppress=True)


def apply_clahe(image_bgr):
    """對亮度通道做對比限制自適應直方圖均衡化，減緩霧氣造成的對比度下降。"""
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l_eq = clahe_processor.apply(l)
    lab_eq = cv2.merge((l_eq, a, b))
    return cv2.cvtColor(lab_eq, cv2.COLOR_LAB2BGR)


# === 角色定位：用顏色遮罩找隊伍血條，取代「假設角色永遠在畫面中間」===
def compute_hsv_bounds_from_sample(sample_bgr, margin, s_min=180, v_min=150):
    """從一張顏色固定的樣本圖，自動算出 HSV 上下限。

    樣本裁圖邊緣常會混到黑色外框、背景等雜訊像素，直接統計全部像素會讓範圍失真，
    所以先篩掉不夠飽和/不夠亮的像素，只用剩下的核心顏色像素算中位數。
    """
    hsv = cv2.cvtColor(sample_bgr, cv2.COLOR_BGR2HSV).reshape(-1, 3)
    core_mask = (hsv[:, 1] >= s_min) & (hsv[:, 2] >= v_min)
    core_pixels = hsv[core_mask]
    if len(core_pixels) == 0:
        core_pixels = hsv  # 萬一篩過頭全部濾掉了，退回用全部像素

    med = np.median(core_pixels, axis=0)
    h_margin, s_margin, v_margin = margin

    lower = np.array([
        max(int(med[0]) - h_margin, 0),
        max(int(med[1]) - s_margin, 0),
        max(int(med[2]) - v_margin, 0),
    ])
    upper = np.array([
        min(int(med[0]) + h_margin, 179),
        min(int(med[1]) + s_margin, 255),
        min(int(med[2]) + v_margin, 255),
    ])
    return lower, upper


def load_character_hsv_bounds():
    sample = cv2.imread(team_blood_template_path, cv2.IMREAD_COLOR)
    if sample is None:
        raise ValueError(f"❌ 找不到隊伍血條樣本圖：{team_blood_template_path}")
    return compute_hsv_bounds_from_sample(sample, character_hsv_margin)


def find_character_position_in_frame(frame_raw, lower_hsv, upper_hsv):
    """在畫面裡找隊伍血條顏色的色塊，回傳 (x, y) 中心座標；血條長度變化不影響偵測。

    排除畫面最下面的 UI 區域（HP/MP/快捷鍵條也是紅色，且比隊伍血條大很多，
    不排除的話「挑最大色塊」永遠會選到那個固定不動的 UI 血條），
    並過濾掉寬度異常大的色塊，避免抓錯目標。
    """
    search_h = max(frame_raw.shape[0] - character_exclude_bottom_px, 1)
    hsv = cv2.cvtColor(frame_raw[:search_h], cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, lower_hsv, upper_hsv)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    candidates = [c for c in contours if cv2.boundingRect(c)[2] <= character_max_width]
    if not candidates:
        return None

    largest = max(candidates, key=cv2.contourArea)
    if cv2.contourArea(largest) < character_min_area:
        return None

    x, y, w, h = cv2.boundingRect(largest)
    return x + w / 2, y + h / 2


# === 怪物偵測 ===
def load_monster_templates():
    paths = sorted(glob.glob(monster_template_glob))
    if not paths:
        raise FileNotFoundError(f"❌ 找不到怪物樣板圖：{monster_template_glob}")

    templates = []
    for p in paths:
        img = cv2.imread(p, cv2.IMREAD_COLOR)
        if img is None:
            continue
        img = apply_clahe(img)
        templates.append(img)
        templates.append(cv2.flip(img, 1))  # 自動產生鏡像版本，處理朝左/朝右兩種朝向
    return templates


def find_local_maxima(result, template_hw, threshold):
    """在整張比對結果矩陣裡找出所有超過門檻值的位置，並用簡單的非極大值抑制
    (NMS) 去掉同一個目標附近重複的候選點，只留下每個目標分數最高的那個。"""
    th, tw = template_hw
    ys, xs = np.where(result >= threshold)
    candidates = sorted(
        zip(result[ys, xs].tolist(), xs.tolist(), ys.tolist()), key=lambda c: -c[0]
    )

    kept = []
    for score, x, y in candidates:
        too_close = any(
            abs(x - kx) < tw * 0.5 and abs(y - ky) < th * 0.5 for _, kx, ky in kept
        )
        if not too_close:
            kept.append((score, x, y))
    return kept


def find_template_boxes(frame_raw, templates, threshold):
    """在給定的一張畫面上找出所有超過門檻值的樣板比對框（跨樣板/鏡像去重複後）。"""
    frame = apply_clahe(frame_raw)

    all_boxes = []  # (score, x, y, w, h)
    for template in templates:
        th, tw = template.shape[:2]
        if th > frame.shape[0] or tw > frame.shape[1]:
            continue
        result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
        for score, x, y in find_local_maxima(result, (th, tw), threshold):
            all_boxes.append((score, x, y, tw, th))

    # 不同樣板/鏡像版本可能比對到同一個目標，跨樣板再做一次 NMS 避免重複計算
    all_boxes.sort(key=lambda b: -b[0])
    final_boxes = []
    for score, x, y, w, h in all_boxes:
        cx, cy = x + w / 2, y + h / 2
        too_close = False
        for _, fx, fy, fw, fh in final_boxes:
            fcx, fcy = fx + fw / 2, fy + fh / 2
            if abs(cx - fcx) < max(w, fw) * 0.5 and abs(cy - fcy) < max(h, fh) * 0.5:
                too_close = True
                break
        if not too_close:
            final_boxes.append((score, x, y, w, h))

    return final_boxes


def find_monster_boxes(frame_raw, templates):
    """在給定的一張畫面上找出所有偵測到的兔子框。"""
    return find_template_boxes(frame_raw, templates, monster_match_threshold)


# === 小地圖黃點偵測 ===
def load_yellow_dot_template():
    template = cv2.imread(yellow_dot_template_path, cv2.IMREAD_COLOR)
    if template is None:
        raise ValueError(f"❌ 找不到黃點樣板圖：{yellow_dot_template_path}")
    return template


def find_dot_position_in_frame(minimap_img, template):
    """回傳黃點在小地圖裁圖裡的 (x, y) 中心座標；找不到回傳 None。"""
    result = cv2.matchTemplate(minimap_img, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < dot_match_threshold:
        return None
    th, tw = template.shape[:2]
    return max_loc[0] + tw // 2, max_loc[1] + th // 2


# === 小地圖紅點偵測（其他玩家）===
def load_red_dot_template():
    template = cv2.imread(red_dot_template_path, cv2.IMREAD_COLOR)
    if template is None:
        raise ValueError(f"❌ 找不到紅點樣板圖：{red_dot_template_path}")
    return template


def find_red_dot_present_in_frame(minimap_img, template):
    result = cv2.matchTemplate(minimap_img, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(result)
    return max_val >= red_dot_match_threshold


# === 即時視覺化除錯畫面 ===
def draw_game_debug_frame(frame_raw, monster_boxes, character_x):
    img = frame_raw.copy()
    center_x = character_x if character_x is not None else screen_center_x

    for score, x, y, w, h in monster_boxes:
        box_center_x = x + w / 2
        in_range = abs(box_center_x - center_x) <= monster_attack_range_x
        color = (0, 200, 0) if in_range else (0, 0, 200)  # 範圍內綠框，範圍外暗紅框（不會被拿去攻擊）
        cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
        cv2.putText(
            img, f"{score:.2f}", (x, max(y - 5, 12)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA,
        )

    # 攻擊範圍框：橘色兩條線，範圍內的兔子才會被拿去計算攻擊方向
    range_left = int(center_x - monster_attack_range_x)
    range_right = int(center_x + monster_attack_range_x)
    cv2.line(img, (range_left, 0), (range_left, img.shape[0]), (0, 128, 255), 2)
    cv2.line(img, (range_right, 0), (range_right, img.shape[0]), (0, 128, 255), 2)

    if character_x is not None:
        # 實際偵測到的角色位置：紅線
        cv2.line(img, (int(character_x), 0), (int(character_x), img.shape[0]), (0, 0, 255), 2)
    else:
        # 偵測不到時的備援中心點：黃線
        cv2.line(img, (screen_center_x, 0), (screen_center_x, img.shape[0]), (0, 255, 255), 1)
    return img


def draw_minimap_debug_frame(minimap_img, dot_x, dot_y, other_player_nearby):
    img = minimap_img.copy()
    h, w = img.shape[:2]
    left_bound = int(w * minimap_left_ratio)
    right_bound = int(w * minimap_right_ratio)
    cv2.line(img, (left_bound, 0), (left_bound, h), (0, 255, 255), 1)
    cv2.line(img, (right_bound, 0), (right_bound, h), (0, 255, 255), 1)
    if dot_x is not None and dot_y is not None:
        cv2.circle(img, (int(dot_x), int(dot_y)), 4, (0, 255, 0), -1)
    if other_player_nearby:
        cv2.putText(img, "PLAYER!", (2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1, cv2.LINE_AA)
    return cv2.resize(img, (w * 2, h * 2), interpolation=cv2.INTER_NEAREST)


def combine_debug_frame(game_debug_frame, minimap_debug_frame):
    """把小地圖除錯畫面疊在遊戲畫面右上角，合併成一個視窗，不用同時開兩個。"""
    combined = game_debug_frame.copy()
    mh, mw = minimap_debug_frame.shape[:2]
    gh, gw = combined.shape[:2]
    mw, mh = min(mw, gw), min(mh, gh)
    combined[0:mh, gw - mw:gw] = minimap_debug_frame[0:mh, 0:mw]
    return combined


# === 共用狀態：背景偵測執行緒寫入，主執行緒讀取 ===
class DetectionState:
    def __init__(self):
        self.lock = threading.Lock()
        self.monster_positions = []  # [(x, y), ...]
        self.dot_x = None
        self.dot_y = None
        self.other_player_nearby = False
        self.character_x = None
        self.character_y = None
        self.debug_frame = None

    def update(self, monster_positions, dot_x, dot_y, other_player_nearby, character_x, character_y, debug_frame):
        with self.lock:
            self.monster_positions = monster_positions
            self.dot_x = dot_x
            self.dot_y = dot_y
            self.other_player_nearby = other_player_nearby
            self.character_x = character_x
            self.character_y = character_y
            self.debug_frame = debug_frame

    def read(self):
        with self.lock:
            return (
                self.monster_positions, self.dot_x, self.dot_y, self.other_player_nearby,
                self.character_x, self.character_y,
            )

    def read_debug_frame(self):
        with self.lock:
            return self.debug_frame


def detection_loop(hwnd, monster_templates, yellow_dot_template, red_dot_template, character_hsv_bounds, state):
    lower_hsv, upper_hsv = character_hsv_bounds
    while not stop_event.is_set():
        try:
            pyautogui.failSafeCheck()  # screenshot() 本身不會檢查角落，要自己主動呼叫
            game_frame = capture_region(hwnd, game_view_region)
            minimap_frame = capture_region(hwnd, minimap_region)

            character_pos = find_character_position_in_frame(game_frame, lower_hsv, upper_hsv)
            character_x, character_y = character_pos if character_pos is not None else (None, None)
            monster_boxes = find_monster_boxes(game_frame, monster_templates)
            monster_positions = [(x + w / 2, y + h / 2) for _, x, y, w, h in monster_boxes]
            dot_pos = find_dot_position_in_frame(minimap_frame, yellow_dot_template)
            dot_x, dot_y = dot_pos if dot_pos is not None else (None, None)
            other_player_nearby = find_red_dot_present_in_frame(minimap_frame, red_dot_template)

            debug_frame = None
            if debug_window_enabled:
                game_debug_frame = draw_game_debug_frame(game_frame, monster_boxes, character_x)
                minimap_debug_frame = draw_minimap_debug_frame(minimap_frame, dot_x, dot_y, other_player_nearby)
                debug_frame = combine_debug_frame(game_debug_frame, minimap_debug_frame)
        except pyautogui.FailSafeException:
            print("🛑 滑鼠移到螢幕角落，觸發緊急停止")
            stop_event.set()
            return
        state.update(monster_positions, dot_x, dot_y, other_player_nearby, character_x, character_y, debug_frame)
        stop_event.wait(detection_interval)


# === 鍵盤控制（直接用 pyautogui，不再透過 AutoHotkey）===
def hold_key(key, hold_ms):
    pyautogui.keyDown(key)
    time.sleep(hold_ms / 1000)
    pyautogui.keyUp(key)


def direction_key(direction):
    return DIRECTION_KEYS[direction]


def walk(direction, hold_ms):
    focus_window()
    hold_key(direction_key(direction), hold_ms)


def walk_and_jump(direction, total_ms):
    focus_window()
    key = direction_key(direction)
    pyautogui.keyDown(key)
    time.sleep(0.1)
    pyautogui.keyDown(KEY_JUMP)
    time.sleep(0.1)
    pyautogui.keyUp(KEY_JUMP)
    time.sleep(max(total_ms - 200, 0) / 1000)
    pyautogui.keyUp(key)


def teleport(direction, hold_ms):
    focus_window()
    key = direction_key(direction)
    pyautogui.keyDown(KEY_TELEPORT)
    time.sleep(hold_ms / 1000)
    pyautogui.keyDown(key)
    time.sleep(hold_ms / 1000)
    pyautogui.keyUp(KEY_TELEPORT)
    time.sleep(0.1)
    pyautogui.keyUp(key)


# === 朝小地圖上的目標座標移動，一步步靠近（撿錢路線重播前，先走到路線起點用）===
def navigate_to_point(hwnd, yellow_dot_template, target_x):
    """先強制往下瞬移到地平線，再單純用水平移動修正到目標 x，不用一直檢查 y 座標。"""
    for _ in range(drop_to_ground_attempts):
        teleport("down", navigate_teleport_ms)

    for attempt in range(1, navigate_max_attempts + 1):
        minimap_frame = capture_region(hwnd, minimap_region)
        dot_pos = find_dot_position_in_frame(minimap_frame, yellow_dot_template)
        if dot_pos is None:
            print(f"⚠ [移動到起點 {attempt}/{navigate_max_attempts}] 小地圖上找不到黃點，放棄這次移動")
            return False

        dot_x, _ = dot_pos
        dx = target_x - dot_x

        if abs(dx) <= navigate_tolerance:
            print(f"✅ 已抵達路線起點附近（黃點 x={dot_x}）")
            return True

        direction = "right" if dx > 0 else "left"
        walk(direction, navigate_walk_ms)

    print(f"⚠ 移動到起點失敗（已達 {navigate_max_attempts} 次嘗試上限）")
    return False


# === 撿錢路線：載入 route_recorder.py 錄好的路線，走到起點後重播按鍵時間軸 ===
def load_pickup_routes():
    paths = sorted(glob.glob(os.path.join(pickup_routes_dir, "route_*.json")))
    if not paths:
        print(f"ℹ 找不到撿錢路線檔案（{pickup_routes_dir}/route_*.json），撿錢路線模式將會停用")
        return []

    routes = []
    for p in paths:
        try:
            with open(p, "r", encoding="utf-8") as f:
                route = json.load(f)
            route["_name"] = os.path.basename(p)
            routes.append(route)
        except (OSError, json.JSONDecodeError) as e:
            print(f"⚠ 讀取路線檔案失敗：{p}（{e}）")
    return routes


def play_route(route):
    focus_window()
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

    # 保險：錄製萬一漏記某個放開事件，重播完強制全部放開，避免按鍵卡住
    for key in (KEY_LEFT, KEY_RIGHT, KEY_UP, KEY_DOWN, KEY_TELEPORT):
        pyautogui.keyUp(key)


def run_pickup_route(hwnd, yellow_dot_template, routes):
    if not routes:
        return

    route = random.choice(routes)
    print(f"🗺 挑選撿錢路線：{route.get('_name', '?')}，先移動到起點")

    reached = navigate_to_point(hwnd, yellow_dot_template, route["start_x"])
    if not reached:
        print("⚠ 沒能走到路線起點，跳過這次撿錢路線")
        return

    print("▶ 開始重播撿錢路線")
    play_route(route)
    print("✅ 撿錢路線執行完畢")


def attack_in_direction(direction):
    """direction: 'Left' / 'Right' / 'None'"""
    if direction != "None":
        key = direction_key(direction.lower())
        pyautogui.keyDown(key)
        time.sleep(T["attack_direction_hold_ms"] / 1000)
        pyautogui.keyDown(KEY_TELEPORT)
        time.sleep(T["attack_teleport_hold_ms"] / 1000)
        pyautogui.keyUp(KEY_TELEPORT)
        time.sleep(0.1)
        pyautogui.keyUp(key)
        time.sleep(0.1)

    hold_key(KEY_ATTACK, T["attack_cast_hold_ms"])

    if random.random() < T["attack_double_cast_probability"]:
        time.sleep(0.12)
        hold_key(KEY_ATTACK, T["attack_cast_hold_ms"])


def attack_toward(direction):
    print(f"⚔ 發現兔子，攻擊方向：{direction}")
    focus_window()
    attack_in_direction(direction)

    # 反方向補刀只用在左右（上下沒有明確定義的「反方向」，交給真實偵測決定）
    if direction in ("Left", "Right") and random.random() < T["attack_opposite_probability"]:
        opposite = "Right" if direction == "Left" else "Left"
        time.sleep(0.15)
        attack_in_direction(opposite)


# === 位置校正（用背景執行緒最新算好的 dot_x，不用重新截圖）===
def correct_position(dot_x):
    if dot_x is None:
        print("⚠ 小地圖上找不到黃點，略過位置校正")
        return

    minimap_w = minimap_region[2]
    left_bound = minimap_w * minimap_left_ratio
    right_bound = minimap_w * minimap_right_ratio

    if dot_x < left_bound:
        direction = "right"
    elif dot_x > right_bound:
        direction = "left"
    else:
        print(f"✅ 黃點 x={dot_x} 在範圍內（{left_bound:.0f}~{right_bound:.0f}），不校正")
        return

    mode = random.choice(["walk", "teleport"])
    print(f"↩ 黃點 x={dot_x}（邊界 {left_bound:.0f}~{right_bound:.0f}）太{'左' if direction == 'right' else '右'}，"
          f"用「{mode}」模式往{direction}校正")

    if mode == "walk":
        walk(direction, T["correct_walk_ms"])
    else:
        teleport(direction, T["correct_teleport_ms"])


# === 遊走 ===
def wander():
    if random.random() < walk_probability:
        mode = random.choices(
            list(walk_mode_weights.keys()), weights=list(walk_mode_weights.values())
        )[0]
        direction = random.choice(["left", "right"])

        if mode == "jump":
            print("🦘 跳躍移動找怪")
            walk_and_jump(direction, T["jump_walk_total_ms"])
        elif mode == "long":
            print("🚶‍♂️ 走比較長一段找怪")
            walk(direction, T["walk_long_ms"])
        else:
            print("🚶 隨機走動找怪")
            walk(direction, T["walk_short_ms"])
    else:
        print("🧍 原地停頓")
        time.sleep(random.uniform(0.5, 1.5))


# === 顯示即時視覺化除錯視窗（只能在主執行緒呼叫，OpenCV 的視窗不是每個後端都支援多執行緒）===
DEBUG_WINDOW_NAME = "main_v6 - 除錯畫面"
_debug_window_ready = False


def show_debug_windows(state):
    global _debug_window_ready
    if not debug_window_enabled:
        return

    debug_frame = state.read_debug_frame()
    if debug_frame is None:
        return

    if not _debug_window_ready:
        cv2.namedWindow(DEBUG_WINDOW_NAME, cv2.WINDOW_NORMAL)
        h, w = debug_frame.shape[:2]
        cv2.resizeWindow(DEBUG_WINDOW_NAME, int(w * debug_window_scale), int(h * debug_window_scale))

        # 強制擺在遊戲視窗右邊，避免疊在遊戲視窗上面，截圖時截到除錯視窗自己
        try:
            game_left, game_top, game_right, _ = win32gui.GetWindowRect(hwnd)
            cv2.moveWindow(DEBUG_WINDOW_NAME, game_right + 10, game_top)
        except Exception:
            pass  # 拿不到視窗座標就算了，用預設位置
        _debug_window_ready = True

    cv2.imshow(DEBUG_WINDOW_NAME, debug_frame)
    cv2.waitKey(1)


def wait_with_debug(seconds, state):
    """取代單純的 stop_event.wait()，等待的同時持續刷新除錯視窗，避免視窗卡住沒反應。"""
    end_time = time.time() + seconds
    while time.time() < end_time and not stop_event.is_set():
        show_debug_windows(state)
        time.sleep(0.05)


# === 主邏輯 ===
monster_templates = load_monster_templates()
print(f"📂 讀到怪物樣板圖（含自動鏡像共 {len(monster_templates)} 個比對版本）")

yellow_dot_template = load_yellow_dot_template()
red_dot_template = load_red_dot_template()
character_hsv_bounds = load_character_hsv_bounds()
print(f"🎨 角色定位顏色範圍（HSV）：{character_hsv_bounds[0]} ~ {character_hsv_bounds[1]}")

pickup_routes = load_pickup_routes()
if pickup_routes:
    print(f"🗺 讀到 {len(pickup_routes)} 條撿錢路線")

show_party_reminder()
runtime_minutes = ask_runtime_minutes(default_runtime_minutes)

hwnd = bring_window_to_front(window_title)

detection_state = DetectionState()
detection_thread = threading.Thread(
    target=detection_loop,
    args=(hwnd, monster_templates, yellow_dot_template, red_dot_template, character_hsv_bounds, detection_state),
    daemon=True,
)
detection_thread.start()

print(f"🎮 物件追蹤打寶腳本啟動中（ESC 離開，本次設定執行 {runtime_minutes:.0f} 分鐘後自動停止）")
print("⌨ 直接用 pyautogui 控制鍵盤，不再透過 AutoHotkey")

step_count = 0
start_time = time.time()
was_paused_for_player = False
attacks_since_pickup = 0
attacks_until_pickup = random.randint(*attacks_before_pickup_range)

while not stop_event.is_set():
    elapsed_minutes = (time.time() - start_time) / 60
    if elapsed_minutes >= runtime_minutes:
        print(f"⏰ 已執行 {elapsed_minutes:.1f} 分鐘，達到本次設定上限，停止動作")
        print(f"⏳ {stop_grace_seconds} 秒後程式自動結束，你將自行進自由市場")
        play_alert_sound()
        time.sleep(20)
        click_relative_to_window(window_title, 961, 700)
        time.sleep(2)
        click_relative_to_window(window_title, 961, 700)
        stop_event.wait(stop_grace_seconds)
        stop_event.set()
        break

    _, _, _, other_player_nearby, _, _ = detection_state.read()
    if other_player_nearby:
        if not was_paused_for_player:
            print("🚨 小地圖偵測到其他玩家，暫停動作！請自行判斷後續處理")
            play_alert_sound()
            was_paused_for_player = True
        wait_with_debug(1.0, detection_state)
        continue
    elif was_paused_for_player:
        print("✅ 其他玩家已離開小地圖範圍，恢復動作")
        was_paused_for_player = False

    step_count += 1
    print(f"[{step_count}]")

    try:
        monster_positions, dot_x, dot_y, _, character_x, character_y = detection_state.read()
        center_x = character_x if character_x is not None else screen_center_x
        center_y = character_y if character_y is not None else screen_center_y
        in_range_monsters = [(x, y) for x, y in monster_positions if abs(x - center_x) <= monster_attack_range_x]

        if in_range_monsters and random.random() >= ignore_monster_probability:
            left_count = sum(1 for x, y in in_range_monsters if x < center_x)
            right_count = sum(1 for x, y in in_range_monsters if x > center_x)
            print(
                f"🐰 範圍內 {len(in_range_monsters)} 隻兔子（左 {left_count} / 右 {right_count}，"
                f"畫面上共看到 {len(monster_positions)} 隻），角色位置 x={center_x:.0f}"
                f"{'（真實偵測）' if character_x is not None else '（備援中心點）'}"
            )

            if left_count > right_count:
                attack_toward("Left")
            elif right_count > left_count:
                attack_toward("Right")
            else:
                # 左右平手，改看有沒有兔子明顯偏上/偏下
                up_count = sum(1 for x, y in in_range_monsters if y < center_y - vertical_deadzone_y)
                down_count = sum(1 for x, y in in_range_monsters if y > center_y + vertical_deadzone_y)
                print(f"   左右平手，改看上下（上 {up_count} / 下 {down_count}）")

                if up_count > down_count:
                    attack_toward("Up")
                elif down_count > up_count:
                    attack_toward("Down")
                else:
                    attack_toward("None")

            attacks_since_pickup += 1
            print(f"   已累積攻擊 {attacks_since_pickup}/{attacks_until_pickup} 次才會進撿錢模式")
            if attacks_since_pickup >= attacks_until_pickup:
                run_pickup_route(hwnd, yellow_dot_template, pickup_routes)
                attacks_since_pickup = 0
                attacks_until_pickup = random.randint(*attacks_before_pickup_range)
        else:
            if in_range_monsters:
                print("🙃 範圍內有看到兔子，但這次隨機決定不理它")
            wander()

        _, latest_dot_x, _, _, _, _ = detection_state.read()
        correct_position(latest_dot_x)
    except pyautogui.FailSafeException:
        print("🛑 滑鼠移到螢幕角落，觸發緊急停止")
        stop_event.set()
        break

    wait_with_debug(random.uniform(*loop_delay_range), detection_state)

cv2.destroyAllWindows()

print("👋 腳本已結束")

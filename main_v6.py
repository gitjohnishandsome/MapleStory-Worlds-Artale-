import threading
import time
import random
import glob
import os
import sys
import winsound
import tkinter as tk
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
screen_center_x = game_view_region[2] // 2  # 假設角色永遠在畫面正中間

monster_template_glob = os.path.join(data_location, config["monster_detection"]["template_glob"])
monster_match_threshold = config["monster_detection"]["match_threshold"]

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

# 霧氣導致對比度下降時，用 CLAHE 拉回對比，讓樣板比對比較不受影響
clahe_processor = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

# pyautogui 內建安全機制：滑鼠移到螢幕角落會拋出 FailSafeException
pyautogui.FAILSAFE = enable_failsafe

stop_event = threading.Event()
keyboard.add_hotkey("esc", lambda: (print("🛑 偵測到 ESC，準備結束"), stop_event.set()))


# === 警示音 ===
def play_alert_sound():
    """比系統提示音更明顯：連續三聲高音蜂鳴警報。"""
    for _ in range(3):
        winsound.Beep(1200, 300)
        time.sleep(0.1)


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


def apply_clahe(image_bgr):
    """對亮度通道做對比限制自適應直方圖均衡化，減緩霧氣造成的對比度下降。"""
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l_eq = clahe_processor.apply(l)
    lab_eq = cv2.merge((l_eq, a, b))
    return cv2.cvtColor(lab_eq, cv2.COLOR_LAB2BGR)


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


def find_monster_positions(hwnd, templates):
    """回傳畫面上所有偵測到的兔子中心 x 座標（跨樣板/鏡像去重複後的清單）。"""
    frame = apply_clahe(capture_region(hwnd, game_view_region))

    all_boxes = []  # (score, x, y, w, h)
    for template in templates:
        th, tw = template.shape[:2]
        if th > frame.shape[0] or tw > frame.shape[1]:
            continue
        result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
        for score, x, y in find_local_maxima(result, (th, tw), monster_match_threshold):
            all_boxes.append((score, x, y, tw, th))

    # 不同樣板/鏡像版本可能比對到同一隻兔子，跨樣板再做一次 NMS 避免重複計算
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

    return [x + w / 2 for _, x, y, w, h in final_boxes]


# === 小地圖黃點偵測 ===
def load_yellow_dot_template():
    template = cv2.imread(yellow_dot_template_path, cv2.IMREAD_COLOR)
    if template is None:
        raise ValueError(f"❌ 找不到黃點樣板圖：{yellow_dot_template_path}")
    return template


def find_dot_x(hwnd, template):
    minimap_img = capture_region(hwnd, minimap_region)
    result = cv2.matchTemplate(minimap_img, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < dot_match_threshold:
        return None
    th, tw = template.shape[:2]
    return max_loc[0] + tw // 2


# === 小地圖紅點偵測（其他玩家）===
def load_red_dot_template():
    template = cv2.imread(red_dot_template_path, cv2.IMREAD_COLOR)
    if template is None:
        raise ValueError(f"❌ 找不到紅點樣板圖：{red_dot_template_path}")
    return template


def find_red_dot_present(hwnd, template):
    minimap_img = capture_region(hwnd, minimap_region)
    result = cv2.matchTemplate(minimap_img, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(result)
    return max_val >= red_dot_match_threshold


# === 共用狀態：背景偵測執行緒寫入，主執行緒讀取 ===
class DetectionState:
    def __init__(self):
        self.lock = threading.Lock()
        self.monster_positions = []
        self.dot_x = None
        self.other_player_nearby = False

    def update(self, monster_positions, dot_x, other_player_nearby):
        with self.lock:
            self.monster_positions = monster_positions
            self.dot_x = dot_x
            self.other_player_nearby = other_player_nearby

    def read(self):
        with self.lock:
            return self.monster_positions, self.dot_x, self.other_player_nearby


def detection_loop(hwnd, monster_templates, yellow_dot_template, red_dot_template, state):
    while not stop_event.is_set():
        try:
            pyautogui.failSafeCheck()  # screenshot() 本身不會檢查角落，要自己主動呼叫
            monster_positions = find_monster_positions(hwnd, monster_templates)
            dot_x = find_dot_x(hwnd, yellow_dot_template)
            other_player_nearby = find_red_dot_present(hwnd, red_dot_template)
        except pyautogui.FailSafeException:
            print("🛑 滑鼠移到螢幕角落，觸發緊急停止")
            stop_event.set()
            return
        state.update(monster_positions, dot_x, other_player_nearby)
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

    if direction != "None" and random.random() < T["attack_opposite_probability"]:
        opposite = "Right" if direction == "Left" else "Left"
        time.sleep(0.15)
        attack_in_direction(opposite)

    # 額外機率補一次上/下瞬移攻擊，涵蓋小山丘之類的高低地形
    if random.random() < T["attack_up_probability"]:
        time.sleep(0.15)
        attack_in_direction("Up")

    if random.random() < T["attack_down_probability"]:
        time.sleep(0.15)
        attack_in_direction("Down")


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


# === 主邏輯 ===
monster_templates = load_monster_templates()
print(f"📂 讀到怪物樣板圖（含自動鏡像共 {len(monster_templates)} 個比對版本）")

yellow_dot_template = load_yellow_dot_template()
red_dot_template = load_red_dot_template()

runtime_minutes = ask_runtime_minutes(default_runtime_minutes)

hwnd = bring_window_to_front(window_title)

detection_state = DetectionState()
detection_thread = threading.Thread(
    target=detection_loop,
    args=(hwnd, monster_templates, yellow_dot_template, red_dot_template, detection_state),
    daemon=True,
)
detection_thread.start()

print(f"🎮 物件追蹤打寶腳本啟動中（ESC 離開，本次設定執行 {runtime_minutes:.0f} 分鐘後自動停止）")
print("⌨ 直接用 pyautogui 控制鍵盤，不再透過 AutoHotkey")

step_count = 0
start_time = time.time()
was_paused_for_player = False

while not stop_event.is_set():
    elapsed_minutes = (time.time() - start_time) / 60
    if elapsed_minutes >= runtime_minutes:
        print(f"⏰ 已執行 {elapsed_minutes:.1f} 分鐘，達到本次設定上限，停止動作")
        print(f"⏳ {stop_grace_seconds} 秒後程式自動結束，你將自行進自由市場")
        play_alert_sound()
        time.sleep(20)
        click_relative_to_window(window_title, 961, 772)
        time.sleep(2)
        click_relative_to_window(window_title, 961, 772)
        stop_event.wait(stop_grace_seconds)
        stop_event.set()
        break

    _, _, other_player_nearby = detection_state.read()
    if other_player_nearby:
        if not was_paused_for_player:
            print("🚨 小地圖偵測到其他玩家，暫停動作！請自行判斷後續處理")
            play_alert_sound()
            was_paused_for_player = True
        stop_event.wait(1.0)
        continue
    elif was_paused_for_player:
        print("✅ 其他玩家已離開小地圖範圍，恢復動作")
        was_paused_for_player = False

    step_count += 1
    print(f"[{step_count}]")

    try:
        monster_positions, dot_x, _ = detection_state.read()

        if monster_positions and random.random() >= ignore_monster_probability:
            left_count = sum(1 for x in monster_positions if x < screen_center_x)
            right_count = sum(1 for x in monster_positions if x > screen_center_x)
            print(f"🐰 偵測到 {len(monster_positions)} 隻兔子（左 {left_count} / 右 {right_count}）")

            if left_count > right_count:
                attack_toward("Left")
            elif right_count > left_count:
                attack_toward("Right")
            else:
                attack_toward("None")
        else:
            if monster_positions:
                print("🙃 有看到兔子，但這次隨機決定不理它")
            wander()

        _, latest_dot_x, _ = detection_state.read()
        correct_position(latest_dot_x)
    except pyautogui.FailSafeException:
        print("🛑 滑鼠移到螢幕角落，觸發緊急停止")
        stop_event.set()
        break

    stop_event.wait(random.uniform(*loop_delay_range))

print("👋 腳本已結束")

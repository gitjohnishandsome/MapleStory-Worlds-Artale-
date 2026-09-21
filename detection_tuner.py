"""
偵測門檻調整工具

用主程式（main_v6.py）同一套「CLAHE + 樣板比對」邏輯，
讓你載入任意截圖，即時看框選結果，並用滑桿調整門檻值。
"""

import os
import glob
import time
import tkinter as tk
from tkinter import filedialog, ttk

import cv2
import numpy as np
import pyautogui
import win32con
import win32gui
import yaml
from PIL import Image, ImageTk

data_location = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(data_location, "config.yaml"), "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

window_title = config["paths"]["window_title"]
game_view_region = tuple(config["game_view"]["region"])
minimap_region = tuple(config["minimap"]["region"])

clahe_processor = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))


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


def imread_unicode(path, flags=cv2.IMREAD_COLOR):
    """cv2.imread 在 Windows 上路徑含中文常會讀取失敗，改用這個方式繞過。"""
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


def apply_clahe(image_bgr):
    lab = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l_eq = clahe_processor.apply(l)
    lab_eq = cv2.merge((l_eq, a, b))
    return cv2.cvtColor(lab_eq, cv2.COLOR_LAB2BGR)


# 可切換的偵測目標，數值直接讀 config.yaml，跟 main_v6.py 保持一致
DETECTORS = {
    "怪物偵測 (monster_rabbit_*.png)": {
        "glob": os.path.join(data_location, config["monster_detection"]["template_glob"]),
        "mirror": True,
        "use_clahe": True,
        "default_threshold": config["monster_detection"]["match_threshold"],
        "capture_region": game_view_region,
    },
    "小地圖黃點 (yellow_dot.png)": {
        "glob": os.path.join(data_location, config["minimap"]["yellow_dot_template"]),
        "mirror": False,
        "use_clahe": False,
        "default_threshold": config["minimap"]["match_threshold"],
        "capture_region": minimap_region,
    },
    "小地圖紅點 (red_dot.png)": {
        "glob": os.path.join(data_location, config["minimap"]["red_dot_template"]),
        "mirror": False,
        "use_clahe": False,
        "default_threshold": config["minimap"]["red_dot_match_threshold"],
        "capture_region": minimap_region,
    },
}


class DetectionTunerApp:
    def __init__(self, root):
        self.root = root
        root.title("偵測門檻調整工具")

        self.image_bgr = None
        self.templates = []  # [(label, template_img), ...]
        self.tk_image = None

        control_frame = tk.Frame(root)
        control_frame.pack(side="top", fill="x", padx=8, pady=8)

        tk.Button(control_frame, text="開啟圖片...", command=self.open_image).pack(side="left")
        tk.Button(control_frame, text="從遊戲截圖", command=self.capture_from_game).pack(
            side="left", padx=(6, 0)
        )

        tk.Label(control_frame, text="   偵測目標：").pack(side="left")
        self.detector_var = tk.StringVar(value=list(DETECTORS.keys())[0])
        detector_menu = ttk.Combobox(
            control_frame, textvariable=self.detector_var,
            values=list(DETECTORS.keys()), state="readonly", width=28,
        )
        detector_menu.pack(side="left")
        detector_menu.bind("<<ComboboxSelected>>", lambda e: self.on_detector_changed())

        tk.Label(control_frame, text="   門檻值：").pack(side="left")
        self.threshold_var = tk.DoubleVar(
            value=DETECTORS[self.detector_var.get()]["default_threshold"]
        )
        self.threshold_scale = tk.Scale(
            control_frame, from_=0.0, to=1.0, resolution=0.01, orient="horizontal",
            length=300, variable=self.threshold_var, command=lambda v: self.redraw(),
        )
        self.threshold_scale.pack(side="left")

        self.threshold_readout = tk.Label(control_frame, text="", width=6)
        self.threshold_readout.pack(side="left", padx=4)

        self.image_label = tk.Label(root, text="請先開啟一張圖片", bg="#222", fg="#ccc")
        self.image_label.pack(side="top", padx=8, pady=8)

        self.result_text = tk.Text(root, height=10, width=90)
        self.result_text.pack(side="top", padx=8, pady=(0, 8))

        self.reload_templates()

    def on_detector_changed(self):
        spec = DETECTORS[self.detector_var.get()]
        self.threshold_var.set(spec["default_threshold"])
        self.reload_templates()

    def reload_templates(self):
        spec = DETECTORS[self.detector_var.get()]
        paths = sorted(glob.glob(spec["glob"]))
        self.templates = []
        for p in paths:
            img = imread_unicode(p)
            if img is None:
                continue
            if spec["use_clahe"]:
                img = apply_clahe(img)
            label = os.path.basename(p)
            self.templates.append((label, img))
            if spec["mirror"]:
                self.templates.append((label + "（鏡像）", cv2.flip(img, 1)))

        if not self.templates:
            self.result_text.delete("1.0", "end")
            self.result_text.insert("end", f"❌ 找不到樣板圖：{spec['glob']}\n")

        self.redraw()

    def open_image(self):
        path = filedialog.askopenfilename(
            title="選擇要測試的圖片",
            filetypes=[("圖片檔", "*.png;*.jpg;*.jpeg;*.bmp"), ("全部檔案", "*.*")],
        )
        if not path:
            return
        img = imread_unicode(path)
        if img is None:
            self.result_text.delete("1.0", "end")
            self.result_text.insert("end", f"❌ 無法讀取圖片：{path}\n")
            return
        self.image_bgr = img
        self.redraw()

    def capture_from_game(self):
        hwnd = win32gui.FindWindow(None, window_title)
        if not hwnd:
            self.result_text.delete("1.0", "end")
            self.result_text.insert("end", f"❌ 找不到遊戲視窗：{window_title}\n")
            return

        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.3)

        # 抓目前選的偵測目標對應的區域：怪物用整個遊戲畫面，黃點/紅點只抓小地圖
        spec = DETECTORS[self.detector_var.get()]
        rel_x, rel_y, w, h = spec["capture_region"]
        abs_x, abs_y = win32gui.ClientToScreen(hwnd, (rel_x, rel_y))
        screenshot = pyautogui.screenshot(region=(abs_x, abs_y, w, h))
        self.image_bgr = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
        self.redraw()

    def redraw(self, *_):
        threshold = self.threshold_var.get()
        self.threshold_readout.config(text=f"{threshold:.2f}")

        if self.image_bgr is None or not self.templates:
            return

        spec = DETECTORS[self.detector_var.get()]
        frame = apply_clahe(self.image_bgr) if spec["use_clahe"] else self.image_bgr
        display_img = self.image_bgr.copy()

        per_template_best = []  # [(label, best_val_or_None), ...] 用於下方報告列表
        all_boxes = []  # [(score, x, y, w, h, label), ...] 這張樣板所有超過門檻的候選框

        for label, template in self.templates:
            th, tw = template.shape[:2]
            if th > frame.shape[0] or tw > frame.shape[1]:
                per_template_best.append((label, None))
                continue

            result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
            per_template_best.append((label, float(result.max())))

            for score, x, y in find_local_maxima(result, (th, tw), threshold):
                all_boxes.append((score, x, y, tw, th, label))

        # 不同樣板/鏡像版本可能都比對到同一隻真的兔子，跨樣板再做一次 NMS 避免重複畫框
        all_boxes.sort(key=lambda b: -b[0])
        final_boxes = []
        for score, x, y, w, h, label in all_boxes:
            cx, cy = x + w / 2, y + h / 2
            too_close = False
            for _, fx, fy, fw, fh, _ in final_boxes:
                fcx, fcy = fx + fw / 2, fy + fh / 2
                if abs(cx - fcx) < max(w, fw) * 0.5 and abs(cy - fcy) < max(h, fh) * 0.5:
                    too_close = True
                    break
            if not too_close:
                final_boxes.append((score, x, y, w, h, label))

        best_val = max((v for _, v in per_template_best if v is not None), default=0.0)

        for score, x, y, w, h, label in final_boxes:
            color = (0, 200, 0)
            cv2.rectangle(display_img, (x, y), (x + w, y + h), color, 2)
            cv2.putText(
                display_img, f"{score:.2f}", (x, max(y - 5, 12)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA,
            )

        self.result_text.delete("1.0", "end")
        self.result_text.insert(
            "end",
            f"目前門檻值：{threshold:.2f}　最高分數：{best_val:.3f}　"
            f"畫面上偵測到 {len(final_boxes)} 個符合門檻的位置\n",
        )
        self.result_text.insert("end", "-" * 70 + "\n")
        for label, val in sorted(per_template_best, key=lambda r: (r[1] is None, -(r[1] or 0))):
            if val is None:
                self.result_text.insert("end", f"{label}：樣板比圖片大，無法比對\n")
            else:
                mark = "✅ 通過" if val >= threshold else "❌ 未達標"
                self.result_text.insert("end", f"{label}：{val:.3f}　{mark}\n")

        rgb = cv2.cvtColor(display_img, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(rgb)
        max_w, max_h = 1000, 650
        scale = min(max_w / pil_img.width, max_h / pil_img.height, 1.0)
        if scale < 1.0:
            pil_img = pil_img.resize((int(pil_img.width * scale), int(pil_img.height * scale)))
        self.tk_image = ImageTk.PhotoImage(pil_img)
        self.image_label.config(image=self.tk_image, text="")


def main():
    root = tk.Tk()
    DetectionTunerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

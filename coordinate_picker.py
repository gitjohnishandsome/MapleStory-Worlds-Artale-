"""
圖片座標選取工具

開一張圖片，點擊圖片上的位置，會顯示該點在「原始圖片」裡的座標
（不是螢幕座標，是圖片本身的像素座標，點沒縮放過的圖跟顯示座標會一樣）。
"""

import os
import tkinter as tk
from tkinter import filedialog

from PIL import Image, ImageTk


class CoordinatePickerApp:
    def __init__(self, root):
        self.root = root
        root.title("圖片座標選取工具")

        self.pil_image = None
        self.tk_image = None
        self.scale = 1.0
        self.points = []

        control_frame = tk.Frame(root)
        control_frame.pack(side="top", fill="x", padx=8, pady=8)

        tk.Button(control_frame, text="開啟圖片...", command=self.open_image).pack(side="left")
        tk.Button(control_frame, text="清除標記", command=self.clear_points).pack(side="left", padx=8)

        self.info_label = tk.Label(control_frame, text="請先開啟圖片，然後點擊圖片上的位置", anchor="w")
        self.info_label.pack(side="left", padx=12)

        self.canvas = tk.Canvas(root, bg="#222222", width=800, height=500)
        self.canvas.pack(side="top", padx=8, pady=8)
        self.canvas.bind("<Button-1>", self.on_click)

        self.log_text = tk.Text(root, height=10, width=90)
        self.log_text.pack(side="top", padx=8, pady=(0, 8))

    def open_image(self):
        path = filedialog.askopenfilename(
            title="選擇圖片",
            filetypes=[("圖片檔", "*.png;*.jpg;*.jpeg;*.bmp"), ("全部檔案", "*.*")],
        )
        if not path:
            return

        try:
            self.pil_image = Image.open(path).convert("RGB")
        except Exception as e:
            self.info_label.config(text=f"❌ 無法開啟圖片：{e}")
            return

        self.points = []
        self.log_text.delete("1.0", "end")
        self.render_image()
        self.info_label.config(
            text=f"已載入：{os.path.basename(path)}"
            f"（原始尺寸 {self.pil_image.width} x {self.pil_image.height}）"
        )

    def render_image(self):
        max_w, max_h = 900, 600
        scale = min(max_w / self.pil_image.width, max_h / self.pil_image.height, 1.0)
        self.scale = scale

        display_img = self.pil_image
        if scale < 1.0:
            display_img = self.pil_image.resize(
                (int(self.pil_image.width * scale), int(self.pil_image.height * scale))
            )

        self.tk_image = ImageTk.PhotoImage(display_img)
        self.canvas.config(width=display_img.width, height=display_img.height)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_image)
        self._redraw_markers()

    def on_click(self, event):
        if self.pil_image is None:
            return

        orig_x = int(event.x / self.scale)
        orig_y = int(event.y / self.scale)
        orig_x = max(0, min(orig_x, self.pil_image.width - 1))
        orig_y = max(0, min(orig_y, self.pil_image.height - 1))

        self.points.append((orig_x, orig_y))

        text = f"座標：({orig_x}, {orig_y})　顯示縮放比例：{self.scale:.2f}"
        self.info_label.config(text=text)
        self.log_text.insert("end", f"第 {len(self.points)} 點：({orig_x}, {orig_y})\n")
        self.log_text.see("end")
        print(f"[{len(self.points)}] ({orig_x}, {orig_y})")

        self._draw_marker(event.x, event.y)

    def _draw_marker(self, cx, cy):
        r = 5
        self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline="red", width=2)
        self.canvas.create_line(cx - 8, cy, cx + 8, cy, fill="red")
        self.canvas.create_line(cx, cy - 8, cx, cy + 8, fill="red")

    def _redraw_markers(self):
        for orig_x, orig_y in self.points:
            self._draw_marker(orig_x * self.scale, orig_y * self.scale)

    def clear_points(self):
        self.points = []
        self.log_text.delete("1.0", "end")
        self.render_image()


def main():
    root = tk.Tk()
    CoordinatePickerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

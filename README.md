# MapleStory Worlds Artale 自動化腳本

用 OpenCV 樣板比對辨識怪物與小地圖狀態，`pyautogui` 直接控制鍵盤，自動打怪+移動+安全監控的腳本。

## 系統需求

- Windows（用到 `win32gui` / `win32con` 操作視窗）
- Python 3.9+

## 安裝

```bash
pip install -r requirements.txt
```

## 使用前準備

1. **設定視窗標題**：打開 [config.yaml](config.yaml)，把 `paths.window_title` 改成你遊戲視窗實際的標題文字
2. **裁樣板圖**，存進 `Image/` 資料夾：
   - `Image/monster_rabbit_1.png`、`_2.png`...（要打的怪物，可以裁多張不同姿勢/角度，檔名符合 `monster_rabbit_*.png` 即可，數量不限）
   - `Image/yellow_dot.png`：小地圖上代表自己角色的黃點
   - `Image/red_dot.png`：小地圖上代表其他玩家的紅點
3. **校正小地圖擷取範圍**：跑 `python calibrate_minimap.py`，調整檔案裡的 `minimap_region` 數字，直到輸出的紅框圖剛好框住小地圖
4. **微調比對門檻值**：跑 `python detection_tuner.py`，用「從遊戲截圖」或「開啟圖片」載入畫面，拖動門檻值滑桿，確認樣板抓得到、抓得準
5. 上面兩步驟量出來的數字，回頭填進 `config.yaml` 對應的欄位（`minimap.region`、`monster_detection.match_threshold`、`minimap.match_threshold`、`minimap.red_dot_match_threshold`）

## 執行

```bash
python main_v6.py
```

啟動後會先跳出一個小視窗，讓你設定這次要跑幾分鐘，確認後遊戲視窗會自動被抓到前景，開始偵測、攻擊、移動。

## 安全機制

- **ESC** 熱鍵隨時停止程式
- 滑鼠移到**螢幕任一角落**會觸發 FAILSAFE 緊急停止（`pyautogui` 內建機制）
- 小地圖偵測到**紅點**（其他玩家）時會**暫停動作**並響三聲警報音，後續怎麼處理由你自己判斷、手動操作，程式不會自動幫你做任何動作
- 執行時間到了會**暫停動作**、響警報音，等 20 秒（可調）才真正結束，留時間給你自己收尾
- 每次執行都會在 `logs/` 資料夾存一份帶時間戳記的紀錄檔，方便事後回溯

## 設定檔（config.yaml）說明

| 區塊 | 內容 |
|---|---|
| `paths` | 遊戲視窗標題 |
| `keys` | 移動/跳躍/瞬移/攻擊分別對應哪個鍵 |
| `timings` | 各動作按鍵按住的毫秒數、補刀/連段機率 |
| `game_view` | 主畫面擷取範圍、找怪判斷方向的中心點 |
| `monster_detection` | 怪物樣板路徑、比對門檻值 |
| `minimap` | 小地圖擷取範圍、黃點/紅點樣板與門檻值 |
| `behavior` | 遊走機率、找不到怪時的行為權重 |
| `safety` | 預設執行時數、結束緩衝秒數、FAILSAFE 開關 |
| `logging` | 執行紀錄是否存檔、存放資料夾 |

## 工具腳本

- **`calibrate_minimap.py`**：截圖 + 紅框標記，用來校正小地圖在遊戲視窗裡的精確座標
- **`coordinate_picker.py`**：載入任意圖片，點擊畫面回報該點在圖片裡的座標
- **`detection_tuner.py`**：GUI 介面，載入截圖（或直接從遊戲擷取）比對樣板，用滑桿即時調整門檻值、看框選結果
- **`遊戲視窗截圖.py`**：連續擷取遊戲視窗畫面，存成截圖方便裁樣板圖

## 免責聲明

本專案屬於遊戲自動化工具，僅供學習與研究用途。使用此類工具可能違反遊戲的服務條款，有帳號被停權或封鎖的風險，請自行評估並承擔使用後果。

# -*- encoding: utf-8 -*-

import cv2 as cv
import numpy as np
import time
import random
import string
import argparse
import glob
import os

# ========= 画面・表示 =========
W, H = 960, 540
FPS_TARGET = 60
LANES_Y = [120, 210, 300, 390]
FONT = cv.FONT_HERSHEY_SIMPLEX

# 皿サイズ・寿司スプライト
PLATE_W = 160
PLATE_H = 56
SUSHI_H = 100

# ========= ルール =========
LIVES_START = 5
POINT_CHAR = 10
COMBO_BONUS_RATE = 0.08

# ========= サウンド =========
try:
    import pygame
    from pygame import mixer
    HAVE_PYGAME = True
except Exception:
    HAVE_PYGAME = False

SAMPLE_RATE = 44100

def _tone(freq, dur, vol=0.25, wave="sine"):
    n = int(SAMPLE_RATE * dur)
    t = np.linspace(0, dur, n, endpoint=False, dtype=np.float32)
    if wave == "sine":
        x = np.sin(2*np.pi*freq*t)
    elif wave == "square":
        x = np.sign(np.sin(2*np.pi*freq*t))
    elif wave == "tri":
        x = 2*np.abs(2*((t*freq) % 1)-1)-1
    else:
        x = np.sin(2*np.pi*freq*t)
    # アタック/リリース
    env = np.ones_like(x)
    a = max(1, int(0.005 * SAMPLE_RATE))
    r = max(1, int(0.020 * SAMPLE_RATE))
    env[:a]  = np.linspace(0, 1, a, dtype=np.float32)
    env[-r:] = np.linspace(1, 0, r, dtype=np.float32)
    x = (x * env) * vol
    return np.int16(x * 32767)

def _mix_layers(layers):
    if not layers:
        return np.zeros((int(SAMPLE_RATE*1.0),), dtype=np.int16)
    L = max(len(a) for a in layers)
    out = np.zeros(L, dtype=np.float32)
    for a in layers:
        if len(a) < L:
            a = np.pad(a, (0, L-len(a)))
        out += a.astype(np.float32) / 32767.0
    out = np.clip(out, -1.0, 1.0)
    return np.int16(out * 32767)

def _stereo(mono):
    return np.stack([mono, mono], axis=1)

class SoundBank:
    def __init__(self, enable=True):
        self.enabled = enable and HAVE_PYGAME
        self.sfx_hit = None
        self.sfx_miss = None
        self.sfx_clear = None
        self.bgm = None
        if self.enabled:
            mixer.pre_init(frequency=SAMPLE_RATE, size=-16, channels=2, buffer=512)
            pygame.init()
            mixer.set_num_channels(8)
            self._build_all()

    def _build_all(self):
        a = _tone(880,  0.05, vol=0.28, wave="sine")
        b = _tone(1175, 0.05, vol=0.26, wave="sine")
        self.sfx_hit = pygame.sndarray.make_sound(_stereo(_mix_layers([a, np.roll(b, len(a))])))
        m1 = _tone(180, 0.07, vol=0.30, wave="square")
        m2 = _tone(140, 0.06, vol=0.20, wave="tri")
        self.sfx_miss = pygame.sndarray.make_sound(_stereo(_mix_layers([m1, m2])))
        c  = _tone(523,  0.06, vol=0.26)
        e  = _tone(659,  0.06, vol=0.24)
        g  = _tone(784,  0.06, vol=0.22)
        ch = _tone(1046, 0.10, vol=0.20)
        self.sfx_clear = pygame.sndarray.make_sound(_stereo(np.concatenate([c, e, g, ch])))
        # BGM 2.5s ループ
        dur = 2.5
        def chord(fs, d): return _mix_layers([_tone(f, d, vol=0.07, wave="tri") for f in fs])
        bass = np.concatenate([
            _tone(130.81,0.6,vol=0.18,wave="sine"),
            _tone(98.00, 0.6,vol=0.18,wave="sine"),
            _tone(110.00,0.6,vol=0.18,wave="sine"),
            _tone(87.31, 0.7,vol=0.18,wave="sine"),
        ])
        pad = np.concatenate([
            chord([261.63,329.63,392.00],0.6),
            chord([196.00,246.94,293.66],0.6),
            chord([220.00,261.63,329.63],0.6),
            chord([174.61,220.00,261.63],0.7),
        ])
        lead = np.concatenate([
            _tone(784,0.15,vol=0.10), _tone(659,0.10,vol=0.09),
            _tone(698,0.15,vol=0.10), _tone(587,0.10,vol=0.09),
            _tone(659,0.20,vol=0.10), _tone(523,0.15,vol=0.09),
            _tone(587,0.20,vol=0.10), _tone(523,0.25,vol=0.09),
        ])
        L = int(SAMPLE_RATE*dur)
        def fit(x): return np.pad(x,(0,max(0,L-len(x))))[:L]
        loop = _mix_layers([fit(bass), fit(pad), fit(lead)])
        self.bgm = pygame.sndarray.make_sound(_stereo(loop))
        self.bgm.set_volume(0.4)

    def play_hit(self):   self.enabled and self.sfx_hit  and self.sfx_hit.play()
    def play_miss(self):  self.enabled and self.sfx_miss and self.sfx_miss.play()
    def play_clear(self): self.enabled and self.sfx_clear and self.sfx_clear.play()
    def start_bgm(self):  self.enabled and self.bgm and self.bgm.play(loops=-1)
    def stop_bgm(self):   self.enabled and self.bgm and self.bgm.stop()
    def stop_all(self):
        if self.enabled:
            try:
                mixer.stop(); mixer.quit(); pygame.quit()
            except Exception:
                pass

# ========= 画像ユーティリティ =========
def alpha_blit(dst_bgr, src_bgra, x, y):
    h, w = src_bgra.shape[:2]
    x0, y0 = int(x), int(y)
    x1, y1 = x0 + w, y0 + h
    if x1 <= 0 or y1 <= 0 or x0 >= dst_bgr.shape[1] or y0 >= dst_bgr.shape[0]:
        return
    cx0 = max(0, x0); cy0 = max(0, y0)
    cx1 = min(dst_bgr.shape[1], x1); cy1 = min(dst_bgr.shape[0], y1)
    sx0 = cx0 - x0; sy0 = cy0 - y0
    sx1 = sx0 + (cx1 - cx0); sy1 = sy0 + (cy1 - cy0)
    roi_dst = dst_bgr[cy0:cy1, cx0:cx1]
    roi_src = src_bgra[sy0:sy1, sx0:sx1]
    if roi_src.shape[2] == 4:
        src_rgb = roi_src[..., :3].astype(np.float32)
        alpha = (roi_src[..., 3:4].astype(np.float32)) / 255.0
    else:
        src_rgb = roi_src[..., :3].astype(np.float32)
        alpha = np.ones_like(src_rgb[..., :1], dtype=np.float32)
    dst_rgb = roi_dst.astype(np.float32)
    out = src_rgb * alpha + dst_rgb * (1.0 - alpha)
    dst_bgr[cy0:cy1, cx0:cx1] = out.astype(np.uint8)

def load_and_fit(path, size_wh):
    """画像読み込み，レターボックスで配置，BGRで返す"""
    img = cv.imread(path, cv.IMREAD_UNCHANGED)
    if img is None:
        return None
    target_w, target_h = size_wh
    h, w = img.shape[:2]
    if w == 0 or h == 0:
        return None
    scale = min(target_w / w, target_h / h)
    nw, nh = int(w*scale), int(h*scale)
    resized = cv.resize(img, (nw, nh), interpolation=cv.INTER_AREA)
    base = np.full((target_h, target_w, 3), (0, 0, 0), np.uint8)
    x0 = (target_w - nw)//2
    y0 = (target_h - nh)//2
    if resized.shape[2] == 4:
        alpha_blit(base, resized, x0, y0)
    else:
        base[y0:y0+nh, x0:x0+nw] = resized
    return base

# ========= 寿司アセット =========
def load_sushi_assets(sushi_dir="assets/sushi", target_h=SUSHI_H):
    items = []
    paths = sorted(glob.glob(os.path.join(sushi_dir, "*.png")))
    for p in paths:
        img = cv.imread(p, cv.IMREAD_UNCHANGED)
        if img is None:
            continue
        name = os.path.splitext(os.path.basename(p))[0].lower()
        h, w = img.shape[:2]
        if h <= 0:
            continue
        sc = target_h / float(h)
        rim = cv.resize(img, (max(1, int(w*sc)), max(1, int(h*sc))), interpolation=cv.INTER_AREA)
        items.append({"name": name, "img": rim})
    return items

# ========= 狐（演出） =========
def load_fox_frames(target_h=200):
    frames = []
    if os.path.exists("assets/fox_strip.png"):
        sheet = cv.imread("assets/fox_strip.png", cv.IMREAD_UNCHANGED)
        if sheet is not None:
            h, w = sheet.shape[:2]
            n = max(2, w // max(1, h))
            cw = w // n
            for i in range(n):
                frames.append(sheet[:, i*cw:(i+1)*cw])
    if not frames:
        for p in sorted(glob.glob("assets/fox_*.png")):
            img = cv.imread(p, cv.IMREAD_UNCHANGED)
            if img is not None:
                frames.append(img)
    if not frames and os.path.exists("assets/fox.png"):
        img = cv.imread("assets/fox.png", cv.IMREAD_UNCHANGED)
        if img is not None:
            frames = [img]
    if not frames:
        d = np.zeros((64, 96, 4), np.uint8)
        d[..., :3] = (0, 140, 255)
        d[..., 3] = 255
        frames = [d]
    norm = []
    for f in frames:
        fh, fw = f.shape[:2]
        sc = target_h / max(1, fh)
        norm.append(cv.resize(f, (int(fw*sc), int(fh*sc)), interpolation=cv.INTER_AREA))
    frame_dt = 0.06 if len(norm) >= 2 else 0.18
    return norm, frame_dt

class FoxRunner:
    def __init__(self, frames, frame_dt, y_base, speed=420.0):
        self.frames = frames
        self.frame_dt = frame_dt
        self.idx = 0
        self.acc = 0.0
        self.h = frames[0].shape[0]
        self.w = frames[0].shape[1]
        self.x = W + self.w + 10
        self.y = int(y_base - self.h)
        self.vx = -abs(speed)
        self.alive = True
        self.bob_t = 0.0
    def update(self, dt):
        self.x += self.vx * dt
        self.acc += dt
        self.bob_t += dt
        if self.acc >= self.frame_dt:
            step = int(self.acc / self.frame_dt)
            self.idx = (self.idx + step) % len(self.frames)
            self.acc -= step * self.frame_dt
        if self.x < -self.w - 10:
            self.alive = False
    def draw(self, img):
        bob = 0
        if len(self.frames) == 1:
            bob = int(2 * np.sin(2*np.pi*2.0*self.bob_t))
        alpha_blit(img, self.frames[self.idx], int(self.x), int(self.y + bob))

# ========= 皿 =========
def draw_plate_base(img, cx, cy, color):
    cv.ellipse(img, (cx, cy), (PLATE_W//2, PLATE_H//2), 0, 0, 360, color, thickness=-1)
    cv.ellipse(img, (cx, cy), (PLATE_W//2, PLATE_H//2), 0, 0, 360, (0,0,0), 2)

def draw_plate(img, p):
    cx, cy = int(p['x']), int(p['y'])
    draw_plate_base(img, cx, cy, p['color'])
    if p.get('sushi') is not None:
        s = p['sushi']
        sh, sw = s.shape[:2]
        alpha_blit(img, s, cx - sw//2, cy - sh//2 - 6)
    text = p['word']
    scale = 0.9; thick = 2
    (tw, th), _ = cv.getTextSize(text, FONT, scale, thick)
    tx = cx - tw//2; ty = cy + th//3
    for dx in (-2,0,2):
        for dy in (-2,0,2):
            cv.putText(img, text, (tx+dx, ty+dy), FONT, scale, (0,0,0), thick+2, cv.LINE_AA)
    cv.putText(img, text, (tx, ty), FONT, scale, (240,240,240), thick, cv.LINE_AA)
    if p['prog'] > 0:
        done = text[:p['prog']]
        (dw, _), _ = cv.getTextSize(done, FONT, scale, thick)
        cv.line(img, (tx, ty+6), (tx + dw, ty+6), (30,30,30), 3, cv.LINE_AA)

# ========= 入力判定 =========
def handle_key(plates, ch):
    """ロック無し時：最も左にあるヒット可能な皿を探す"""
    for p in sorted(plates, key=lambda q: q['x']):
        w = p['word']; k = p['prog']
        if k < len(w) and w[k] == ch:
            p['prog'] += 1
            return True, (p['prog'] == len(w)), p
    return False, False, None

def handle_key_only(target_plate, ch):
    """ロック中：指定の1枚だけ判定"""
    if target_plate is None:
        return False, False
    w = target_plate['word']; k = target_plate['prog']
    if k < len(w) and w[k] == ch:
        target_plate['prog'] += 1
        return True, (target_plate['prog'] == len(w))
    return False, False

def make_plate(now, base_speed, rng, sushi_assets):
    lane = rng.choice(LANES_Y)
    color = (rng.randint(100,255), rng.randint(100,255), rng.randint(100,255))
    speed = base_speed * rng.uniform(0.90, 1.25)
    if sushi_assets:
        item = rng.choice(sushi_assets)
        word = item["name"]
        sushi_img = item["img"]
    else:
        word = rng.choice(["maguro","salmon","ebi","uni","ikura","anago","tamago","kappa","tai","hotate","toro"])
        sushi_img = None
    return {'x': W + 60.0, 'y': lane, 'word': word, 'prog': 0,
            'speed': speed, 'color': color, 'born': now, 'sushi': sushi_img}

# ========= 画面画像 =========
def load_title_img():
    img = load_and_fit("assets/title.png", (W, H))
    if img is None:
        img = np.full((H, W, 3), (20, 30, 45), np.uint8)
        cv.putText(img, "Typing Sushi", (W//2-220, H//2-20), FONT, 2.0, (255,255,255), 4, cv.LINE_AA)
        cv.putText(img, "Press any key", (W//2-180, H//2+60), FONT, 1.0, (220,220,220), 2, cv.LINE_AA)
    return img

def load_mode_img():
    img = load_and_fit("assets/mode.png", (W, H))
    if img is None:
        img = np.full((H, W, 3), (30, 20, 30), np.uint8)
        cv.putText(img, "Select Mode: 1 Easy / 2 Normal / 3 Hard", (50, H//2), FONT, 1.0, (255,255,255), 3, cv.LINE_AA)
    return img

def load_bg_for_mode(mode:int):
    p = f"assets/bg{mode}.png"
    img = load_and_fit(p, (W, H))
    if img is not None: return img
    fb = load_and_fit("assets/bg.png", (W, H))
    return fb if fb is not None else np.full((H, W, 3), (245,250,255), np.uint8)

def load_gameover_img(which:int):
    # which: 1=時間満了, 2=ライフ喪失
    fn = "assets/gameover1.png" if which == 1 else "assets/gameover2.png"
    img = load_and_fit(fn, (W, H))
    if img is None:
        img = np.full((H, W, 3), (20,30,60) if which==1 else (10,10,10), np.uint8)
        msg = "TIME UP!" if which==1 else "GAME OVER"
        cv.putText(img, msg, (W//2-240, H//2-10), FONT, 2.0, (255,255,255), 5, cv.LINE_AA)
        cv.putText(img, "Press any key", (W//2-180, H//2+70), FONT, 1.0, (230,230,230), 2, cv.LINE_AA)
    return img

# ========= 難易度パラメタ =========
def difficulty_params(mode:int):
    """
    1: Easy   2: Normal   3: Hard
    BASE_* と DURATION_SECONDS（耐久時間）を返す
    """
    if mode == 1:
        BASE_SPEED   = 130.0
        BASE_SPAWN   = 3.00
        SPEED_GROWTH = 0.00
        SPAWN_SHRINK = 0.01
        SPAWN_MIN    = 2.00
        DURATION_SECONDS = 60
    elif mode == 2:
        BASE_SPEED   = 160.0
        BASE_SPAWN   = 2.00
        SPEED_GROWTH = 0.80
        SPAWN_SHRINK = 0.005
        SPAWN_MIN    = 1.50
        DURATION_SECONDS = 90
    elif mode == 3:
        BASE_SPEED   = 210.0
        BASE_SPAWN   = 1.50
        SPEED_GROWTH = 1.20
        SPAWN_SHRINK = 0.018
        SPAWN_MIN    = 0.50
        DURATION_SECONDS = 120
    else:
        BASE_SPEED   = 160.0
        BASE_SPAWN   = 1.60
        SPEED_GROWTH = 0.80
        SPAWN_SHRINK = 0.012
        SPAWN_MIN    = 0.80
        DURATION_SECONDS = 75
    return dict(BASE_SPEED=BASE_SPEED, BASE_SPAWN=BASE_SPAWN,
                SPEED_GROWTH=SPEED_GROWTH, SPAWN_SHRINK=SPAWN_SHRINK,
                SPAWN_MIN=SPAWN_MIN, DURATION_SECONDS=DURATION_SECONDS)

# ========= メイン =========
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--fox-h", type=int, default=200, help="狐スプライト高さ(px)")
    parser.add_argument("--fox-speed", type=float, default=420.0)
    parser.add_argument("--sushi-dir", type=str, default="assets/sushi")
    args = parser.parse_args()
    rng = random.Random(args.seed)

    # 画像
    title_img = load_title_img()
    mode_img  = load_mode_img()

    # サウンド
    sb = SoundBank(enable=True)
    if not HAVE_PYGAME:
        print("[INFO] pygame なし：無音で実行（'pip install pygame' で有効化）")

    # 寿司
    sushi_assets = load_sushi_assets(args.sushi_dir, target_h=SUSHI_H)
    if sushi_assets:
        print(f"[INFO] Loaded {len(sushi_assets)} sushi sprites from '{args.sushi_dir}'")
    else:
        print(f"[WARN] '{args.sushi_dir}' に寿司画像が見つからず．フォールバック語で実行")

    # 狐
    fox_frames, fox_frame_dt = load_fox_frames(target_h=args.fox_h)

    cv.namedWindow("Yukakko Sushi (ESC to quit)")

    # ステート
    STATE_TITLE, STATE_MODE, STATE_PLAY, STATE_OVER = 0, 1, 2, 3
    state = STATE_TITLE
    current_mode = 2

    # ゲーム状態
    def reset_game(params, bg_img):
        plates = []
        lives = LIVES_START
        score = 0
        combo = 0
        start_t = time.time()
        prev_t  = start_t
        base_speed  = params["BASE_SPEED"]
        spawn_mean  = params["BASE_SPAWN"]
        next_spawn_t = start_t + rng.expovariate(1.0 / spawn_mean)
        foxes = []
        plates.append(make_plate(start_t, base_speed, rng, sushi_assets))
        return {
            "plates": plates, "lives": lives, "score": score, "combo": combo,
            "start_t": start_t, "prev_t": prev_t,
            "base_speed": base_speed, "spawn_mean": spawn_mean,
            "next_spawn_t": next_spawn_t, "foxes": foxes,
            "fps_acc": 0.0, "fps_cnt": 0, "fps_disp": 0.0,
            "params": params, "bg_img": bg_img,
            "time_limit": params["DURATION_SECONDS"], "timeup": False,
            "lock": None,
            "cleared": 0,          # これまでにクリアした皿の枚数
            "mode": current_mode,  # 難易度（1/2/3）
            "last_nonempty_t": start_t
        }


    game = None

    while True:
        # ===== タイトル =====
        if state == STATE_TITLE:
            cv.imshow("Yukakko Sushi (ESC to quit)", title_img)
            k = cv.waitKey(20) & 0xFF
            if k == 27: break
            elif k != 255: state = STATE_MODE
            continue

        # ===== モード選択 =====
        if state == STATE_MODE:
            cv.imshow("Yukakko Sushi (ESC to quit)", mode_img)
            k = cv.waitKey(20) & 0xFF
            if k == 27:
                state = STATE_TITLE; continue
            if k in (ord('1'), ord('2'), ord('3')):
                current_mode = int(chr(k))
                params = difficulty_params(current_mode)
                bg_img = load_bg_for_mode(current_mode)
                game = reset_game(params, bg_img)
                sb.start_bgm()
                state = STATE_PLAY
            continue

        # ===== ゲームオーバー =====
        if state == STATE_OVER:
            which = 1 if game["timeup"] else 2
            gameover_img = load_gameover_img(which)
            show_start = time.time()
            while True:
                frame = gameover_img.copy()
                # スコアを上部センター（Y≈140）に表示
                text = f"Score: {game['score']}"
                (tw, th), _ = cv.getTextSize(text, FONT, 1.6, 3)
                tx, ty = (W - tw)//2, 140
                for dx in (-2,0,2):
                    for dy in (-2,0,2):
                        cv.putText(frame, text, (tx+dx, ty+dy), FONT, 1.6, (0,0,0), 6, cv.LINE_AA)
                cv.putText(frame, text, (tx, ty), FONT, 1.6, (255,255,255), 3, cv.LINE_AA)

                # ------- 右側黒枠（2段）に数字のみ表示 -------
                cx = W - 210          # 右黒枠の中央X（目安）
                y_top = 250           # 上段Y（枚数）
                y_bot = y_top + 150   # 下段Y（難易度）

                # 1) クリア枚数
                num1 = str(game.get("cleared", 0))
                (scale1, thick1) = (3.0, 6)
                (tw1, th1), _ = cv.getTextSize(num1, FONT, scale1, thick1)
                #tx1, ty1 = cx - tw1//2, y_top + th1//2
                tx1, ty1 = W // 2 - tw1//2, y_top + th1//2
                for dx in (-3,0,3):
                    for dy in (-3,0,3):
                        cv.putText(frame, num1, (tx1+dx, ty1+dy), FONT, scale1, (0,0,0), thick1+4, cv.LINE_AA)
                cv.putText(frame, num1, (tx1, ty1), FONT, scale1, (255,255,255), thick1, cv.LINE_AA)

                # 2) 難易度（1/2/3）
                num2 = str(game.get("mode", 2))
                (scale2, thick2) = (2.6, 6)
                (tw2, th2), _ = cv.getTextSize(num2, FONT, scale2, thick2)
                #tx2, ty2 = cx - tw2//2, y_bot + th2//2
                #tx2, ty2 = W // 2 - tw2//2, y_bot + th2//2
                tx2, ty2 = W // 2, y_bot + th2//2
                for dx in (-3,0,3):
                    for dy in (-3,0,3):
                        cv.putText(frame, num2, (tx2+dx, ty2+dy), FONT, scale2, (0,0,0), thick2+4, cv.LINE_AA)
                cv.putText(frame, num2, (tx2, ty2), FONT, scale2, (255,255,255), thick2, cv.LINE_AA)
                # ----------------------------------------------

                cv.imshow("Yukakko Sushi (ESC to quit)", frame)
                k = cv.waitKey(20) & 0xFF
                if k == 27:
                    sb.stop_bgm(); cv.destroyAllWindows(); return
                if time.time() - show_start >= 3.0 and k != 255:
                    sb.stop_bgm(); state = STATE_TITLE; break
            continue

        # ===== プレイ中 =====
        now = time.time()
        dt = now - game["prev_t"]; game["prev_t"] = now
        elapsed = now - game["start_t"]
        remaining = max(0.0, game["time_limit"] - elapsed)

        frame = game["bg_img"].copy()

        # 難易度スケール
        p = game["params"]
        game["base_speed"] = p["BASE_SPEED"] + p["SPEED_GROWTH"] * elapsed
        game["spawn_mean"] = max(p["SPAWN_MIN"], p["BASE_SPAWN"] - p["SPAWN_SHRINK"] * elapsed)

        # 皿移動
        for pl in game["plates"]:
            pl['x'] -= pl['speed'] * dt

        # 左端抜け（ミス & ロック解除判定）
        remain = []
        for pl in game["plates"]:
            if pl['x'] < -80:
                game["lives"] -= 1
                game["combo"] = 0
                if game["lock"] is pl:
                    game["lock"] = None
            else:
                remain.append(pl)
        game["plates"] = remain

        # 1秒以上場に皿がゼロなら強制スポーン
        if len(game["plates"]) == 0:
            if now - game["last_nonempty_t"] >= 1.0:
                game["plates"].append(make_plate(now, game["base_speed"], rng, sushi_assets))
                # 次の通常スポーンも改めて設定
                game["next_spawn_t"] = now + rng.expovariate(1.0 / game["spawn_mean"])
                game["last_nonempty_t"] = now   # もうゼロではないので更新
        else:
            # 皿が1枚でもあれば時刻を記録
            game["last_nonempty_t"] = now

        # スポーン（ポアソン）
        if now >= game["next_spawn_t"]:
            game["plates"].append(make_plate(now, game["base_speed"], rng, sushi_assets))
            game["next_spawn_t"] = now + rng.expovariate(1.0 / game["spawn_mean"])

        # 狐更新
        for fox in game["foxes"]: fox.update(dt)
        game["foxes"] = [f for f in game["foxes"] if f.alive]

        # 描画：皿（ロック中は薄い枠で強調）
        for pl in game["plates"]:
            if game["lock"] is pl:
                cx, cy = int(pl['x']), int(pl['y'])
                cv.ellipse(frame, (cx, cy), (PLATE_W//2+6, PLATE_H//2+6), 0, 0, 360, (0,80,255), 2, cv.LINE_AA)
            draw_plate(frame, pl)
        for fox in game["foxes"]: fox.draw(frame)

        # HUD
        cv.putText(frame, f"Score: {game['score']}", (20, 40), FONT, 0.9, (30,30,30), 2, cv.LINE_AA)
        cv.putText(frame, f"Combo: x{game['combo']}", (20, 80), FONT, 0.9, (60,60,60), 2, cv.LINE_AA)
        cv.putText(frame, f"Lives: {game['lives']}", (20, 120), FONT, 0.9, (0,0,180), 2, cv.LINE_AA)
        cv.putText(frame, f"Time Left: {int(np.ceil(remaining))}s", (W-280, 40), FONT, 0.9, (30,30,30), 2, cv.LINE_AA)

        # FPS
        game["fps_acc"] += 1.0/max(dt,1e-6); game["fps_cnt"] += 1
        if game["fps_cnt"] >= 10:
            game["fps_disp"] = game["fps_acc"]/game["fps_cnt"]; game["fps_acc"] = 0.0; game["fps_cnt"] = 0
        cv.putText(frame, f"FPS: {game['fps_disp']:.1f}", (W-200, 80), FONT, 0.8, (90,90,90), 2, cv.LINE_AA)
        cv.putText(frame, "ESC: title", (W-150, H-20), FONT, 0.7, (80,80,80), 2, cv.LINE_AA)

        # 入力
        key = cv.waitKey(int(1000 / FPS_TARGET)) & 0xFF
        if key == 27:
            sb.stop_bgm(); state = STATE_TITLE; continue

        # ---- 終了判定 ----
        if remaining <= 0.0:
            game["timeup"] = True
            state = STATE_OVER
            cv.imshow("Yukakko Sushi (ESC to quit)", frame); cv.waitKey(1)
            continue
        if game["lives"] <= 0:
            game["timeup"] = False
            state = STATE_OVER
            cv.imshow("Yukakko Sushi (ESC to quit)", frame); cv.waitKey(1)
            continue

        # ---- ロックオン入力処理 ----
        if 0 <= key < 256:
            ch = chr(key).lower()
            if ch in string.ascii_lowercase:
                if game["lock"] is None:
                    # 未ロック → 最初に当たる皿にヒットしたらロック開始
                    hit, cleared, tgt = handle_key(game["plates"], ch)
                    if hit:
                        game["lock"] = tgt
                        sb.play_hit()
                        game["combo"] += 1
                        game["score"] += POINT_CHAR
                        if cleared:
                            sb.play_clear()
                            game["score"] += int(len(tgt['word']) * COMBO_BONUS_RATE * POINT_CHAR * max(1, game['combo']//5))
                            game["plates"] = [p for p in game["plates"] if p is not tgt]
                            game["lock"] = None
                            game["cleared"] += 1
                            # 狐
                            ground_y = H - 40 - random.randint(0,30)
                            game["foxes"].append(FoxRunner(fox_frames, fox_frame_dt, y_base=ground_y, speed=args.fox_speed))
                    else:
                        sb.play_miss()
                        game["combo"] = 0
                else:
                    # ロック中：その皿だけを見る
                    hit, cleared = handle_key_only(game["lock"], ch)
                    if hit:
                        sb.play_hit()
                        game["combo"] += 1
                        game["score"] += POINT_CHAR
                        if cleared:
                            sb.play_clear()
                            tgt = game["lock"]
                            game["score"] += int(len(tgt['word']) * COMBO_BONUS_RATE * POINT_CHAR * max(1, game['combo']//5))
                            game["plates"] = [p for p in game["plates"] if p is not tgt]
                            game["lock"] = None
                            game["cleared"] += 1
                            # 狐
                            ground_y = H - 40 - random.randint(0,30)
                            game["foxes"].append(FoxRunner(fox_frames, fox_frame_dt, y_base=ground_y, speed=args.fox_speed))
                    else:
                        sb.play_miss()
                        game["combo"] = 0

        # 皿の数（=クリア枚数）を上部中央の黒帯位置に中央揃えで表示
        num_text = str(game["cleared"])
        (scale, thick) = (2.2, 5)
        (tw, th), _ = cv.getTextSize(num_text, FONT, scale, thick)
        cx, cy = W // 2, 50
        tx, ty = cx - tw // 2, cy + th // 2
        # アウトライン
        for dx in (-2, 0, 2):
            for dy in (-2, 0, 2):
                cv.putText(frame, num_text, (tx+dx, ty+dy), FONT, scale, (0,0,0), thick+3, cv.LINE_AA)
        cv.putText(frame, num_text, (tx, ty), FONT, scale, (255,255,255), thick, cv.LINE_AA)

        cv.imshow("Yukakko Sushi (ESC to quit)", frame)

    cv.destroyAllWindows()
    sb.stop_all()

if __name__ == "__main__":
    main()

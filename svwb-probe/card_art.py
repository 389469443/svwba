"""Card artwork cache and nonblocking tracker hover previews."""
from collections import OrderedDict
import threading
from io import BytesIO
from pathlib import Path
import html
import json
import queue
import re
import time
import tkinter as tk
from functools import lru_cache
from PIL import ImageFont

from PIL import Image, ImageDraw, ImageOps, ImageTk

HERE = Path(__file__).resolve().parent
CACHE = HERE / "gamedata" / "card-art"
JOBS = queue.Queue()
def _worker():
    while True:
        job = JOBS.get()
        try:
            job()
        finally:
            JOBS.task_done()
for _ in range(2):
    threading.Thread(target=_worker, daemon=True, name="card-art").start()
KEY = "#010203"


def effect_text(value):
    value = re.sub(r"<hr\s*/?>", "\n────────\n", value or "", flags=re.I)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def cached(cid):
    if (CACHE/(str(cid)+".art.webp")).is_file() and (CACHE/(str(cid)+".log.webp")).is_file():
        return True
    try:
        meta = json.loads((CACHE / (str(cid)+".json")).read_bytes())
        return meta.get("art_version") == 2 and all(
            (CACHE / (str(cid)+suffix)).is_file() for suffix in (".webp", ".banner.webp"))
    except (OSError, ValueError):
        return False


def fetch_card(cid):
    """Only public card IDs go to the official card site; no user configuration."""
    cid = int(cid)
    if not 10000000 <= cid <= 99999999:
        raise ValueError("Invalid card ID")
    if cached(cid):
        return
    import requests
    response = requests.get("https://shadowverse-wb.com/web/CardList/card",
                            params={"card_id": cid}, headers={"Lang": "chs"}, timeout=18)
    response.raise_for_status()
    details = response.json()["data"]["card_details"][str(cid)]
    common = details["common"]
    if int(common["card_id"]) != cid:
        raise ValueError("Card ID mismatch")
    CACHE.mkdir(parents=True, exist_ok=True)
    for kind, field, suffix in (("card", "card_image_hash", ".webp"),
                                ("list", "card_banner_image_hash", ".banner.webp")):
        digest = common[field]
        if not re.fullmatch(r"[a-f0-9]{32}", digest):
            raise ValueError("Invalid artwork identifier")
        result = requests.get("https://shadowverse-wb.com/uploads/card_image/chs/" +
                              kind + "/" + digest + ".png", timeout=20)
        result.raise_for_status()
        if len(result.content) > 8 * 1024 * 1024:
            raise ValueError("Artwork exceeds limit")
        with Image.open(BytesIO(result.content)) as source:
            if source.width * source.height > 16_000_000:
                raise ValueError("Artwork dimensions exceed limit")
            image = source.convert("RGBA")
            image.thumbnail((650, 900) if kind == "card" else (640, 120))
            target = CACHE / (str(cid) + suffix)
            temporary = target.with_suffix(suffix + ".tmp")
            image.save(temporary, "WEBP", quality=88)
            temporary.replace(target)
    # Commit metadata last; incomplete downloads are never treated as complete.
    target = CACHE / (str(cid) + ".json")
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(dict(common=common, evo=details.get("evo"),
                                         art_version=2, source="https://shadowverse-wb.com/chs/deck/cardslist/card/?card_id=" + str(cid)),
                                    ensure_ascii=False), encoding="utf8")
    temporary.replace(target)


class ArtManager:
    def __init__(self, tracker):
        self.tracker, self.root = tracker, tracker.root
        self.pending, self.failed = set(), {}
        self.events, self.rows = queue.Queue(), {}
        self.images = OrderedDict()
        self.tip, self.tip_image = None, None
        self.current, self.hover_row, self.timer = None, None, None
        self.closed = False
        self.streaming = False
        self.tick = self.root.after(100, self._pump)

    def ensure(self, cid, full=False):
        if not full and ((CACHE/(str(cid)+".log.webp")).is_file()
                         or (CACHE/(str(cid)+".banner.webp")).is_file()):
            return
        if cached(cid) or cid in self.pending or time.monotonic() < self.failed.get(cid, 0):
            return
        self.pending.add(cid)
        def job():
            if self.closed:
                return
            try:
                fetch_card(cid)
                self.events.put((cid, True))
            except Exception:
                self.events.put((cid, False))
        JOBS.put(job)

    def _pump(self):
        if self.closed:
            return
        while not self.events.empty():
            cid, ok = self.events.get_nowait()
            self.pending.discard(cid)
            if not ok:
                self.failed[cid] = time.monotonic() + 120
            for row in self.rows.get(cid, ()):
                if row.winfo_exists():
                    row.paint()
            if self.current == cid and self.tip and self.tip.winfo_exists():
                self._fill_tip(cid)
                self._position_tip(self.hover_row)
        self.tick = self.root.after(100, self._pump)

    def banner(self, cid, width, height, grey):
        key = (cid, width, height, grey)
        if key in self.images:
            self.images.move_to_end(key)
            return self.images[key]
        native = CACHE/(str(cid)+".log.webp")
        path = native if native.is_file() else CACHE/(str(cid)+".banner.webp")
        if not path.is_file():
            return None
        with Image.open(path) as source:
            art = ImageOps.fit(source.convert("RGBA"), (width, height),
                               method=Image.Resampling.LANCZOS)
            image = Image.new("RGB", (width,height), "#9aa0a6" if grey else "#171c25")
            image.paste(art, mask=art.getchannel("A"))
        # Darker below the name, more visible artwork toward the right edge.
        shade = Image.new("RGB", image.size, "#9aa0a6" if grey else "#12161d")
        mask = Image.new("L", image.size)
        draw = ImageDraw.Draw(mask)
        for x in range(width):
            draw.line((x, 0, x, height), fill=int(180 - 90 * x / max(width, 1)))
        image = Image.composite(shade, image, mask)
        result = image
        self.images[key] = result
        while len(self.images) > 64:
            self.images.popitem(last=False)
        return result

    def enter(self, row):
        if self.tracker.options.get("locked"):
            return
        self.hover_row = row
        if self.timer:
            self.root.after_cancel(self.timer)
        self.timer = self.root.after(250, lambda: self.show(row))

    def leave(self, row):
        if self.timer:
            self.root.after_cancel(self.timer)
            self.timer = None
        self.root.after(100, self._check_leave)

    def _check_leave(self):
        if self.closed:
            return
        x, y = self.root.winfo_pointerxy()
        def inside(w):
            return (w and w.winfo_exists() and w.winfo_rootx() <= x < w.winfo_rootx()+w.winfo_width()
                    and w.winfo_rooty() <= y < w.winfo_rooty()+w.winfo_height())
        if not inside(self.hover_row) and not inside(self.tip):
            self.hide()

    def show(self, row):
        self.timer = None
        if self.closed or not row.winfo_exists() or self.tracker.options.get("locked"):
            return
        self.hide()
        self.current, self.hover_row = row.cid, row
        self.effect_offset = 0
        self.tip = tk.Toplevel(self.root)
        self.tip.withdraw()
        self.tip.title("卡牌详情")
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        self.tip.configure(bg="#161b24", padx=0, pady=0)
        if not self.streaming:
            self.tip.attributes("-transparentcolor", KEY)
        else:
            # 直播模式：不用透明色，让 OBS / 录屏直接采集到提示窗
            try:
                import stream_settings
                stream_settings.capture_style(self.tip)
            except Exception:
                pass
        self.tip.bind("<Leave>", lambda e: self.root.after(100, self._check_leave))
        self.ensure(row.cid, full=True)
        self._fill_tip(row.cid)
        self._position_tip(row)
        self.tip.deiconify()

    def _position_tip(self, row):
        if not self.tip or not row or not row.winfo_exists():
            return
        self.tip.update_idletasks()
        width, height = self.tip.winfo_reqwidth(), self.tip.winfo_reqheight()
        # Keep it on the same monitor as the tracker, including negative coordinates.
        import ctypes
        from ctypes import wintypes as W
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("size", W.DWORD), ("monitor", W.RECT), ("work", W.RECT), ("flags", W.DWORD)]
        api = ctypes.windll.user32
        api.MonitorFromWindow.argtypes = [W.HWND, W.DWORD]
        api.MonitorFromWindow.restype = W.HANDLE
        api.GetMonitorInfoW.argtypes = [W.HANDLE, ctypes.POINTER(MonitorInfo)]
        area = MonitorInfo(); area.size = ctypes.sizeof(area)
        monitor = api.MonitorFromWindow(W.HWND(self.root.winfo_id()), 2)
        if api.GetMonitorInfoW(monitor, ctypes.byref(area)):
            left, top, right, bottom = area.work.left, area.work.top, area.work.right, area.work.bottom
        else:
            left, top, right, bottom = 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x, y = self.root.winfo_rootx()+self.root.winfo_width()+8, row.winfo_rooty()
        if x+width > right:
            x = self.root.winfo_rootx()-width-8
        x, y = max(left, min(x, right-width)), max(top, min(y, bottom-height))
        self.tip.geometry("%+d%+d" % (x, y))

    def _fill_tip(self, cid):
        for widget in self.tip.winfo_children():
            widget.destroy()
        import deck_reader
        meta=deck_reader.load_meta().get(str(cid),{})
        body=tk.Frame(self.tip,bg="#161b24",padx=16,pady=16)
        body.pack()
        native=CACHE/(str(cid)+".art.webp")
        path=native if native.is_file() else CACHE/(str(cid)+".webp")
        meta=dict(meta,_native_art=native.is_file())
        if path.is_file() and cached(cid):
            with Image.open(path) as source:
                image=source.convert("RGBA")
            image,_=hover_image(image,meta)
            self.tip_image=ImageTk.PhotoImage(image,master=self.root)
            tk.Label(body,image=self.tip_image,bg="#161b24",bd=0,padx=0,pady=0).pack()
        else:
            self.tip_image=None
            tk.Label(body,text="卡图加载中…" if cid in self.pending else "卡图暂不可用",
                     bg="#161b24",fg="#efe9dc",width=30,height=7).pack()
        skill=effect_text(meta.get("skill")) or "暂无效果文字"
        rules=tk.Frame(body,bg="#161b24",width=388,height=310)
        rules.pack(fill="x")
        rules.pack_propagate(False)
        text=tk.Text(rules,width=1,height=7,wrap="word",bg="#161b24",fg="#efe9dc",
                     font=("Microsoft YaHei UI",10),relief="flat",bd=0,cursor="arrow",
                     highlightthickness=0,padx=0,pady=8)
        text.insert("1.0",skill);text.configure(state="disabled");text.pack(fill="both",expand=True)
        text.bind("<Leave>",lambda e:self.root.after(100,self._check_leave))
        self.effect_widget=text


    def set_streaming(self, enabled):
        """控制台直播模式变化：悬停卡图/效果提示窗跟随切换可录 / 透明样式。"""
        self.streaming = bool(enabled)
        try:
            if self.tip is not None and self.tip.winfo_exists():
                if self.streaming:
                    self.tip.attributes("-transparentcolor", "")
                    import stream_settings
                    stream_settings.capture_style(self.tip)
                else:
                    self.tip.attributes("-transparentcolor", KEY)
        except Exception:
            pass

    def hide(self):
        self.current = None
        if self.tip:
            self.tip.destroy()
            self.tip = None
        self.tip_image = None

    def clear_rows(self):
        self.hide()
        if self.timer:
            self.root.after_cancel(self.timer)
            self.timer = None
        self.hover_row = None
        self.rows.clear()
        # 保留 self.images（行背景图缓存）：整窗重建时直接复用，
        # 否则每行都要重新合成一次图，逐行出现就是用户看到的「闪烁」。

    def close(self):
        self.closed = True
        self.root.after_cancel(self.tick)
        self.clear_rows()


class CardRow(tk.Canvas):
    def __init__(self, manager, parent, cid, qty, meta, section, font, bold_font, color, rarity):
        self.manager, self.cid, self.qty, self.meta = manager, int(cid), qty, meta
        self.section, self.text_font, self.bold_font, self.color = section, font, bold_font, color
        self.row_height = max(30, font.metrics("linespace") + 10)
        super().__init__(parent, height=self.row_height, bg="#171c25", highlightthickness=2,
                         highlightbackground=rarity, bd=0, cursor="arrow")
        self.pack(fill="x", pady=1)
        self.photo = None
        self.bind("<Configure>", lambda e: self.paint())
        self.bind("<Enter>", lambda e: manager.enter(self), add="+")
        self.bind("<Leave>", lambda e: manager.leave(self), add="+")
        manager.rows.setdefault(self.cid, []).append(self)
        manager.ensure(self.cid)

    def config(self, cnf=None, **kwargs):
        if "text" in kwargs:
            self.qty = kwargs.pop("text")
            self.paint()
        if cnf or kwargs:
            super().config(cnf, **kwargs)

    def paint(self):
        if not self.winfo_exists():
            return
        width, height = max(80, self.winfo_width()), self.row_height
        self.delete("all")
        played = self.section == "played"
        surface = "#9aa0a6" if played else "#171c25"
        foreground = "#1b2229" if played else "white"
        background = self.manager.banner(self.cid, width, height, played)
        image = background.copy() if background else Image.new("RGB",(width,height),surface)
        draw = ImageDraw.Draw(image)
        pixels = max(11, round(abs(self.text_font.actual("size")) *
                               self.winfo_fpixels("1i") / 72))
        font, bold = ui_font(pixels), ui_font(pixels,True)
        draw.rectangle((3,3,29,height-4), fill=self.color)
        draw.text((16,height/2),str(self.meta.get("cost",0)),font=bold,fill="#111111",anchor="mm")
        typ = {1:"随从",2:"护符",3:"护符",4:"法术"}.get(self.meta.get("type"),"")
        quantity = str(self.qty).strip()
        show_quantity = quantity not in ("", "0", "1")
        if show_quantity:
            draw.rectangle((width-30,3,width-4,height-4),fill=surface,outline="#657063")
            draw.text((width-17,height/2),quantity,font=font,
                      fill=foreground if played else "#ebd7ab",anchor="mm")
        type_right = width-35 if show_quantity else width-8
        draw.text((type_right,height/2),typ,font=font,
                  fill=foreground if played else "#c0b69e",anchor="rm")
        name_limit = type_right-int(draw.textlength(typ,font=font))-8
        name = self.meta.get("name","#%s"%self.cid)
        while name and draw.textlength(name,font=font)>max(10,name_limit-35):
            name = name[:-2]+"…" if len(name)>2 else ""
        # Rasterize over the actual artwork, preventing Windows/Tk text
        # antialiasing from producing opaque grey rectangles behind glyphs.
        # Played cards keep their artwork, with a grey name background and
        # dark text. The same card in deck/hand retains the deep background.
        name_width=min(name_limit,35+int(draw.textlength(name,font=font))+4)
        draw.rectangle((31,0,max(31,name_width),height),fill=surface)
        draw.text((35,height/2),name,font=font,fill=foreground,anchor="lm",
                  stroke_width=0 if played else 1,stroke_fill="#090d12")
        self.photo = ImageTk.PhotoImage(image,master=self.manager.root)
        self.create_image(0,0,image=self.photo,anchor="nw")


@lru_cache(maxsize=32)
def ui_font(size, bold=False):
    return ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc" if bold
                              else "C:/Windows/Fonts/msyh.ttc", size)


def hover_image(image,meta,offset=0):
    """Rectangular illustration above the rules, in a single dark popup."""
    source_width,source_height=image.size
    illustration=image if meta.get("_native_art") else image.crop(
        (round(source_width*.12),round(source_height*.22),
         round(source_width*.88),round(source_height*.85)))
    # Game textures are stored square for the GPU but displayed at 848:1024.
    # Normalize that layout once; then contain the complete artwork without crop.
    if meta.get("_native_art") and abs(illustration.width/illustration.height-1)<.01:
        illustration=illustration.resize((round(illustration.height*848/1024),
                                          illustration.height),Image.Resampling.LANCZOS)
    image=ImageOps.contain(illustration,(270,300),
                          method=Image.Resampling.LANCZOS).convert("RGBA")
    width,height=image.size
    overlay=Image.new("RGBA",image.size);draw=ImageDraw.Draw(overlay)
    for y in range(70):
        draw.line((0,y,width,y),fill=(9,13,19,max(0,220-y*4)))
    font=ui_font(18,True);name=meta.get("name","")
    while name and font.getlength(name)>width-55:
        name=name[:-2]+"…" if len(name)>2 else ""
    draw.text((12,12),name,font=font,fill="#fff6e8",stroke_width=1,stroke_fill="#090d13")
    draw.text((width-14,11),str(meta.get("cost","?")),font=ui_font(24,True),
              fill="#e6d2a9",anchor="rt",stroke_width=1,stroke_fill="#090d13")
    if meta.get("type")==1:
        draw.text((width-12,height-10),"%s / %s"%(meta.get("atk","?"),meta.get("life","?")),
                  font=ui_font(16,True),fill="#f1dfbc",anchor="rs",
                  stroke_width=1,stroke_fill="#090d13")
    return Image.alpha_composite(image,overlay),0

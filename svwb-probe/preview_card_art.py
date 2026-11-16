"""卡图功能预览，示例数据不会写入战绩或卡组。"""
import sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import deck_tracker
cards=[11041110,11001110,10741110,11002110,10842110,10742110]
snapshot=dict(in_battle=True,deckCount=32,handReady=True,mulliganDone=True,
 hand=[11001110,11001110,11002110,10742110,90001210],
 playedFromHand={str(11041110):1},rows=[(c,3,3,0) for c in cards],
 played=[11041110],destroyed=[],overdraw=0,returned=False)
deck_tracker.DeckTracker._snapshot=lambda self:snapshot
deck_tracker.window_settings=lambda:dict(width=410,height=760,font=10,auto=False,locked=False)
window=deck_tracker.DeckTracker()
window.root.title("卡图与悬停效果预览")
window.root.geometry("410x760+80+90")
window.root.attributes("-alpha",1)
window.rate_label.config(text="—")
window.win_label.config(text="")
window.loss_label.config(text="")
window.root.after(1000,lambda:window.status_text.config(text="界面预览 · 示例牌局数据"))
window.root.mainloop()

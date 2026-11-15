"""Vahti pitch deck v3 (editable .pptx): the full deck and the 3-minute stage cut, from one script.

Rasmus's decisions (coord/rasmus-claude, docs/presentation/HANDOFF-DECK-TO-ASMOWW.md): pitch separate from the video;
concepts A/E/H/K/L/M as the data visuals; anything showing the site is a real 2x capture (assets/); wording reads like
a person talking; one example throughout (the hotel's 1,000 L, Aino). Numbers come from numbers.json, written by
deck_numbers.py from real runs; never typed here.

Run from the repo root with python-pptx installed:  python docs/presentation/build/build_deck.py
Fonts: Archivo, Archivo Black, IBM Plex Mono, Permanent Marker (free, Google Fonts / Omnibus-Type). Install them to edit
the .pptx with the intended look; the PDF embeds them.
"""
import json
import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

HERE = Path(__file__).resolve().parent
A = HERE / "assets"
OUT = HERE.parent
N = json.loads((HERE / "numbers.json").read_text())

NAVY, NAVY2, INK, SKY, PAPER = "002F6D", "0D4182", "001B40", "EAF4FD", "FFFFFF"
MUTE, LINE, SUN, PALE = "5B6B86", "C9D6EA", "FCB900", "B9CBE6"
GO, HOLD, STOP = "1D6B3A", "A35400", "B3261E"
HOLD_T, CHIP = "FFF3D6", "E2ECF8"
TITLE, BODY, MONO, MARK = "Archivo Black", "Archivo", "IBM Plex Mono", "Permanent Marker"
rgb = lambda h: RGBColor.from_string(h)


# ---------------------------------------------------------------------------------------------- helpers
def shape(s, x, y, w, h, fill=None, line=None, kind=MSO_SHAPE.RECTANGLE, lw=1.0, name=None):
    sh = s.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    sh.shadow.inherit = False
    if fill:
        sh.fill.solid(); sh.fill.fore_color.rgb = rgb(fill)
    else:
        sh.fill.background()
    if line:
        sh.line.color.rgb = rgb(line); sh.line.width = Pt(lw)
    else:
        sh.line.fill.background()
    if name:
        sh.name = name
    return sh


def text(s, x, y, w, h, paras, size=16, color=INK, bold=False, font=BODY, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, after=0, line=None, name=None):
    """paras: a string, or a list of paragraphs; a paragraph is a string or a list of (text, {opts}) runs."""
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    for i, para in enumerate(paras if isinstance(paras, list) else [paras]):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(after)
        if line:
            p.line_spacing = line
        for t, o in ([(para, {})] if isinstance(para, str) else para):
            r = p.add_run(); r.text = t; f = r.font
            f.name = o.get("font", font); f.size = Pt(o.get("size", size)); f.bold = o.get("bold", bold)
            f.italic = o.get("italic", False); f.color.rgb = rgb(o.get("color", color))
    return tb


def pic(s, path, x, y, w=None, h=None, name=None):
    p = s.shapes.add_picture(str(path), Inches(x), Inches(y), Inches(w) if w else None, Inches(h) if h else None)
    if name:
        p.name = name
    return p


def logo(s, x, y, scale=1.0, frame="FFFFFF"):
    """The Vahti mark: a box with a yellow tick that overshoots it (the V of vahti)."""
    u = scale * 0.022
    sq = shape(s, x + 1.5 * u, y + 6.5 * u, 18 * u, 18 * u, line=frame, kind=MSO_SHAPE.ROUNDED_RECTANGLE, lw=2.6 * scale, name="Logo box")
    sq.adjustments[0] = 0.12
    fb = s.shapes.build_freeform(Inches(x + 6 * u), Inches(y + 15.2 * u))
    fb.add_line_segments([(Inches(x + 10.6 * u), Inches(y + 20.4 * u)), (Inches(x + 27.5 * u), Inches(y + 2.5 * u))], close=False)
    t = fb.convert_to_shape(); t.fill.background(); t.line.color.rgb = rgb(SUN); t.line.width = Pt(3.6 * scale)
    t.shadow.inherit = False; t.name = "Logo tick"


def chip(s, x, y, label, fill=CHIP, color=NAVY, size=12, w=None, font=BODY):
    w = w or (0.2 + len(label) * size * 0.0075)
    shape(s, x, y, w, 0.3, fill=fill, kind=MSO_SHAPE.ROUNDED_RECTANGLE, name="Chip").adjustments[0] = 0.5
    text(s, x, y + 0.045, w, 0.26, label, size=size, color=color, align=PP_ALIGN.CENTER, font=font)
    return x + w + 0.1


class Deck:
    def __init__(self):
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = Inches(13.333), Inches(7.5)
        self.n = 0

    def slide(self, title, notes, dark=False, appendix=False):
        s = self.prs.slides.add_slide(self.prs.slide_layouts[6]); self.n += 1
        s.background.fill.solid(); s.background.fill.fore_color.rgb = rgb(NAVY if dark else SKY)
        if title:
            text(s, 0.6, 0.42, 12.1, 1.2, title, size=30, font=TITLE, color="FFFFFF" if dark else NAVY, line=0.95, name="Title")
        logo(s, 0.6, 6.95, scale=0.5, frame="FFFFFF" if dark else NAVY)
        text(s, 1.02, 7.0, 6, 0.3, [[("vahti", {"bold": True, "color": "FFFFFF" if dark else NAVY}),
                                     ("  for Valio Aimo" + (", appendix" if appendix else ""), {"color": PALE if dark else MUTE})]], size=10)
        text(s, 11.9, 7.0, 0.8, 0.3, "A" if appendix else str(self.n), size=10, color=PALE if dark else MUTE, align=PP_ALIGN.RIGHT)
        s.notes_slide.notes_text_frame.text = notes
        return s


# ---------------------------------------------------------------------------------------------- slides
def cover(d):
    s = d.slide(None, "Vahti is the guard between buying agents and Valio Aimo. Any agent can buy from Valio; Vahti decides "
                      "what it may do alone, a person decides the rest, and nobody breaks the hard rules.", dark=True)
    logo(s, 0.8, 0.8, scale=2.2)
    text(s, 2.4, 0.95, 6, 1.1, "vahti", size=60, font=TITLE, color="FFFFFF")
    text(s, 2.45, 1.95, 6, 0.4, "for Valio Aimo", size=18, color=PALE)
    text(s, 0.85, 3.05, 11.6, 2.4, [[("Any agent can buy from Valio.", {})], [("Vahti decides what it may do alone.", {"color": SUN})]],
         size=46, font=TITLE, color="FFFFFF", line=1.0, name="Tagline")
    text(s, 0.85, 6.35, 11.5, 0.4, "Junction 2026, Valio Aimo challenge. valio.psavela.com", size=14, color=PALE)


def agents(d):
    s = d.slide("Valio can't choose which AI agents its customers use",
                "Kitchens will buy through their own AI agents, built in-house, by a vendor, or a general assistant. Each one "
                "follows rules written into its own prompt, and Valio never sees them. A fooled agent's wrong order arrives "
                "looking like any other order.")
    names = ["A hotel chain's own agent", "ChatGPT or Claude", "A procurement vendor's agent", "An agent someone has fooled"]
    for i, n in enumerate(names):
        y = 1.95 + i * 1.05
        bad = i == 3
        shape(s, 0.6, y, 3.7, 0.72, fill="FCE3DF" if bad else PAPER, name=f"Agent {i + 1}")
        text(s, 0.82, y + 0.2, 3.4, 0.4, n, size=17, bold=True, color=STOP if bad else INK)
        c = s.shapes.add_connector(MSO_CONNECTOR.CURVE, Inches(4.3), Inches(y + 0.36), Inches(5.6), Inches(3.9))
        c.line.color.rgb = rgb(STOP if bad else "8A97AD"); c.line.width = Pt(2.5 if bad else 1.75)
    shape(s, 5.6, 1.95, 2.6, 3.87, fill=NAVY, name="Valio")
    text(s, 5.6, 3.45, 2.6, 1.0, [[("Valio Aimo", {"size": 24, "font": TITLE})], [("20,000 kitchens", {"size": 14, "color": PALE})]],
         color="FFFFFF", align=PP_ALIGN.CENTER)
    text(s, 8.75, 2.6, 4.0, 2.6, "Their rules live in their own prompts. Valio never sees them, and a wrong order looks like any other.",
         size=22, line=1.1)


def outcomes(d):
    s = d.slide("Vahti checks every order on Valio's side, and each one ends in one of three places",
                "Live site, the earlier-orders column: a yellow ticket waiting for a person, a green one the agent ordered "
                "alone, a red one nobody can order. Every order lands in one of these three, whatever agent sent it.")
    rows = [("tk2.png", "Ordered", "Within the limits the buyer signed.", GO),
            ("tk1.png", "Waiting for a person", "Over the agent's limit, late, a substitute, or a fact the data can't confirm.", HOLD),
            ("tk3.png", "Not ordered", "A banned allergen, a recall or the spending ceiling.", STOP)]
    for i, (img, h, b, c) in enumerate(rows):  # live tickets from the site's earlier-orders column
        x = 0.9 + i * 4.1
        pic(s, A / img, x, 1.85, h=3.25, name=f"Live: {h}")
        text(s, x, 5.3, 3.6, 1.4, [[(h, {"font": TITLE, "size": 22, "color": c})], [(b, {"size": 16})]], after=4, line=1.05)


def ladder(d):
    s = d.slide("Six checks. The first “no” decides who acts next",
                "The brief's own example, as the gateway saw it. Each check shows the evidence it used; the right column "
                "says where the request would have gone on a no. It passed five; the sixth said no, so the head chef decides. "
                "Checks 4 to 6 run in Valio's gateway on every order, whichever agent sent it.")
    text(s, 0.6, 1.2, 9, 0.35, "“I need 1,000 litres of lactose-free milk by Monday.”", size=14, font=MONO, color=MUTE)
    rows = [("A purchase", "an order, not a question", [], "else: answer or decline"),
            ("Clear", "", ["milk", "1,000 L", "lactose-free", "by Mon 16 Nov", "profile: fat 1.5 %"], "else: ask the kitchen"),
            ("Valio has it", "", ["276 word matches", "25 plain milks", "14 deliverable", "4 live API calls"], "else: ask back, with what exists"),
            ("Safe", "", ["signed offer genuine", "€1,010 ≤ €5,000 hard limit", "matches the request"], "else: stop, nobody can approve"),
            ("Proven", "", ["lactose 0 g/100 g", "Valio stock", "on time"], "else: a person checks the facts")]
    for i, (name, ans, chips, exit_) in enumerate(rows):
        y = 1.72 + i * 0.78
        shape(s, 0.62, y + 0.06, 0.42, 0.42, fill=PAPER, line=GO, kind=MSO_SHAPE.OVAL, lw=2, name=f"Check {i + 1}")
        text(s, 0.62, y + 0.12, 0.42, 0.3, str(i + 1), size=13, bold=True, color=GO, align=PP_ALIGN.CENTER)
        shape(s, 1.25, y, 8.1, 0.62, fill=PAPER, name=f"Row {i + 1}")
        text(s, 1.42, y + 0.07, 7.8, 0.3, [[(name, {"bold": True, "size": 15}), ("   " + ans, {"size": 11, "color": MUTE})]])
        cx = 1.42
        for c in chips:
            cx = chip(s, cx, y + 0.31, c, size=10, fill="E3F2E6", color=GO)
        text(s, 9.55, y + 0.2, 3.2, 0.3, exit_, size=12, color=MUTE)
    y = 1.72 + 5 * 0.78
    shape(s, 0.62, y + 0.06, 0.42, 0.42, fill=HOLD, kind=MSO_SHAPE.OVAL, name="Check 6")
    text(s, 0.62, y + 0.12, 0.42, 0.3, "6", size=13, bold=True, color="FFFFFF", align=PP_ALIGN.CENTER)
    shape(s, 1.25, y, 8.1, 0.62, fill=HOLD_T, line=HOLD, lw=1.25, name="Row 6")
    text(s, 1.42, y + 0.07, 7.8, 0.3, [[("Agent's call", {"bold": True, "size": 15}), ("   €1,010 is over the €500 it may spend alone", {"size": 11, "color": HOLD, "bold": True})]])
    cx = chip(s, 1.42, y + 0.31, "auto-approve limit €500", size=10, fill="FCE6C4", color=HOLD)
    cx = chip(s, cx, y + 0.31, "24 h spend", size=10, fill="E3F2E6", color=GO)
    chip(s, cx, y + 0.31, "on time", size=10, fill="E3F2E6", color=GO)
    shape(s, 9.55, 4.95, 3.15, 1.85, fill=NAVY, name="Who acts next")
    text(s, 9.75, 5.07, 2.8, 1.7, [[("Who decides", {"size": 11, "color": PALE})], [("Head chef", {"font": TITLE, "size": 22, "color": SUN})],
                                  [("Aino Virtanen can approve up to €1,500.", {"size": 12, "color": "FFFFFF"})]], after=2)


def verdict(d):
    s = d.slide(None, "One sentence a kitchen manager understands in a second, with the proof one tap away. €1,010 for 1,000 litres "
                      "of the cheapest lactose-free milk that arrives Monday; it stopped at the agent's own limit, so Aino decides.", dark=True)
    text(s, 0.75, 0.75, 8, 1.8, [[("The agent found it.", {})], [("The head chef decides.", {})]], size=44, font=TITLE, color="FFFFFF", line=0.95)
    text(s, 0.72, 3.55, 7, 1.4, "€1,010", size=110, font=TITLE, color="FFFFFF")
    text(s, 0.8, 5.25, 7.5, 0.4, "1,000 litres of Valio Eila lactose-free milk, Monday", size=19, bold=True, color="FFFFFF")
    shape(s, 8.4, 3.4, 4.35, 2.25, fill=NAVY2, line="3A64A8", lw=1, kind=MSO_SHAPE.ROUNDED_RECTANGLE, name="Verdict").adjustments[0] = 0.06
    text(s, 8.7, 3.62, 3.8, 2.0, [[("Check 6, Agent's call", {"size": 11, "color": PALE})],
                                  [("Over the €500 the agent may spend alone", {"font": TITLE, "size": 20, "color": "FFFFFF"})],
                                  [("Aino, the head chef, approves up to €1,500.", {"size": 13, "color": PALE})]], after=6, line=1.0)
    cx = 0.8
    for c in ["Cheapest of 14 that arrive Monday", "Lactose checked in the data", "Price signed by Valio"]:
        cx = chip(s, cx, 6.05, c, fill=NAVY2, color="FFFFFF", size=12)


def catalog(d):
    s = d.slide("Valio's whole catalog, and the 25 products that are actually lactose-free milk",
                "17,546 squares, one per product, grouped by category. Shade shows how far each product got for the 1,000 litre "
                "request. A keyword match isn't enough: most of the blue never reaches the price check. The yellow box is the one "
                "ordered: Valio Eila 1.5 %, €1.01 a litre.")
    pic(s, A / "concept_K.png", 0.6, 1.85, w=12.1, name="Concept K")


def numbers3(d):
    s = d.slide(None, "The narrowing happens on Valio's own data. The live API is asked only about the shortlist, as Valio's guidance "
                      "asks. 76 milk products were dropped because the data says they contain lactose, whatever the name says.", dark=True)
    text(s, 0.75, 0.6, 12, 1.6, [[("17,546 products.", {})], [("One right answer. Four API calls.", {})]], size=40, font=TITLE, color="FFFFFF", line=0.95)
    for x, n, l, sub in ((0.75, "17,546", "in the catalog", "searched locally, no API"), (5.55, "25", "plain lactose-free milks", "lactose 0 g checked in the data"),
                         (9.2, "1", "chosen", "cheapest of 14 that arrive Monday")):
        text(s, x, 2.8, 4.4, 1.5, n, size=80, font=TITLE, color="FFFFFF")
        text(s, x + 0.05, 4.3, 3.9, 0.7, [[(l, {"bold": True, "size": 17, "color": "FFFFFF"})], [(sub, {"size": 13, "color": PALE})]])
    text(s, 4.75, 3.25, 0.7, 0.6, "→", size=40, color=PALE); text(s, 8.45, 3.25, 0.7, 0.6, "→", size=40, color=PALE)
    for i, (n, l) in enumerate((("76", "“milk” products dropped: the data says they contain lactose"), ("4", "live calls to Valio, for the shortlist only"),
                                ("€1.01 a litre", "the cheapest one that arrives Monday"))):
        x = 0.75 + i * 4.05
        shape(s, x, 5.35, 3.85, 1.3, fill=NAVY2, line="3A64A8", kind=MSO_SHAPE.ROUNDED_RECTANGLE, name=f"Fact {i + 1}").adjustments[0] = 0.08
        text(s, x + 0.25, 5.5, 3.4, 1.1, [[(n, {"font": TITLE, "size": 22, "color": "FFFFFF"})], [(l, {"size": 13, "color": PALE})]])


def exceptions(d):
    s = d.slide("When an order can't go through as asked, a person sees why before anything is bought",
                "Live site. Needed today, but Valio can deliver on Monday: the ticket shows both dates and waits for a person. "
                "150 litres of rice drink, but only 109 in stock: a person decides. Fifteen real cases are one click away on the "
                "right: partial stock, substitutes, recalls, a hijacked agent.")
    for i, (q, img) in enumerate((("“We need 100 litres of lactose-free milk today.”", "dec_late.png"),
                                  ("“We need 150 litres of rice drink for Monday.”", "dec_partial.png"))):
        y = 1.75 + i * 2.55
        text(s, 0.6, y, 9, 0.4, q, size=17, font=MONO, color=INK)
        shape(s, 0.6, y + 0.5, 8.95, 1.75 if i == 0 else 1.6, fill=PAPER, name=f"Ticket foot {i + 1}")
        pic(s, A / img, 0.75, y + 0.6, w=8.65, name=f"Live: exception {i + 1}")
    pic(s, A / "cases_c.png", 10.05, 1.75, h=4.95, name="Live: try an exception")
    text(s, 10.05, 6.75, 2.8, 0.3, "Fifteen real cases, one click each", size=10, color=MUTE)


def exceptions3(d):
    """Three columns: what was asked, the gap drawn, what Vahti does (Rasmus's exceptions-v2 layout)."""
    s = d.slide("When an order can't go through as asked, a person sees why before anything is bought",
                "Four real cases on Valio's mock data. Each row: what the kitchen asked, the gap between that and what Valio can "
                "do, and what Vahti does about it. Nothing here quietly becomes a successful order.")
    cols = ((0.6, "Asked"), (5.0, "The gap"), (9.15, "What Vahti does"))
    for x, h in cols:
        text(s, x, 1.62, 4, 0.35, h, size=15, bold=True, color=MUTE)
    shape(s, 0.6, 2.02, 12.1, 0.03, fill=NAVY, name="Header rule")
    rows = (("“We need 100 litres of lactose-free milk today.”", "late", "Waits for Aino", "It arrives a day late. She sees both dates.", HOLD),
            ("“We need 150 litres of rice drink for Monday.”", "partial", "Waits for Aino", "She decides if 109 litres is enough.", HOLD),
            ("“Order 10 kg of sweet potato fries 475387 by Monday.”", "subst", "Waits for Aino", "She signs off Valio's substitute.", HOLD),
            ("Hospital ward, milk banned: “100 litres of lactose-free milk by Monday.”", "ward", "Not ordered", "Nobody can override the ban.", STOP))
    for i, (asked, kind, verdict_, why, c) in enumerate(rows):
        y = 2.2 + i * 1.18
        if i:
            shape(s, 0.6, y - 0.09, 12.1, 0.012, fill=LINE, name="Row rule")
        text(s, 0.6, y + 0.05, 4.1, 1.0, asked, size=14, font=MONO, color=INK, line=1.05)
        gx = 5.0
        if kind == "late":  # a date line: needed Sunday, earliest delivery Monday
            shape(s, gx, y + 0.5, 3.6, 0.03, fill="8A97AD", name="Date line")
            for dx, lab, col in ((0.3, "needed Sun 15.11", STOP), (2.5, "arrives Mon 16.11", NAVY)):
                shape(s, gx + dx, y + 0.4, 0.22, 0.22, fill=col, kind=MSO_SHAPE.OVAL, name="Date")
                text(s, gx + dx - 0.45, y + 0.68, 1.6, 0.3, lab, size=12, color=col, bold=True, align=PP_ALIGN.CENTER)
            text(s, gx + 0.75, y + 0.12, 1.6, 0.3, "one day late", size=12, color=MUTE, align=PP_ALIGN.CENTER)
        elif kind == "partial":  # bars: asked 150 L, in stock 109 L
            for j, (lab, n, fill) in enumerate((("asked", 150, None), ("in stock", 109, NAVY))):
                yy = y + 0.1 + j * 0.45
                text(s, gx, yy + 0.04, 0.85, 0.3, lab, size=12, color=MUTE)
                shape(s, gx + 0.9, yy, 2.3 * n / 150, 0.3, fill=fill or PAPER, line=NAVY, lw=1.25, name="Bar")
                text(s, gx + 0.97 + 2.3 * n / 150, yy + 0.03, 0.7, 0.3, f"{n} L", size=13, bold=True, color=INK)
        elif kind == "subst":  # the asked product out, Valio's substitute offered
            shape(s, gx, y + 0.1, 1.55, 0.72, fill=PAPER, line="8A97AD", lw=1, name="Asked product")
            text(s, gx + 0.08, y + 0.17, 1.4, 0.6, [[("475387", {"bold": True, "size": 12})], [("out of stock", {"size": 11, "color": STOP})]], color=INK)
            text(s, gx + 1.62, y + 0.24, 0.4, 0.4, "→", size=24, color=NAVY)
            shape(s, gx + 2.05, y + 0.1, 1.75, 0.72, fill=PAPER, line=NAVY, lw=1.5, name="Substitute")
            text(s, gx + 2.13, y + 0.17, 1.6, 0.6, [[("Valio's substitute", {"bold": True, "size": 12})], [("McCain sweet potato fries", {"size": 11, "color": MUTE})]], color=NAVY)
        else:  # the ban: lactose-free milk is milk
            chip(s, gx, y + 0.3, "lactose-free milk", fill=CHIP, color=NAVY, size=13)
            text(s, gx + 1.75, y + 0.22, 0.5, 0.4, "=", size=24, bold=True, color=INK, align=PP_ALIGN.CENTER)
            chip(s, gx + 2.3, y + 0.3, "milk", fill="FCE3DF", color=STOP, size=13)
            text(s, gx + 0.05, y + 0.72, 3.6, 0.3, "the ward's signed rules ban milk", size=12, color=MUTE)
        text(s, 9.15, y + 0.02, 3.6, 1.05, [[(("!  " if c == HOLD else "×  ") + verdict_, {"bold": True, "size": 17, "color": c})],
                                            [(why, {"size": 13, "color": INK})]], after=2, line=1.05)


def receipt(d):
    s = d.slide("Every price is signed, and anyone can check the receipt without signing in",
                "Aino approves on her own phone with a single-use link. The receipt re-checks itself every time it's opened: "
                "Valio's signature on the price, the buyer's signed rules, who approved it, and the audit trail. No agent can "
                "approve an order, set a price, order for another buyer, break a hard rule or use one offer twice.")
    pic(s, A / "phone_before.png", 0.6, 1.65, h=5.15, name="Live: phone approval")
    pic(s, A / "receipt_c.png", 3.35, 1.65, h=5.15, name="Live: receipt")
    text(s, 7.75, 2.4, 5.0, 3.6, [[("Aino approves on her phone, with a link that works once.", {})],
                                  [("Anyone can scan the receipt to re-check the signed price, the rules and who approved it.", {})]],
         size=20, after=18, line=1.1)


def results(d):
    t, e = N["traps"], N["everyday"]
    lead = f"all {t['total']}" if t["passed"] == t["total"] else f"{t['passed']} of {t['total']}"
    s = d.slide(f"On Valio's mock data, {lead} trap orders ended safely and {e['placed']} of {e['total']} everyday orders needed nobody",
                f"Trap orders are real-data cases where buying agents usually go wrong. Everyday orders: {e['placed']} of {e['total']} went "
                f"through on their own; each of the other {e['total'] - e['placed']} stopped at a named check with the reason "
                "(no delivery on Sunday, no date given, a new agent's limit, stock Valio can't confirm).", dark=True)
    text(s, 0.75, 2.2, 5.5, 1.8, f"{t['passed']}/{t['total']}", size=110, font=TITLE, color=SUN)
    text(s, 0.8, 4.15, 5.2, 1.0, "trap orders ended safely", size=17, color="FFFFFF")
    text(s, 6.9, 2.2, 5.5, 1.8, f"{e['placed']}/{e['total']}", size=110, font=TITLE, color="FFFFFF")
    text(s, 6.95, 4.15, 5.6, 1.0, "everyday orders needed nobody",
         size=17, color="FFFFFF")
    text(s, 0.8, 6.35, 12, 0.4, f"Trap suite and everyday set on Valio's mock data, run {N['run_at']}, read by {N['reader']}. {N['tests']} automated tests.",
         size=11, color=PALE)


def valio_view(d):
    s = d.slide("Valio also sees which missing product data keeps agents waiting for a person",
                "Live Valio view. A hospital ward's agent can decide alone on 17 % of the catalog today, because allergen data and "
                "delivery history are missing. The list on the right is what to fill in first, ranked by how many held orders it "
                "would let through.")
    shape(s, 0.6, 1.75, 12.1, 2.85, fill=PAPER, name="Valio view frame")
    pic(s, A / "valio_gaps.png", 0.75, 1.88, w=11.8, name="Live: Valio view")
    text(s, 0.6, 4.95, 12, 1.2, "A hospital ward's agent can decide alone on 17 % of the catalog. The list on the right is what to fix first.",
         size=22, color=INK, line=1.1)


def standards(d):
    s = d.slide("Open standards today, four steps to production",
                "Everything an agent needs is in open, documented formats, and the AI that reads requests is an open model running in "
                "Finland. The prototype runs on Valio's mock data; production needs buyer-held keys, a credential per agent, "
                "Valio's order system and rate limits. Details in docs/PRODUCTION.md.")
    text(s, 0.6, 1.9, 5.8, 0.5, "Today", size=24, font=TITLE, color=NAVY)
    text(s, 0.6, 2.55, 5.8, 4, ["MCP, REST and A2A", "Signed offers and verifiable buyer rules", "Orders as Peppol documents", "An open model, Gemma 4, running in Finland"],
         size=18, after=10)
    text(s, 7.0, 1.9, 5.8, 0.5, "For production", size=24, font=TITLE, color=NAVY)
    text(s, 7.0, 2.55, 5.7, 4, ["Buyers hold their own keys", "Each agent gets its own credential", "Orders go into Valio's order system", "Rate limits against scraping"], size=18, after=10)


def real(d):
    s = d.slide("What's real, and what we made up",
                "Assumptions are visible: docs/ASSUMPTIONS.md lists every quirk of the mock data and how it's handled.")
    text(s, 0.6, 1.9, 5.8, 0.5, "Real", size=24, font=TITLE, color=GO)
    text(s, 0.6, 2.55, 5.6, 4, "Live prices and stock from Valio's mock API, all 17,546 products, and every check, signature and receipt.",
         size=19, line=1.15)
    text(s, 7.0, 1.9, 5.8, 0.5, "Made up for the demo", size=24, font=TITLE, color=HOLD)
    text(s, 7.0, 2.55, 5.6, 4, "The buyers, their keys, and orders that stop before Valio's order system.", size=19, line=1.15)


def demo_card(d):
    s = d.slide(None, "Switch to the laptop here: the hotel's agent sends the brief's own request, live.", dark=True)
    text(s, 0.75, 1.0, 12, 0.5, "Live, on Valio's mock data", size=18, color=PALE)
    text(s, 0.75, 1.7, 11.8, 2.4, "“I need 1,000 litres of lactose-free milk by Monday.”", size=40, font=MONO, color="FFFFFF", bold=True, line=1.05)
    text(s, 0.75, 4.3, 8, 0.6, "The hotel's agent asks. Watch what Vahti lets it do.", size=22, color=SUN, bold=True)
    pic(s, A / "qr.png", 10.6, 4.2, h=2.1, name="QR to the site")
    text(s, 10.45, 6.38, 2.4, 0.3, "valio.psavela.com", size=12, color=PALE, align=PP_ALIGN.CENTER)


def close(d):
    s = d.slide(None, "Close on the one line. Everything shown runs live at valio.psavela.com.", dark=True)
    logo(s, 0.85, 0.85, scale=1.6)
    text(s, 0.85, 2.2, 9.2, 2.6, [[("Valio can't choose which agents its customers use.", {})], [("With Vahti, it doesn't have to.", {"color": SUN})]],
         size=38, font=TITLE, color="FFFFFF", line=1.0)
    text(s, 0.85, 5.35, 9, 0.9, [[("valio.psavela.com", {"bold": True, "size": 24, "color": "FFFFFF"})],
                                 [("github.com/Asmoww/junction-valio", {"size": 15, "color": PALE})]], after=4)
    pic(s, A / "qr.png", 10.4, 4.3, h=2.2, name="QR to the site")


def appendix_l(d):
    s = d.slide("Every product that drops out has a reason you can read",
                "For technical questions. Dark bar: still in. Light bar: removed at this step, with the reason. Log scale, so each "
                "step is visible; the numbers are exact. The live Valio API is asked only from the buyer-profile step on.", appendix=True)
    pic(s, A / "concept_L.png", 0.6, 1.5, w=12.1, name="Concept L")


def appendix_m(d):
    s = d.slide("How far each lactose-free milk got, and why it stopped",
                "For technical questions. The 25 plain lactose-free milks from the catalog check. A filled cell means the product "
                "passed that step; the hatched cell is where it stopped, and the last column says why.", appendix=True)
    pic(s, A / "concept_M.png", 2.05, 1.4, h=5.45, name="Concept M")


FULL = [cover, agents, outcomes, ladder, verdict, catalog, numbers3, exceptions3, receipt, results, valio_view, standards, real, close,
        appendix_l, appendix_m]
STAGE = [cover, agents, demo_card, exceptions3, results, close]

if __name__ == "__main__":
    for name, slides in (("vahti-pitch", FULL), ("vahti-stage", STAGE)):
        d = Deck()
        for f in slides:
            f(d)
        d.prs.core_properties.title = "Vahti for Valio Aimo" + (" (stage cut)" if name == "vahti-stage" else "")
        d.prs.core_properties.author = "Team Vahti, Junction 2026"
        d.prs.save(OUT / f"{name}.pptx")
        print("saved", name, len(d.prs.slides), "slides")
    sys.exit(0)

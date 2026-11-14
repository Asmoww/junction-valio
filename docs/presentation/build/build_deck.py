"""Vahti pitch deck (editable .pptx, native shapes and text). Run with the deck venv's python."""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

NAVY, INK, SKY, PAPER = "002F6D", "001B40", "EAF4FD", "FFFFFF"
MUTE, LINE, SUN = "5B6B86", "C9D6EA", "FCB900"
GO, HOLD, STOP = "1D6B3A", "C2620A", "C0261C"
GO_T, HOLD_T, STOP_T, BACK_T = "E3F2E6", "FFF3C4", "FCE3DF", "E3ECF9"
SANS, MONO = "Arial", "Courier New"

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
BLANK = prs.slide_layouts[6]
rgb = lambda h: RGBColor.from_string(h)


def box(slide, x, y, w, h, fill=None, line=None, shape=MSO_SHAPE.RECTANGLE, name=None, lw=1.0):
    s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    s.shadow.inherit = False
    if fill:
        s.fill.solid(); s.fill.fore_color.rgb = rgb(fill)
    else:
        s.fill.background()
    if line:
        s.line.color.rgb = rgb(line); s.line.width = Pt(lw)
    else:
        s.line.fill.background()
    if name:
        s.name = name
    return s


def text(slide, x, y, w, h, runs, size=16, color=INK, bold=False, font=SANS, align=PP_ALIGN.LEFT,
         anchor=MSO_ANCHOR.TOP, name=None, spacing=None):
    """runs: a string, or a list of paragraphs; a paragraph is a string or a list of (text, {opts}) runs."""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    if name:
        tb.name = name
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    paras = runs if isinstance(runs, list) else [runs]
    for i, para in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if spacing:
            p.space_after = Pt(spacing)
        for t, o in ([(para, {})] if isinstance(para, str) else para):
            r = p.add_run(); r.text = t
            f = r.font
            f.name = o.get("font", font); f.size = Pt(o.get("size", size)); f.bold = o.get("bold", bold)
            f.italic = o.get("italic", False); f.color.rgb = rgb(o.get("color", color))
    return tb


def logo(slide, x, y, scale=1.0, frame="FFFFFF"):
    """The Vahti mark: a box with a yellow tick that overshoots it (the V of vahti)."""
    u = scale * 0.022  # inches per logo unit (viewBox 30 x 26)
    sq = box(slide, x + 1.5 * u, y + 6.5 * u, 18 * u, 18 * u, line=frame, shape=MSO_SHAPE.ROUNDED_RECTANGLE, name="Logo box", lw=2.6 * scale)
    sq.adjustments[0] = 0.12
    fb = slide.shapes.build_freeform(Inches(x + 6 * u), Inches(y + 15.2 * u))
    fb.add_line_segments([(Inches(x + 10.6 * u), Inches(y + 20.4 * u)), (Inches(x + 27.5 * u), Inches(y + 2.5 * u))], close=False)
    tick = fb.convert_to_shape()
    tick.fill.background(); tick.line.color.rgb = rgb(SUN); tick.line.width = Pt(3.6 * scale); tick.shadow.inherit = False
    tick.name = "Logo tick"


def ticket(slide, x, y, w, h, fill=PAPER, clip=True, name="Ticket"):
    t = box(slide, x, y, w, h, fill=fill, name=name)
    if clip:
        box(slide, x + w / 2 - 0.3, y - 0.12, 0.6, 0.22, fill="7A889E", shape=MSO_SHAPE.ROUNDED_RECTANGLE, name="Ticket clip")
    return t


def content_slide(title, notes, n):
    s = prs.slides.add_slide(BLANK)
    s.background.fill.solid(); s.background.fill.fore_color.rgb = rgb(SKY)
    text(s, 0.6, 0.45, 12.1, 0.9, title, size=34, bold=True, color=NAVY, name="Title")
    logo(s, 0.6, 6.93, scale=0.55, frame=NAVY)
    text(s, 1.05, 6.98, 6, 0.3, [[("vahti", {"bold": True, "color": NAVY}), ("  for Valio Aimo · Junction 2026", {"color": MUTE})]], size=11)
    text(s, 11.9, 6.98, 0.8, 0.3, str(n), size=11, color=MUTE, align=PP_ALIGN.RIGHT, name="Slide number")
    s.notes_slide.notes_text_frame.text = notes
    return s


def dark_slide(notes):
    s = prs.slides.add_slide(BLANK)
    s.background.fill.solid(); s.background.fill.fore_color.rgb = rgb(NAVY)
    s.notes_slide.notes_text_frame.text = notes
    return s


def arrow(slide, x, y, w, color=NAVY):
    a = box(slide, x, y, w, 0.34, fill=color, shape=MSO_SHAPE.RIGHT_ARROW, name="Arrow")
    return a


# ---------------------------------------------------------------- 1 Cover
s = dark_slide("Vahti is the guard between buying agents and Valio Aimo. One line: any agent can buy from Valio; "
               "Vahti decides what it may do alone, a person decides the rest, and nobody breaks the hard rules.")
logo(s, 0.85, 0.85, scale=2.4)
text(s, 2.55, 1.0, 6, 1.2, "vahti", size=66, bold=True, color="FFFFFF", name="Wordmark")
text(s, 2.6, 2.05, 6, 0.5, "for Valio Aimo", size=20, color="B9CBE6")
text(s, 0.9, 3.25, 11.0, 2.0, [[("Any agent can buy from Valio.", {})], [("Vahti decides what it may do alone.", {"color": SUN})]],
     size=40, bold=True, color="FFFFFF", name="Tagline")
text(s, 0.9, 6.55, 11.5, 0.4, "Junction 2026 · Valio Aimo: Trusted Trade Agents · valio.psavela.com", size=14, color="B9CBE6")

# ---------------------------------------------------------------- 2 Problem
s = content_slide("Valio can't choose which AI agents its customers use",
                  "Kitchens will buy through their own AI agents: built by themselves, by a vendor, or a general assistant. "
                  "Each agent has its own rules in its own prompt. Valio sees none of them and can't enforce them. "
                  "A fooled or careless agent orders anyway. If Valio doesn't define how agents may buy, someone else's platform will.", 2)
agents = [("A hotel chain's own agent", "built in-house"), ("A general AI assistant", "Claude, ChatGPT, …"),
          ("A procurement vendor's agent", "a black box to Valio"), ("A fooled or careless agent", "it orders anyway")]
for i, (a, b) in enumerate(agents):
    y = 1.65 + i * 1.18
    ticket(s, 0.6, y, 3.6, 0.95, fill=STOP_T if i == 3 else PAPER, clip=False, name=f"Agent {i + 1}")
    text(s, 0.82, y + 0.17, 3.2, 0.4, a, size=16, bold=True, color=STOP if i == 3 else INK)
    text(s, 0.82, y + 0.53, 3.2, 0.3, b, size=13, color=MUTE)
    arrow(s, 4.35, y + 0.31, 0.75, color=STOP if i == 3 else "7A889E")
ticket(s, 5.25, 1.65, 2.5, 4.48, fill=NAVY, clip=False, name="Valio box")
text(s, 5.25, 3.2, 2.5, 1.2, [[("Valio Aimo", {"bold": True, "size": 22})], [("20,000 kitchens", {"size": 14, "color": "B9CBE6"})]],
     color="FFFFFF", align=PP_ALIGN.CENTER)
pts = [("Their rules live in their prompts.", "Valio can't see them, check them, or enforce them."),
       ("A wrong order is still an order.", "Milk for a milk-allergy ward, 10× the quantity, a stale price."),
       ("Someone will define the interface.", "If it isn't Valio, it's another platform, and it owns the customer.")]
for i, (h, b) in enumerate(pts):
    y = 1.7 + i * 1.5
    text(s, 8.35, y, 4.4, 1.3, [[(h, {"bold": True, "size": 18, "color": NAVY})], [(b, {"size": 15})]], spacing=4)

# ---------------------------------------------------------------- 3 Solution
s = content_slide("Vahti is the guard on Valio's side, not inside the agent",
                  "The checks don't live in the agent. They run in Vahti, on Valio's side, on every order, whoever sent it. "
                  "Every request ends in exactly one of three outcomes, and each one names who acts next.", 3)
flow = [("Any agent", "MCP · REST · A2A", PAPER, INK), ("Vahti", "six checks on every order", NAVY, "FFFFFF"),
        ("Valio Aimo", "a signed, fixed-price order", PAPER, INK)]
for i, (a, b, f, c) in enumerate(flow):
    x = 0.9 + i * 4.15
    ticket(s, x, 1.75, 3.3, 1.35, fill=f, clip=False, name=f"Flow {a}")
    text(s, x, 1.95, 3.3, 1.0, [[(a, {"bold": True, "size": 24})], [(b, {"size": 14, "color": "B9CBE6" if f == NAVY else MUTE})]],
         color=c, align=PP_ALIGN.CENTER)
    if i < 2:
        arrow(s, x + 3.42, 2.27, 0.62)
outs = [("The agent orders alone", "Everything checks out and it's within the limits the buyer signed.", GO, GO_T),
        ("A named person decides", "Over the agent's limits, a late delivery, a substitute, a fact the data can't prove.", HOLD, HOLD_T),
        ("Nobody can order it", "A hard rule: a banned allergen, a recall, the buyer's spending ceiling.", STOP, STOP_T)]
for i, (h, b, c, t) in enumerate(outs):
    x = 0.9 + i * 4.15
    ticket(s, x, 3.75, 3.6, 2.15, fill=t, name=f"Outcome {i + 1}")
    text(s, x + 0.3, 4.05, 3.0, 2.1, [[(["✓", "!", "×"][i] + "  " + h, {"bold": True, "size": 19, "color": c})], [(b, {"size": 15})]],
         spacing=8)

# ---------------------------------------------------------------- 4 Six checks
s = content_slide("The first “no” of six checks decides who acts next",
                  "Checks 1 to 3 happen while the request is read: is it a purchase, is it clear, does Valio have it. "
                  "Checks 4 to 6 run in Valio's gateway on every order from any agent: hard rules, facts backed by data, "
                  "and the agent's own authority. Hard rules can't be overridden by anyone, not even the approver.", 4)
ticket(s, 0.6, 1.55, 12.1, 5.15, name="Checks ticket")
rows = [("1", "A purchase", "Is it an order, not a question?", "Answered, nothing ordered", NAVY),
        ("2", "Clear", "Product, amount and date known?", "Back to the kitchen with a question", NAVY),
        ("3", "Valio has it", "Real products, fixed price, in time?", "Back with the nearest option", NAVY),
        ("4", "Safe", "Allergens, recalls, ceiling, signed rules?", "Stopped. No one can override it", STOP),
        ("5", "Proven", "Stock, allergen data, pack size in Valio's data?", "A person checks the fact", HOLD),
        ("6", "Agent's call", "Within what the agent may decide alone?", "A named person approves", HOLD)]
text(s, 1.0, 1.82, 11.4, 0.3, [[("CHECK", {}), ("", {})]], size=11, color=MUTE)
text(s, 3.6, 1.82, 4.5, 0.3, "WHAT IT ASKS", size=11, color=MUTE)
text(s, 8.55, 1.82, 4.0, 0.3, "IF THE ANSWER IS NO", size=11, color=MUTE)
for i, (n, name, ask, no, c) in enumerate(rows):
    y = 2.22 + i * 0.7 + (0.35 if i >= 3 else 0)
    text(s, 1.0, y, 0.5, 0.5, n, size=24, bold=True, color=NAVY, font=MONO)
    text(s, 1.55, y + 0.06, 2.0, 0.45, name, size=17, bold=True, color=INK)
    text(s, 3.6, y + 0.08, 4.8, 0.45, ask, size=15, color=INK, font=MONO)
    text(s, 8.55, y + 0.06, 3.95, 0.45, no, size=15, bold=True, color=c)
    if i == 2:
        box(s, 1.0, y + 0.66, 11.3, 0.03, fill=NAVY, name="Gateway line")
        text(s, 3.15, y + 0.53, 7.0, 0.3, "Checks 4 to 6 run in Valio's gateway, on every order, from any agent",
             size=12, bold=True, color=NAVY, align=PP_ALIGN.CENTER).fill.solid()
        s.shapes[-1].fill.fore_color.rgb = rgb(PAPER)

# ---------------------------------------------------------------- 5 The brief's example
s = content_slide("The brief's own example, end to end",
                  "The exact request from the challenge brief, sent by the hotel's agent. Vahti searches 17,546 products, asks "
                  "Valio for live prices and availability for the shortlist only, gets a signed fixed-price offer and runs the checks. "
                  "€1,010 is over the €500 the agent may spend alone, so it waits for Aino, the head chef, who can approve it on her "
                  "own phone. The ticket also shows the agent's choice and the two alternatives it compared.", 5)
steps = [("“I need 1,000 litres of lactose-free milk by Monday.”", "The hotel's agent sends the brief's example", True),
         ("17,546 → 14 products", "fit, are lactose-free in the data, and arrive Monday", False),
         ("4 live calls to Valio", "price and availability for the shortlist only", False),
         ("A signed, fixed-price offer: €1,010", "not a negotiation; the price is rechecked when ordering", False),
         ("Check 6: over the €500 it may spend alone", "so Aino, the head chef, decides, on her own phone", False),
         ("Approved: a placed order and a receipt", "anyone can re-check the receipt, no sign-in", False)]
for i, (h, b, q) in enumerate(steps):
    y = 1.6 + i * 0.86
    box(s, 0.62, y + 0.07, 0.3, 0.3, fill=PAPER, line=NAVY, name=f"Step box {i + 1}", lw=1.5)
    text(s, 0.62, y + 0.02, 0.3, 0.32, "✓" if i < 4 else ("!" if i == 4 else "✓"), size=16, bold=True,
         color=HOLD if i == 4 else NAVY, align=PP_ALIGN.CENTER)
    text(s, 1.1, y, 6.6, 0.85, [[(h, {"bold": True, "size": 15 if q else 17, "font": MONO if q else SANS,
                                     "color": HOLD if i == 4 else INK})], [(b, {"size": 13, "color": MUTE})]])
s.shapes.add_picture("ticket_1000_c.png", Inches(7.95), Inches(1.45), height=Inches(5.35)).name = "Screenshot: the ticket"

# ---------------------------------------------------------------- 6 Exceptions
s = content_slide("When a request can't be met, Vahti says why",
                  "Four real cases on Valio's mock data, each picked to try on the Safety checks page. Partial availability, "
                  "substitution and delivery constraints are exactly what the robustness criterion names. None of these "
                  "quietly becomes a successful order.", 6)
cases = [("Delivery constraint", "We need 100 litres of lactose-free milk today.", "Arrives Mon 16.11, needed Sat 14.11", "A person decides", HOLD, HOLD_T),
         ("Partial availability", "We need 150 litres of rice drink for Monday.", "Only 109 litres are in Valio's stock", "A person decides", HOLD, HOLD_T),
         ("Substitution", "Order 10 kg of sweet potato fries 475387 by Monday.", "The product asked for is out: Valio's own substitute is offered", "A person signs it off", HOLD, HOLD_T),
         ("Hard rule", "Milk-allergy ward: 100 litres of lactose-free milk by Monday.", "Lactose-free milk is still milk", "Stopped. No one can override it", STOP, STOP_T)]
for i, (k, q, why, who, c, t) in enumerate(cases):
    x, y = 0.6 + (i % 2) * 6.15, 1.6 + (i // 2) * 2.62
    ticket(s, x, y, 5.95, 2.35, fill=t, name=f"Case {i + 1}")
    text(s, x + 0.3, y + 0.22, 5.4, 0.3, k, size=13, bold=True, color=MUTE)
    text(s, x + 0.3, y + 0.55, 5.4, 0.7, "“" + q + "”", size=14, font=MONO, color=INK)
    text(s, x + 0.3, y + 1.35, 5.4, 0.85, [[(why, {"size": 15, "color": INK})], [(("×  " if c == STOP else "!  ") + who, {"size": 16, "bold": True, "color": c})]],
         spacing=3)

# ---------------------------------------------------------------- 7 Trust
s = content_slide("Every claim carries proof. No agent skips a step",
                  "The trust protocol: the minimum an outside agent needs to rely on Valio's claims without a manual check. "
                  "Offers are signed and the signing keys are public; every claim says how far it can be trusted; the receipt "
                  "re-checks itself; the audit trail is hash-chained so edits show. And five things no agent can do, whatever it is told.", 7)
proofs = [("Signed, fixed-price offer", "Ed25519, valid 15 minutes, public key. The price is rechecked when the order is placed."),
          ("Evidence for every claim", "Price, stock, cut-off, allergens: checked live, declared by the supplier, or unknown."),
          ("A receipt anyone can verify", "Scan the QR: signatures, the buyer's rules, who decided. No sign-in."),
          ("One audit trail", "Hash-chained, so edits show. The buyer and Valio see the same record.")]
for i, (h, b) in enumerate(proofs):
    x, y = 0.6 + (i % 2) * 4.05, 1.6 + (i // 2) * 2.45
    ticket(s, x, y, 3.85, 2.2, name=f"Proof {i + 1}")
    text(s, x + 0.28, y + 0.28, 3.3, 1.8, [[(h, {"bold": True, "size": 17, "color": NAVY})], [(b, {"size": 14})]], spacing=6)
ticket(s, 8.85, 1.6, 3.85, 4.65, fill=NAVY, name="No agent can")
never = ["Approve an order", "Set a price", "Act for another buyer", "Break a hard rule", "Order twice from one offer"]
text(s, 9.15, 1.9, 3.3, 0.5, "No agent can", size=19, bold=True, color=SUN)
text(s, 9.15, 2.5, 3.3, 3.6, [[("×  " + n, {})] for n in never], size=16, color="FFFFFF", spacing=12)

# ---------------------------------------------------------------- 8 Numbers
s = content_slide("Tested on Valio's live mock data",
                  "26 trap cases where buying agents typically go wrong all pass with the open model. Of 36 everyday requests, "
                  "22 were ordered by the agent alone; every other one stopped at a named check with a reason (no delivery on "
                  "Sunday, no date given, a new agent's limit, stock Valio can't confirm). 15 exception cases are ready to try on the site.", 8)
stats = [("26/26", "trap cases pass", "real-data cases where buying agents go wrong"),
         ("22/36", "everyday requests ordered alone", "every other one names its check and reason"),
         ("15", "exception cases to try", "partial stock, substitution, late delivery, allergens …"),
         ("394", "automated tests", "every rule, every interface: REST, MCP, A2A")]
for i, (n, l, d) in enumerate(stats):
    x = 0.6 + i * 3.05
    ticket(s, x, 1.85, 2.85, 3.5, name=f"Stat {i + 1}")
    text(s, x + 0.25, 2.25, 2.4, 1.0, n, size=44, bold=True, color=NAVY, font=MONO)
    text(s, x + 0.25, 3.45, 2.4, 2.2, [[(l, {"bold": True, "size": 17})], [(d, {"size": 13, "color": MUTE})]], spacing=6)

# ---------------------------------------------------------------- 9 Valio learns
s = content_slide("Valio also learns which missing data blocks agents",
                  "Agents can only decide alone on facts the data proves. The Valio view shows which missing fields send orders to "
                  "people, per buyer, and which data fix would let the most orders through. The 17 % is the share of the catalog "
                  "with the data a hospital ward's agent needs to decide alone (allergen data among it); it is not a safe-order rate.", 9)
ticket(s, 0.6, 1.7, 4.4, 4.5, fill=NAVY, name="17 percent")
text(s, 0.9, 2.1, 3.9, 1.5, "17%", size=80, bold=True, color=SUN, font=MONO)
text(s, 0.9, 3.65, 3.9, 2.3, "of the catalog has the data a hospital ward's agent needs to decide alone", size=18, color="FFFFFF")
gains = [("Catalog gaps, ranked", "Which missing fields send the most orders to a person: allergen data, stock, delivery history."),
         ("What a data fix unlocks", "Re-run the held orders as if the data existed: how many would have gone through alone."),
         ("The same record as the buyer", "Every decision and its reason, so Valio keeps the customer relationship.")]
for i, (h, b) in enumerate(gains):
    y = 1.75 + i * 1.5
    text(s, 5.5, y, 7.1, 1.3, [[(h, {"bold": True, "size": 19, "color": NAVY})], [(b, {"size": 15})]], spacing=4)

# ---------------------------------------------------------------- 10 Open + production
s = content_slide("Open standards, and a credible road to production",
                  "Everything an agent needs is in open, documented formats, and the AI that reads requests is an open model "
                  "running in Finland. The prototype runs on Valio's mock data; production needs buyer-held keys, a credential per "
                  "agent, Valio's order system and rate limits. Details in docs/PRODUCTION.md.", 10)
ticket(s, 0.6, 1.6, 6.0, 4.0, name="Standards")
text(s, 0.9, 1.9, 5.4, 0.5, "Open today", size=20, bold=True, color=NAVY)
std = ["MCP, REST with OpenAPI, and an A2A agent card", "AP2-style signed checkout on every offer",
       "Buyer rules as W3C Verifiable Credentials", "Orders as Peppol BIS Ordering 3",
       "Ed25519 signatures, public keys (JWKS)", "Gemma 4, an open model running in Finland"]
text(s, 0.9, 2.5, 5.5, 3.7, [[("✓  " + t, {})] for t in std], size=15, spacing=9)
ticket(s, 6.85, 1.6, 5.85, 4.0, name="Next steps")
text(s, 7.15, 1.9, 5.3, 0.5, "Next, for production", size=20, bold=True, color=NAVY)
nxt = [("Buyers hold their own keys", "and sign their rules themselves"), ("A credential per agent", "so trust and holds follow the exact agent"),
       ("Valio's order system", "instead of prototype orders"), ("Rate limits and quotas", "so no agent can scrape prices")]
text(s, 7.15, 2.5, 5.3, 3.7, [[(f"{i + 1}  {h}", {"bold": True}), ("  " + b, {"color": MUTE})] for i, (h, b) in enumerate(nxt)],
     size=15, spacing=13)

# ---------------------------------------------------------------- 11 What's real
s = content_slide("What is real, and what is mocked",
                  "Assumptions are visible: docs/ASSUMPTIONS.md lists every quirk of the mock data and how it is handled.", 11)
real = ["Live calls to Valio's mock price and availability API", "17,546 products from the challenge catalog",
        "Every check, signature, receipt and audit record", "REST, MCP and A2A interfaces, tested end to end"]
mock = ["Buyers and their rules are demo data", "One shared demo sign-in for people",
        "The gateway holds the buyers' signing keys", "Placed orders aren't sent to Valio's order system"]
for i, (h, items, c, t) in enumerate((("Real", real, GO, GO_T), ("Mocked in the prototype", mock, HOLD, HOLD_T))):
    x = 0.6 + i * 6.15
    ticket(s, x, 1.6, 5.95, 3.6, fill=t, name=h)
    text(s, x + 0.3, 1.9, 5.3, 0.5, h, size=20, bold=True, color=c)
    text(s, x + 0.3, 2.55, 5.4, 3.6, [[(("✓  " if i == 0 else "~  ") + it, {})] for it in items], size=16, spacing=12)

# ---------------------------------------------------------------- 12 Close
s = dark_slide("Close on the one line. Then the URL: everything shown runs live at valio.psavela.com.")
logo(s, 0.9, 0.9, scale=1.6)
text(s, 0.9, 2.35, 11.6, 2.4, [[("Valio can't choose which agents its customers use.", {})], [("With Vahti, it doesn't have to.", {"color": SUN})]],
     size=40, bold=True, color="FFFFFF", name="Closing line")
text(s, 0.9, 5.4, 11.5, 1.0, [[("valio.psavela.com", {"bold": True, "size": 22, "color": "FFFFFF"})],
                              [("github.com/Asmoww/junction-valio · Apache-2.0", {"size": 15, "color": "B9CBE6"})]], spacing=4)

prs.core_properties.title = "Vahti for Valio Aimo"
prs.core_properties.author = "Team Vahti, Junction 2026"
prs.save("../vahti-pitch.pptx")
print("saved", len(prs.slides), "slides")

"""Theme candidates as CSS overrides injected over the real pages. Nothing in the repo changes."""

THEMES = {}

THEMES["a_current"] = ("Current: navy bar, orange accent", "")

THEMES["b_valio_white"] = ("Valio white: white bar, one Valio blue, bold titles", """
:root { --navy: #0047bb; --navy-2: #0047bb; --accent: #0047bb; --accent-soft: #e6eefb; --valio: #0047bb; --valio-ink: #003087;
  --bg: #ffffff; --line: #e3e7ee; --ink: #0b1b33; --shadow: none; --radius: 12px; }
.topbar { background: #fff; color: var(--ink); border-bottom: 1px solid var(--line); }
.tabs a { color: #51607a; } .tabs a:hover, .tabs a.on { color: #0047bb; } .tabs a.on { border-bottom-color: #0047bb; border-bottom-width: 3px; }
.brand .dot { background: #0047bb; border-radius: 50%; }
.topbar .right { color: #51607a; }
.page-head h1 { font-size: 34px; font-weight: 800; letter-spacing: -.035em; color: #0b1b33; }
.card { border-color: var(--line); }
.card > h2, .eyebrow { color: #0047bb; letter-spacing: .05em; }
.btn-primary { background: #0047bb; } .btn-primary:hover { background: #003087; }
.metric.dark, .counter { background: #0047bb; } .metric.dark .v { color: #fff; }
.step.final .bullet { background: #0047bb; border-color: #0047bb; }
""")

THEMES["c_navy_night"] = ("Navy night: dark Valio navy, white type, bright blue", """
:root { --navy: #06142e; --navy-2: #7fb0ff; --accent: #3d8bff; --accent-soft: #10264f; --valio: #6ea8ff; --valio-ink: #ffffff; --valio-soft: #16336b;
  --bg: #06142e; --card: #0c1f42; --ink: #eef3ff; --mute: #9fb0cf; --faint: #7084a8; --line: #1d3563;
  --ok: #3ccf8e; --ok-soft: #0f3a2d; --warn: #ffb547; --warn-soft: #3d2c0f; --bad: #ff6b5e; --bad-soft: #43171a; --shadow: none; }
.topbar { background: #06142e; border-bottom: 1px solid var(--line); }
.tabs a.on { border-bottom-color: #3d8bff; } .brand .dot { background: #3d8bff; }
select, textarea, input[type=number], input[type=text] { background: #091a38; color: var(--ink); border-color: var(--line); }
.chip, .flow span, .btn-soft, .funnel div, .mono, .bars .track { background: #132b57; color: #d4defa; border-color: var(--line); }
.chip.navy, .funnel div:last-child, .banner.info { background: #16336b; color: #cfe0ff; }
.rail-b, .step .bullet, .slot.what, .slot.allergy, .switch input::after { background: #0c1f42; }
.slot.what { color: #fff; } .reqcard { background: #123a7a; }
.rail-t .yes { color: #cfe0ff; background: #1b3c78; } .rail-g:not(.fail):not(.skip):not(.done) .rail-n { background: #3d8bff; color: #fff; }
.page-head h1 { font-size: 30px; font-weight: 800; letter-spacing: -.03em; }
.metric.dark .v { color: #fff; } .btn-primary { background: #3d8bff; } .btn-primary:hover { background: #2f74e0; } .metric.dark { background: #3d8bff; border-color: #3d8bff; }
.quote { background: #2a1416; color: #ffd9d4; }
""")

THEMES["d_teal_review"] = ("Outside review: off-white, navy text, teal actions", """
:root { --navy: #142d4e; --navy-2: #087e80; --accent: #087e80; --accent-soft: #eaf5f2; --valio: #142d4e; --valio-ink: #142d4e; --valio-soft: #eaf5f2;
  --bg: #f6f7f4; --ink: #142d4e; --mute: #596779; --line: #dde3e6;
  --ok: #24734b; --ok-soft: #e6f2ea; --warn: #9a610c; --warn-soft: #fff4df; --bad: #b43a32; --bad-soft: #fcedeb; }
.topbar { background: #fff; color: var(--ink); border-bottom: 2px solid #087e80; }
.tabs a { color: #596779; } .tabs a.on, .tabs a:hover { color: #142d4e; } .tabs a.on { border-bottom-color: #087e80; }
.brand .dot { background: #eaa34a; } .topbar .right { color: #596779; }
.reqcard { background: #fff; color: var(--ink); border: 1px solid var(--line); }
.reqcard * { color: inherit; } .slot.what { background: #eaf5f2; }
.card > h2, .eyebrow { text-transform: none; letter-spacing: 0; font-size: 13px; color: #142d4e; }
.btn-primary { background: #087e80; } .btn-primary:hover { background: #066a6c; }
.metric.dark { background: #142d4e; }
""")

THEMES["e_nordic_paper"] = ("Nordic paper: warm paper, black ink, one Valio blue, square and bold", """
:root { --navy: #111111; --navy-2: #0047bb; --accent: #0047bb; --accent-soft: #e6eefb; --valio: #0047bb; --valio-ink: #111;
  --bg: #f3f0e8; --card: #fbfaf6; --ink: #111111; --mute: #5b5a55; --faint: #8b8983; --line: #d9d4c7;
  --radius: 3px; --shadow: none; }
body { font-feature-settings: "tnum"; }
.topbar { background: #f3f0e8; color: #111; border-bottom: 2px solid #111; }
.tabs a { color: #5b5a55; font-weight: 600; } .tabs a.on, .tabs a:hover { color: #111; } .tabs a.on { border-bottom: 3px solid #0047bb; }
.brand .dot { background: #0047bb; border-radius: 2px; } .topbar .right { color: #5b5a55; }
.page-head h1 { font-size: 40px; font-weight: 900; letter-spacing: -.045em; line-height: 1.05; }
.card { border: 1.5px solid #111; }
.card > h2, .eyebrow { color: #111; font-weight: 800; letter-spacing: .08em; }
.chip, .btn, select, textarea, input[type=number], input[type=text], .rail-b, .slot { border-radius: 3px !important; }
.btn-primary { background: #0047bb; } .btn-primary:hover { background: #003087; }
.metric { border: 1.5px solid #111; } .metric.dark { background: #0047bb; border-color: #0047bb; } .metric.dark .v { color: #fff; }
.metric .v { font-size: 34px; font-weight: 900; }
""")

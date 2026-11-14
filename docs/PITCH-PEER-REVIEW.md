# Peer review, Sunday 15 November: 5-minute table pitch

Reviewers arrive at the table and have exactly **five minutes**. Build it around three moments *they* take part in:
they get stopped, they decide as the head chef on their own phone, they try to break it. Everything below is live on valio.psavela.com.

## Before 10:30
1. **Reset the demo** (about 09:45): `ssh psavela@pikkuinen 'bash ~/hackathon-1/scripts/reset_demo.sh'`, or ask Claude.
   Clears orders, the game leaderboard and earned trust; keeps all keys. Don't test on the live site afterwards.
2. **Laptop**, signed in, charger, phone hotspot as backup internet. Tabs in this order:
   1 Buyer agent (Hotel Aurora) · 2 Live · 3 Play · 4 Valio view · 5 Connect.
3. **Live map pre-run once** after the reset (Live → Start) so you know it works; it runs in its own database.
4. **Fallback**: the recorded walk-through video (`state/demo-video.webm` on the server, 110 s, not in git: download it to the laptop), in case the network dies.
5. Save the team's table in the Junction **Teams** tab before peer review starts.

## The 5 minutes

**0:00 · The problem (20 s).** "Soon kitchens' AI agents will buy from Valio Aimo. Would you let one order for a
hospital ward with milk allergies? Valio can't choose which agents connect. Vahti, Finnish for guard, sits between
any agent and Valio: every order passes six checks, and the first 'no' decides who acts next."

**0:20 · They get stopped (35 s).** Buyer agent, click **"On a ward where milk is banned"**. The six checks light
up; it stops at **Safe**: "the product asked for is milk". Point at the sentence with what the agent understood.

**0:55 · They decide (45 s).** Click **"More than the agent may buy alone"**: the brief's own example, "I need 1,000
litres of lactose-free milk by Monday". The agent finds the cheapest that arrives in time, €1,010, but it may spend
only €500 alone, so it waits for the head chef. Press **Approve on phone** and hand the QR to the reviewer: "You're
Aino, the head chef." They see why it came to them, approve on their own phone (no sign-in, single-use link), and
the laptop flips to placed. Then **Receipt**: "Scan this one too if you like. It re-checks Valio's signature on
the price, the buyer's signed rules, who decided and the audit trail, live."

**1:40 · Change one thing (15 s).** Type the same order with "today" instead of Monday. The earliest delivery is
Monday, so it goes to a person with both dates side by side: "A requirement can't quietly disappear. The agent
doesn't call a late delivery a successful order."

**1:55 · They try to break it (65 s).** Play tab, hand over the laptop: "You're a rogue agent. Get milk into the
milk-allergy ward." One try, the token walks the checks and bounces. "Every try runs the real gateway. Nobody has
got through." (The counter shows attempts and 0 breaches.)

**3:00 · Scale (40 s).** Live tab, **Start Monday morning** (started a few seconds earlier is fine). Seventeen
kitchens order at once: green ones fly to Valio, amber wait for a person, red are attacks stopped. "These are real
decisions on Valio's live data. New agents earn trust: they start at a quarter of the limit and earn more with
clean orders; one attack puts an agent on hold."

**3:40 · Why it's real (35 s).** Connect tab, one sentence each: "Any agent connects: MCP, REST, the A2A agent card,
and every offer is also an AP2-style signed checkout. Orders export as Peppol. A buyer's rules come from its own
policy document: the AI drafts them with sources, a person signs." (Rules tab if they ask.)

**4:15 · Close (20 s).** "Agents can buy alone when it's safe, a person decides when it isn't, and nobody can buy
what the rules forbid. Valio stays the trusted party, whoever's agent comes to buy." 25 s spare for questions.

## If they ask
- *Why not just prompt the agent well?* Scoreboard (Valio view → Agents vs. gateway): a good prompt works for a good
  model; Valio can't choose the models. Rules in the gateway hold for every agent.
- *What does Valio get?* Catalog gaps (Valio view): which missing data blocks agents, e.g. a hospital's agent can
  decide alone on only 17 % of the catalog, and which categories to fix first.
- *Is the AI deciding?* No. Gemma 4, an open model running in Finland, reads requests and drafts; every decision is a
  rule in code, logged and signed. The same prompts run on Claude; all 25 trap cases pass on both.
- *Is this a chatbot?* No. The Buyer agent page is one buyer's agent; Vahti is the guard every agent goes
  through, whoever built it (Live and Connect show that).
- *What does an agent never see? How would Valio run it?* docs/PRODUCTION.md: no raw Valio API, no other buyers,
  no supplier terms; buyers hold their own keys in production, per-agent credentials and rate limits.

## Network fallback
The site runs on our server through a Cloudflare tunnel. If the venue network fails, use the phone hotspot; if the
site itself is down, play the downloaded demo-video.webm and tell the story over it.

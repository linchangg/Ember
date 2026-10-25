# Ember: Cal Hacks 13.0 source snapshot
Captured Sat Oct 24, ~4:15 AM PT from hive.hackberkeley.org (prizes "updated Oct 23 9:55 PM", resources "updated Oct 23 8:29 PM"), Devpost, and linked sponsor docs. Hive pages need a browser; this file exists so research agents don't need one.

## Team
- Cody: data + agent (backend/data system owner)
- Abby: physics (fire spread simulator, ensemble, filter)
- Linda: frontend (3D map, time slider, dashboard; mockup exists)
- No repo yet. No stack locked. Demo fire is open: Eaton, another historical fire, or a fully simulated scenario if it gives more "wow".

## Hard schedule (PT)
- Hacking ends / Devpost submission: Sun 8:50-9:00 AM (hard). Must be checked in at the venue.
- Judging: Sun 10:30 AM-12:30 PM, judges come to your table; presentation <=5 min total (Devpost: 3 min pitch + 2 min Q&A). Need a live OR pre-recorded demo, a Devpost video (sponsors judge async), a project image, and a GitHub repo.
- Closing ceremony Sun 2-4 PM.
- Relevant workshops Sat: Bright Data 10 AM (Gate 3), Microsoft "System Design for Deployed Agents" 10 AM (Theater), Apify 11 AM (Gate 3), Anthropic "Multi-agent teams with Claude" 1 PM (Theater), Annapurna Labs 3 PM (Theater), Fetch.ai 3 PM, SpacetimeDB 4 PM (Theater), Convex 4 PM (Gate 3).
- Hive FAQ says teams 1-4; Devpost says 2-4. Judging FAQ: impact, functionality, technical complexity, creativity. Devpost rubric: Application, Intentionality, Functionality, Creativity, Technical Complexity.

## Prizes most relevant to Ember (criteria as published on Hive)
- Cal Hacks: First/Second/Third Overall; Most Technically Complex; Most Creative; Best People-First; Best Planet-First; Hardware; Hacker's Choice.
- SpaceXAI "Make it Legendary" (Cursor mechanical keyboard): "Real space data goes in and a legendary project comes out. ... must be built with Cursor. The more you use Cursor, the more you're likely to win. You must also use the Grok Imagine or Voice API in your project. Bonus points if you use Grok Bot for project planning and team collaboration." CONFLICT: the SpaceXSI starter-pack PDF and MLH page do NOT mention the Grok Imagine/Voice requirement. Treat Hive as binding; confirm at SpaceXSI booth. X Premium codes (Cursor, Grok Bot, Grok) at the booth; Grok Bot download https://x.ai/bot.
- MLH "Build Your Hackathon A-Team with SpaceXSI" (raffle): use Cursor as core coding agent + Grok Bot automation; describe both in project description; opt in to the raffle.
- SpacetimeDB ($2048 / $1024 / $512; NOT on Devpost list): wants Spacetime as "the core real-time backend ... live shared state ... instant sync between users/agents/systems", explicitly names "live dashboards", "shared ops dashboard", "shared simulations", "AI agent coordination in a persistent world state". Must be meaningfully used, not on the side.
- Convex ($5,000 cash on Hive; Devpost said $1,308): "reactive backend platform ... Database, functions, workflow, sync, search, file storage. All TypeScript." No further criteria published. Note: overlaps SpacetimeDB as realtime layer.
- Microsoft "Built for the Real World" (Surface per member + FDE talk + mentorship) and Customer Impact Award: production-minded AI agent for a high-impact domain. Criteria: customer understanding, measurable usefulness, path from prototype to production, responsible design, end-to-end functionality.
- Anthropic (prize "coming soon"): 1) a named community (narrow), 2) a real problem you lived or heard first-hand, 3) it works, usable Monday, 4) thoughtful use of Claude: Claude does something that matters, and you know where it could go wrong for users.
- Cartesia (Meta glasses TBD): voice must be core, not added for the demo. 25% each: problem/originality, use of Cartesia, execution, impact & responsibility. Submit working demo, 2-min demo-first presentation, README (setup, Cartesia features, limitations). Credit code CARTESIACALHACKS; keys at play.cartesia.ai; guide docs.cartesia.ai/calhacks. Products: Sonic TTS, Managed Agents (ASR+LLM+voice).
- Apify ($2,000/$1,000/$800/$400/$400 + Best Actor $400): use Apify meaningfully; Best Actor = build an Actor for this event. Starter folder: Drive "Get Started with Apify" (one guide doc).
- Bright Data (credits + Amazon cards, 1st $1500+$500): live public web data must be essential; must run live in demo; handle blocks/slow/empty results. DISQUALIFIED for mocked or hardcoded data in the demo, scraping behind login, non-public personal data. Hackpack: github.com/ScrapeAlchemist/public-data-hackpack (MCP server, CLI, agent skills).
- MLH Best Use of: Tiger Data (Postgres + time-series, continuous aggregates), Vultr (cloud compute/GPUs, free credits), DigitalOcean (credits via MLH coach), Snowflake API (LLMs via Snowflake REST), MongoDB Atlas, ElevenLabs (voice; promo code via MLH link), Gemini API, Gen AI, Auth0, .Tech domain, GoDaddy domain, Commit Fellowship (opt in + supplemental form). Small prizes (swag/devices). Details: https://www.mlh.com/events/prizes
- Fetch.ai: Hive says $1500/$1000/$500; fetch.ai page says $1250/$750/$500 + two $250 special. Code CALHACKS26AV (Agentverse Premium). Agent-registration criteria not published on the page.
- Factory Droid SDK ($2000): Droid must do real work in the running product on a designed trigger, with scoped permissions and visible plans/steps/results; README explains usage.
- Mana ($5000): use Mana meaningfully while building; link public Mana pages/chats; show one moment Mana changed what/how fast you built. https://mana.space/a/calhacks
- Others likely irrelevant: Hyphenate (finance), Visa (shopping), CRS (credit), Coinbase x402, Capital One Nessie, Meta (human connection), Creao, Bitdeer, Moss, Notability (process notes), Atlassian (agents/security), Elastic (TBA), Armadin (TBA), AfterQuery (TBA), Annapurna Labs (TBA), Figma (doc linked).

## Starter-pack links and how to reach them
- Google Docs: readable from the cloud via WebFetch on /export?format=txt (follow the one redirect).
- Google Drive files/folders: browser only.
- Hive pages: browser only (JSON at /api/live/prizes, /api/live/resources, /api/live/schedule, /api/live/faq).
- Links: Canva doc 1K7Yr2nThpxLt6mALIyIKlaW_fmTacVCFudH-TFM47rA; Factory doc 1fvdIhF-v-a9Q35eiNHcQj6avg0nMUjKk0TPMUync0Rk; Fetch.ai https://www.fetch.ai/events/calhacks-13-0; Creao https://community.creao.ai/CalHacks2026; CRS https://calhacks.crscreditapi.com/; Hyphenate doc 1u4iN8R97ILY3kfHTPKC82zdutHrGx4ze5eCAP-J92w4; Cartesia doc 1lBbGq4zbjx4tcgYP2WmcHu6w_E_N39BC; Moss https://docs.moss.dev/docs; Convex https://docs.convex.dev/home; Apify Drive folder 1RqT3r6i1MqSBAJoAIwXc3lEMmQCnshVt; Atlassian doc 12Y2Vamxbm9W_l2Eo4AM530R7HfNAYCXI-UI6F6Xnggw; Context.dev doc 1CM6dcuojhrRtcokWgspJ8ELeS864UVAneW83eQLliw4; Mana https://mana.space/a/calhacks; Bright Data https://github.com/ScrapeAlchemist/public-data-hackpack; Bitdeer Drive file 1s6HW6Auouwv2XbRsPMmyge6vJRKaXJPZ; Greptile https://www.greptile.com/docs/introduction; Visa doc 1Lgtd0f8mHZy_nWabtz751AkSW5Oj-z1XMhzAwddtfOA; SpaceXSI Drive PDF 15YRK6uPOlHDAnc78oJOoUBQZ9cTHqCiL (contents captured above); Figma doc 1HjzThDGveSLiilIdEPEHq12ZVLZ5eS0T8CQC5_m_-rg; Creao criteria doc 1N1imAn1aBrA1JPoz5X_sjyOsQggUfPvJW2UIIFmdHf4; Coinbase Drive file 12TnPLguduV3gh1zY_TJ176XmVDz2RGW9.

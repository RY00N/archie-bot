# Archie's Trading Rules (v2, Archie's own, 2026-10-05)

Ryan's brief: be in the market at all times, write my own rules, go for max profit, losses are fine because this is paper. Study every day and keep getting better.

Account: Alpaca **paper** only. The bot refuses to run unless the account number starts with PA. Real money only if Ryan asks in words, with hard limits agreed first.
Budget: $10,000 of the $100k paper account (budget = Alpaca equity minus $90k). No margin, no shorting.

## Strategy: momentum rotation, always invested
- Universe: ~55 liquid US stocks and ETFs, including 2 leveraged ETFs (TQQQ, SOXL). Listed in `rules.json`.
- Score each one: 20% of its 1-month return + 50% of its 3-month return + 30% of its 6-month return.
- Only names above their 50-day average and over $10 qualify.
- Hold the top 5, about $2,000 each.
- If fewer than 5 qualify, the empty slots go into SPY, so the money is never idle.

## Research before every buy (Ryan's rule)
- No order goes in without research. Before each check, Archie reads the latest news on every candidate, looks up the next earnings date, analyst moves and big risks, and checks it doesn't pile onto a sector we already own.
- Each name gets a verdict in `research.json`: buy or avoid, a confidence score out of 10, and the reasons.
- The bot only buys names with a "buy" verdict, confidence 7 or higher, researched in the last 3 days. Earnings within 5 trading days means avoid.
- Holdings are re-researched daily. A fresh "avoid" on something we hold sells it.

## Selling
- Every position gets an 8% trailing stop that sits on Alpaca's servers, so it protects us even when Archie isn't checking.
- A holding is sold when it drops out of the top 10 or falls below its 50-day average.
- After any sale, the next check buys the best ranked name to fill the slot.

## Schedule
- Trading checks every weekday at 9:38, 11:38, 1:38 and 3:38 ET: log fills, rotate, add stops, refill empty slots.
- Study session every day, weekends included: review the day, test one new idea on 2 years of history, write the lesson in `journal.md`, and change `rules.json` only when the idea clearly wins.
- Weekly recap every Sunday in the thread: P&L vs SPY, win rate, what changed.

## Honest notes
- The backtest of these rules shows +198% over 2 years vs SPY +37%, but that's flattering: the stock list was picked today, with winners like NVDA and PLTR already known. With ETFs only (no hindsight) it was +52% vs +37%, with a 17% worst drop. Expect real results closer to that, or worse.
- Leveraged ETFs and a 5-stock portfolio can swing 30% from peak. That's the price of chasing max profit.

## Day trading book (separate $10k practice budget)
- Setup: 15-minute opening range breakout, buying only. From 9:45 to 10:30 ET, when a one-minute bar closes above the first 15 minutes' high, buy. The stop goes at the opening-range low, in a bracket order, so both legs live on Alpaca.
- Exit plan (Ryan, 2026-10-06):
  - Once the trade is up 1.5x its risk, the stop locks at +1.5x, so the first goal is banked.
  - Once it's up 2x, the stop locks at +2x.
  - The take profit is at 2.5x.
  - Above 2x, a fade of 0.25x off the best price cashes out.
- Everything is closed by 3:55 ET. Nothing is held overnight.
- Limits:
  - 3 trades open at once, about $3,300 each.
  - 4 trades a day.
  - No new trades after -$200 on the day.
  - Skip a setup whose risk is over 4% of price.
- Research gate: only names approved in that morning's `dayplan.json`. Avoid any name with earnings that day or the next, and any name with a red-flag headline that morning. Never trade a name the swing book holds.
- Backtest (Jul-Oct 2026, 16 liquid names, 1-minute IEX data): +$824 on $10k over 67 days (+8%). There were 203 trades and 48% of them won. The worst drop was $366 and 30 of 67 days were red. Both halves of the period were positive. Honest caveat: the market rose during the whole test, and breakout buying likes rising markets. Expect worse when it chops.

## Change log
- v1 (2026-10-05): first version.
- v2 (2026-10-05): research gate added at Ryan's request. First use: swapped SOXL (too much chip exposure on top of AMD and MU) for MSFT.
- v3 (2026-10-06): day trading book added at Ryan's request, with its own $10k budget and P&L, kept separate from swing.
- v4 (2026-10-06): day exits changed to Ryan's plan: lock +1.5x, then +2x, aim for 2.5x, cash out above 2x on a fade. Backtest $770 vs $818 for the old fixed 1.5x target, with the same worst drop. Basically even, so it was Ryan's call.

## 2026-10-07: day book risk x10 (Ryan's call)
Ryan asked for 10x the day-trading risk. The first trade (AMZN, 12 shares, stop $2.31 below entry) risked about $28.
Day trades now size by dollars at risk: $280 per trade (shares = 280 / (entry - range low)), capped at $31k per position and at the account's cash
(no margin borrowing). Today's AMZN trade at this size = 119 shares = about +$410 instead of +$41. Daily loss stop raised $200 -> $600 (Ryan's number).
Exit plan, entries, research gate unchanged. Paper only.

## 2026-10-07: day entries until 12:30 ET (Ryan: "at least 2 good entries a day, don't force it")
Today only AMZN qualified by 10:30 (TQQQ was held by swing, NVDA had a red-flag headline); AVGO, NFLX, QQQ, SPY broke out later.
Backtest at the new $280 risk sizing, 66 days: last entry 10:30 = +$2,579, max drawdown $1,943; 12:30 = +$3,132, drawdown $1,765,
better in both halves. 2:00pm made a bit more but with a worse drawdown in the second half, so 12:30 it is. Setup, exits and research gate unchanged.

## 2026-10-07: swing trading OFF (Ryan: "fade swing trading, focus on day trading only", "get rid of all positions")
rules.json "swing_enabled": false. The server's next swing check cancels swing stops and market-sells every swing holding
(AMD, MSFT, MU, PLTR, TQQQ), then does nothing more on swing. Day trading continues and can now trade any name.

## 2026-10-08: ALL Alpaca trading OFF (Ryan: "cancel archie's alpaca trading, we're moving to futures")
rules.json day.enabled = false (swing was already off). The server stops opening day trades; code is kept. Archie now studies
Micro Nasdaq (MNQ) and Micro S&P (MES) futures charts instead.

## 2026-10-08: paper FUTURES book (Ryan: "paper trading for now")
futures_sim.py on the server simulates Micro NQ (MNQ, $2/pt) and Micro ES (MES, $5/pt), 1 contract each, on real CME 1-minute
prices (free Yahoo feed, ~10 min late). Setup v1: 15-min opening range breakout, long or short, stop at the other side,
target 1.5R, entries 9:45-11:30 ET, flat 15:55, $1.50 fees per round trip. Unproven (49-day test: MNQ +$262, MES -$806);
it's a paper baseline the nightly study will replace when something better passes. Settings: rules.json "futures".
Moves to an Interactive Brokers paper account once Ryan opens one.
- 2026-10-08 (Ryan: only trade when it's volatile enough to hit the target soon, never force it): paper futures setup v2. 5-min opening range (9:30-9:35) break on a 5-min close, fixed stop MES 12 / MNQ 60 pts, 2R target, out after 90 min if neither hits, entries to 11:30. Trade only on active days: yesterday's regular-session range >= MES 50 / MNQ 400 pts, else skip. Backtest 48 days: MES 19 trades, 68% wins, +$838, both halves positive, drawdown $126 (v1 15-min ORB: -$898). MNQ unproven (only ~10 active days; +$582 but second half -$128).
- 2026-10-08 night: paper futures v2.1. MNQ now uses a 15-min opening range (9:30-9:45) instead of 5 min; MES unchanged. MNQ backtest on active days: 8 trades, 75% wins, +$1,290, both halves positive (+$1,070 / +$220), drawdown $122 (5-min version: +$386, second half -$324). Small sample.
- 2026-10-09 (futures, v2.1 + news check): Ryan: "confirm the news and make sure the chart is volatile". Paper book now reads newsplan.json each morning: no entry on a 5-min close within a scheduled release window (10 min before to 15 min after, e.g. 10:00 ISM/JOLTS, Fed speakers), and a symbol sits out the day on big unscheduled news. Tested and REJECTED today (all made the backtest worse): break-strength candle, opening range vs 20-day normal, first-5-min relative volume, VWAP side (no change), open outside yesterday's range (MNQ 83% wins but less profit, MES much worse).
- 2026-10-10 (Ryan): contract specs confirmed: MES $5/pt, tick 0.25 = $1.25; MNQ $2/pt, tick 0.25 = $0.50 (paper bot already uses these). Execution rule: every order function must support brackets, and on a funded account the stop + target go on as an automatic OCO pair the moment the entry fills, no manual setup. The paper bot already works this way (stop and target set at entry, first one hit cancels the other). Applies to any future broker code (Interactive Brokers).
- 2026-10-12 after the close: paper futures v2.2. MNQ stop 60 -> 80 pts and target 2R -> 3R (240 pts); MES unchanged (5-min range, stop 12, 2R). 2-yr proxy test MNQ +$6,712 dd $1,599 with both halves positive (v2.1: +$982, first half negative); 10-week futures test +$1,155 (v2.1 +$1,168). First day live: Tue 10/13.
- 2026-10-13 (Ryan: "I need Archie to start trading... don't force anything"; picked "Yes, MES daily"): paper futures v2.3. MES activity filter lowered 50 -> 20 pts, so MES trades almost every day (same 5-min range, stop 12, 2R). Both tests stay positive with both halves up: 2-yr proxy +$2,654 dd $924 (v2.2 +$2,507 dd $894); 10-week futures 42 trades +$528 dd $292 (v2.2 20 trades +$775). MNQ keeps 400: lower levels lose on real futures (300: +$204 with second half -$533; 200: -$1,512).
- 2026-10-14: PAPER prop practice added (prop_sim.py, rules.json "prop"): 2 simulated $50k Topstep-style accounts, 15 MES each, MES news rules (news day, no FOMC, quiet pre-open <0.3%, prior range 20-150, RSI/volume). Feed key "prop". Ryan chose "Paper prop first".
- 2026-10-15 (Ryan approved "Yea"): paper futures v2.3 + MNQ drop skip. MNQ sits out the day when today's open is 1%+ below yesterday's close, or yesterday closed 2%+ down (rules.json futures "skip_after_drop"). 2-yr proxy: $3,819 -> $5,170, drawdown $2,348 -> $1,296, skipped days lost money in both halves. MES unchanged.
- 2026-10-15 (Ryan picked "One each" from the all-markets study): paper prop practice v2. Prop 1 = 6 MES, 30-min opening range (9:30-10:00) break on a 5-min close with above-average volume, stop 0.2 x 14-day average range, 1R target, out after 4 hours, entries to 11:30. Prop 2 = 6 MYM, prior-day high/low sweep that closes back inside between 9:30-10:30, stop just past the sweep, 1R target. Both: news days only, no FOMC, quiet pre-open. Sizes picked for 0% blown evals in the test (MES 95% pass, MYM 42% pass). Replaces 15 MES. Default plans in prop_sim.py DEFAULTS.
- 2026-10-16 (Ryan picked "Yes, 80 min"): MNQ paper trades close after 80 minutes if neither stop nor target is hit (was 90). MES stays at 90 (rules.json futures "time_stop_min" is now per symbol). 2-yr proxy MNQ: 90 min $3,856 dd $2,342 -> 80 min ~$4,900 dd $2,002; 75 and 85 also beat 90; 10-week $993 -> $1,093.
- 2026-10-16: News watcher (newswatch.py) checks mega-cap/index headlines every minute 6am-4pm ET and flags shocks on the desk News bot (alert only, no trading effect). New desk bot "spotter" calls out setups as price nears or pokes through the opening range (info only).
- 2026-10-16 (Ryan: "Start using 1m charts"): futures entries now trigger on any 1-minute close outside the opening range (rules.json futures.entry_bar_min 1; was 5-min closes). 10-day 1m check: MNQ identical, MES +$74 vs 5-min (thin sample); 2-yr 1m proxy test running.
- 2026-10-16 (Ryan picked "5-min entries"): entries back on 5-minute closes (entry_bar_min 5) after the 2-yr 1-minute test lost (MES -$282 vs +$2,110, MNQ +$1,850 vs +$3,801). Spotter still watches 1-minute prices.
- 2026-10-16 (Ryan approved): MNQ gap-fill PRACTICE lab book (gapfill_sim.py, rules.json lab_books.gapfill, pnl.json "gapfill"), separate $10k book, starts Mon 10/19. Fade 0.3-1% opening gaps toward yesterday's close when the first 5-min bar already moves that way; enter 9:35, target prior close, stop 1x gap beyond the open, out 11:30.
- 2026-10-16 Ryan: "If something fails automatically replace it with another until it succeeds." The daily lab now keeps testing (up to 9 a day) until one passes, auto-adds passing strategies as paper lab books, and auto-retires lab books that fail live (10+ trades net negative, or drawdown > 1.5x backtest). Main ORB and prop books still need Ryan's OK to change.
- 2026-10-16 night (auto-added under Ryan's "automatically replace it until it succeeds" rule): MNQ drop-day long PRACTICE lab book (droplong_sim.py, rules.json lab_books.droplong, pnl.json "droplong"), $10k, starts Mon 10/19. Only on days the main book skips MNQ for a drop; long-only 15-min ORB break, stop 100, 2R (tuned 10/16 10:38pm from 80/3R; all 6 stop/target combos positive), 80-min time stop. Backtest 10wk 4 tr +$524, 2yr 53 tr +$2,878 dd $551, both halves up.
- 2026-10-17 (Ryan: "stop asking me, add and remove everything until you're profitable"): main paper book now SKIPS the day after an NR7 day (yesterday's RTH range % = narrowest of last 7), both MNQ and MES (rules.json futures.skip_after_nr7). Backtest: MNQ 10wk $1,093 vs $931, 2yr $4,330 vs $3,730; MES 10wk $612 vs $496, 2yr $2,672 vs $2,294, lower dd. NR4/NR10 variants weaker (some fit risk). From now on Archie changes all PAPER books without asking; pings Ryan only after 2 profitable trading days in a row.
- 10/17 (auto, no-asking rule): gapfill lab book min gap 0.3% -> 0.2%. Backtest 10wk 18 tr $886 dd $231 (was $714/$244); 2yr 149 tr 71% $6,470 dd $1,221 (was $5,160/$1,463), both halves up. Retire threshold now 1.5x $1,221.
10/17 (auto, no-asking rule): MNQ main ORB gets a TEMA(20) slope filter: a breakout close only counts if TEMA(20) on 5-min closes rose (long) / fell (short) over the 2 bars before the break bar; otherwise wait for the next qualifying close. Backtest 2yr $5,353 dd $2,096 (live $4,331/$2,458), both halves up; 10wk $1,568 dd $171 (live $1,093/$495). TEMA(30) similar; TEMA(10) worse. MES gets worse with it, so MNQ only.
10/17 (auto, weekend deep study): MNQ target 3R -> 2.5R now that the TEMA filter is on. Grid with TEMA beats no-TEMA in all 6 stop/target cells (70-90 pts x 2.5/3R) on both tests. 80/2.5R vs 80/3R: 2yr $5,362 dd $1,859 vs $5,353/$2,096 (both halves up), 10wk $1,574 vs $1,568, dd $171 both. 90/2.5R makes more ($6,218) but 10wk dd a bit worse, so not taken.
10/17 18:46Z (Ryan: "Put futures trading aside, let's do this on alpaca"): futures main, gapfill, droplong and prop paper books PAUSED (enabled=false, code and settings kept). Next: Alpaca paper QQQ/SPY options day bot.
10/17 19:10Z (Ryan: "let's do this on alpaca", copy nickafischer's bots): NEW optbot.py = QQQ options scalper on the Alpaca PAPER account. His rules from 10 reels: 1-min chart; a candle close across VWAP, across EMA50, or out of the 15-min range -> buy 1DTE ATM call (above) / put (below); 5% profit lock then trail; stop; daily goal $1,500 / loss brake $2,500; first 30 min only. Ours: $12k per trade, stop -12%, lock +5% keeps +2% then trails 10% off the peak, 30-min max hold, max 6 trades, entries 9:31-10:00. Backtest (options/scalp_bt.py, real Alpaca option bars Feb 2024-Oct 2026, 665 days): loses $113k with 2c slippage + 3c fees per side; +$69k with 1c slip (but last year -$4k, last 10wk -$22k); +$228k with zero costs. Edge = fill quality. Running it on paper Mon 10/19 to measure real fills. Rules in rules.json "options".
10/17 19:20Z (Ryan: "make the bot read the charts every second"): optbot now runs in its own 1-second thread (ensure_thread from daytrader.tick): exits checked every second on the option bid, chart read within ~1-2 s of each 1-min close.

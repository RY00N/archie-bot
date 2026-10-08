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

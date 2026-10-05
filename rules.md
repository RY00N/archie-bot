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

## Change log
- v1 (2026-10-05): first version.
- v2 (2026-10-05): research gate added at Ryan's request. First use: swapped SOXL (too much chip exposure on top of AMD and MU) for MSFT.

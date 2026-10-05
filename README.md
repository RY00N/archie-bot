# archie-bot

Archie's paper-trading bot for Alpaca (paper account only; refuses to run otherwise).

- `archie_live.py`: always-on loop for the server. Polls prices every 3s, refills a slot within seconds when a trailing stop sells, rotates every 15 min, and `git pull`s every 5 min.
- `archie_bot.py`: momentum rotation logic. Buys only names with a fresh "buy" verdict in `research.json`.
- `rules.json` / `rules.md`: settings Archie tunes in his nightly study sessions. `research.json`: his per-stock research verdicts.
- `backtest.py`: tests rule changes on 2 years of history.

Server setup (Ubuntu, run as root in the droplet console):

    bash <(curl -fsSL https://raw.githubusercontent.com/RY00N/archie-bot/main/server/setup.sh) RY00N/archie-bot

It asks for the Alpaca paper keys and stores them only in /etc/archie.env on the server. Live log: `journalctl -u archie -f`.

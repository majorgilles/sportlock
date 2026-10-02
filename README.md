# sportlock

Scheduled training locks for Omarchy: at set times the desktop locks into a training session and
unlocks when you finish it or the time runs out. See [DESIGN.md](DESIGN.md) for the full plan.

**Status: milestone 1** — schedule, lock screen, override, placeholder session.

## Install

```bash
./install.sh
$EDITOR ~/.config/sportlock/config.toml   # set your schedule, then enabled = true
sportlock reload
```

## Use

```bash
sportlock status            # lock state, next lock, trained today
sportlock test              # lock now for 1 minute (cannot be overridden)
sportlock log               # recent locks and how they ended
sportlock override <phrase> # start the override countdown (also on the lock screen)
```

## If you are ever stuck

The locker fails open: stopping the service releases the lock within ~15 seconds.

1. Switch to a TTY with `Ctrl+Alt+F3` and log in.
2. Run `systemctl --user stop sportlock`.
3. Switch back with `Ctrl+Alt+F1` (or F2).

## Develop

```bash
python3 -m unittest        # from the repo root
```

# 4PM

A small always-on-top task window with one strict rule: every day's deadline is 4:00 PM.

- **4:00 PM flip.** At 4:00 PM the list becomes tomorrow's. Ticked tasks leave. Unticked tasks carry over in red and show how many days late they are.
- **No repeats.** Nothing is ever created for you. A task only exists if you typed it that day.
- **Morning banner.** A red "WRITE TODAY'S TASKS" banner stays until you type a new task. Carried-over tasks don't clear it, and the window won't stay minimised until you do.
- **Local only.** Tasks are saved to `tasks.json` in a `FourPM` folder in your home folder. No account, no internet.

It is one Python file with no extra libraries.

## Run it

You need Python 3 with tkinter.

**Mac (Homebrew Python):** Homebrew ships tkinter separately, so install it once:

```
brew install python-tk@3.13
python3.13 fourpm.py
```

**Windows (not tested):** Python from python.org already includes tkinter:

```
py fourpm.py
```

## Open it at every login

```
python3.13 fourpm.py --install
```

This copies the program into the `FourPM` folder and registers it to start at login. To undo it:

```
python3.13 fourpm.py --uninstall
```

## Change the rules

The settings are at the top of `fourpm.py`:

- `DEADLINE_HOUR = 16` is the hour the day flips (16 = 4:00 PM).
- `NAG_FROM_HOUR = 5` is the hour the red banner starts in the morning.

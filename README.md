# hp50g

Use an HP 50g from the Mac over its USB cable.

1. Plug in the calculator (mini-USB port) and turn it on.
2. On the calc, type `XSERV` and press ENTER.
3. Run `./hp` for a REPL, or `./hp --help` for the commands.

The first run creates `.venv` (and installs Homebrew `libusb` if missing).

```
$ ./hp eval "100 !"
93326215443944152681699238856266700490715968264381621468592963895217599993229915608941463976156518286253697920827223758251185210916864000000000000000000000000
$ ./hp
HP 50g REPL — :help for help
  (empty stack)
hp> 3 4
  2: 3
  1: 4
hp> +
  1: 7
```

If the calc shows an error and leaves server mode, press ON and run
`XSERV` again. Protocol notes are in `CLAUDE.md`.

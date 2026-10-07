# What pcbkit needs on a Mac. `brew bundle --file=Brewfile` installs it; `pcbkit doctor`
# checks the result and says what is still missing.

# KiCad 10: the schematic and board tools, and the Python that can import pcbnew.
# The cask asks for your password.
cask "kicad"
# Java, for Freerouting, the autorouter. Homebrew leaves it off PATH; pcbkit finds it.
brew "openjdk@21"
# SPICE, for the checks that simulate a circuit.
brew "ngspice"
# rsvg-convert, which draws the assembly drawing and the pictures of the board.
brew "librsvg"
# Runs pcbkit, and builds each board project's Python environment.
brew "uv"

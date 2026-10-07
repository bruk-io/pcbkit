---
description: >-
  Fill in a PCBWay order for the current pcbkit board, from the numbers `pcbkit quote`
  prints, in the user's browser, and stop at sign-in, at every file upload and before
  payment. Bare boards, with or without assembly. The user starts it with
  /pcbkit:order-pcbway; Claude never starts an order on its own.
disable-model-invocation: true
argument-hint: "[boards to make] [boards to assemble]"
---

# Ordering from PCBWay

Arguments given: $ARGUMENTS (boards to make, then boards to assemble). If either is
missing, ask. Assembling fewer boards than you make is normal and allowed.

## Before the browser

1. Check that the fab files are current: `pcbkit finalize` ran after the last change to
   the board, and `pcbkit check` passes. If not, say so and stop; an order made from stale
   files is paid for.
2. Ask what the assembler needs to know that the files do not say: parts the user supplies
   themselves, parts to leave unfitted, polarity beyond the assembly drawing, parts that
   must not be substituted. Write the answer, 600 characters at most, to `order-notes.txt`
   in the project folder (not in out/ or fab/, which pcbkit rewrites). Do not invent
   requirements.
3. Run `pcbkit quote --fab-qty N --assembled M --notes order-notes.txt`. Add
   `--self-solder-tht` when the user will solder the through-hole parts. The command fails
   and says by how much if the notes are too long. Its output is the only source of the
   numbers you enter.

## In the browser

Use the Claude in Chrome tools. Without them, print the quote and these stops for the
user to follow by hand, and do nothing else.

Open pcbway.com and its instant quote, read the page, and find each field by its label,
never by position: the form changes. Enter the values from the quote (layers, size, thickness,
copper weight, finish, track and spacing, minimum hole, quantity, then the assembly
numbers). Leave every other field at its default and tell the user the ones that cost
money or time (shipping, lead time). Decline upgrades, coupons and extra services.

- Assembly quantity is the number to assemble (M), not the number of boards made (N).
- If the form asks whether alternative or substitute parts may be used, answer No. If
  PCBWay later proposes a substitute, show it to the user; accepting one is their call.
- If the page computes something different from the quote (a unique-part count after the
  BOM is read, say), stop and show both numbers instead of overriding either.

## Three places you always stop

Say exactly what is done and what is left, then wait for the user to say they are ready.

1. **Credentials.** At a sign-in, registration or account page, the user signs in
   themselves in the browser. Never type, read, store or ask for a password or code.
2. **Uploads.** At each file upload, name the file and give its full path, and let the user
   pick it in the dialog: the Gerber zip, the BOM, the centroid, all in out/fab/. Do not
   try to choose a file for them.
3. **Payment.** Stop before any button that pays or places the order, and at any page that
   asks for card or billing details. Summarise the order: each field you set, the total the
   page shows, the shipping method. The user presses the button.

## Afterwards

Give the user a table of every value entered next to the quote's value for it, the notes
that went in, and anything left at a default that they may want to change.

# Pablo — Game Rules

The reference the code is built from. If the code and this file disagree, this file wins and the code has a bug.

## Players and deck

- 3 or 4 players (chosen when the room is created).
- 40 cards: **1, 2, 3, 4, 5, 6, 7, Jack, Queen, King** in the four suits. No jokers.

| Card | Points |
|---|---|
| 1 – 7 | its number |
| Jack, Queen, King | 10 |
| **King of Hearts** | **0** |

## Start of a round

1. Each player gets **4 cards face-down**.
2. Each player may look at **any 2 of their 4 cards**, once. After that they play from memory.
3. The rest of the cards form the face-down **deck**. The **middle pile** starts empty.

## Your turn — do exactly one of these

**A. Throw a pair.** Pick two of your face-down cards you believe have the same number and throw them into the middle pile.
- If they match, both stay in the middle.
- If they don't, both go back to your hand **and** you take a **penalty card** (face-down, unseen) from the deck.

**B. Draw.** Take the top card of the deck and look at it (only you see it). Then either:
- **throw it** straight into the middle pile, or
- **swap it** with one of your face-down cards. The card you replace goes face-up into the middle pile (everyone sees it).

**C. Call "Pablo".** Only on your own turn, and the round ends immediately (see *Scoring*).

## Card powers

A power triggers when a **Jack** or **Queen** goes into the middle pile on your turn, by any turn action:
- you throw the drawn card,
- it is the card you swapped out of your hand, or
- you throw a matching **pair** of Jacks or Queens (the power is used **once** for the pair).

- **Jack** — swap one of your cards with one card of another player, **without looking** at either.
- **Queen** — look at **one card anywhere** on the table (yours or another player's).
- **King** — no power. (The King of Hearts is simply worth 0.)

Cards thrown by **cutting** do not trigger powers.

## Cutting (out of turn)

- Every time cards land in the middle pile (a single card, a swapped-out card or a pair), a **3-second cut window** opens.
- During the window, **any** player may throw one of **their own** face-down cards that has the **same number** as the card on top of the pile.
- Several players can cut in the same window, and a player may cut more than once.
- **Wrong cut:** the card goes back to your hand **and** you take a penalty card.
- The order of cuts is the order in which they **reach the server** (network timing decides races).
- When the window closes, play continues with the player **next to whoever threw the last card** (the last successful cutter, or the original thrower if nobody cut).

## End of a round

A round ends when:
- a player has **no cards left** (they win the round instantly, whether they emptied their hand on their turn or by cutting), or
- a player calls **Pablo**. Everyone reveals their cards immediately.

If the deck runs out, the middle pile, except its top card, is shuffled to become the new deck.

## Scoring

Each player's **hand total** is the sum of the points of the cards still in front of them.

| Situation | Round score |
|---|---|
| Player emptied their hand | that player: **−5**, everyone else: their hand total |
| Pablo called, caller has the **strictly lowest** total | caller: **−5**, everyone else: their hand total |
| Pablo called, someone else has the strictly lowest total | caller: hand total **+50**; the lowest player: **−5**; everyone else: hand total |
| Pablo called and the lowest total is **tied** | nobody gets −5 or +50; everyone scores their hand total |

Scores add up from round to round and can be negative.

## End of the match

After a round, if any player's total score is **100 or more**, the match ends. The player with the **lowest total score** wins.

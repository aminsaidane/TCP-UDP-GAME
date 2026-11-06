"""Cards and the Pablo deck (40 cards: 1-7, J, Q, K in four suits)."""
import random
from dataclasses import dataclass

RANKS = ("1", "2", "3", "4", "5", "6", "7", "J", "Q", "K")
SUITS = ("H", "D", "C", "S")  # Hearts, Diamonds, Clubs, Spades
SUIT_NAMES = {"H": "Hearts", "D": "Diamonds", "C": "Clubs", "S": "Spades"}
RANK_NAMES = {"J": "Jack", "Q": "Queen", "K": "King"}


@dataclass(frozen=True)
class Card:
    rank: str  # one of RANKS
    suit: str  # one of SUITS

    def __post_init__(self):
        if self.rank not in RANKS or self.suit not in SUITS:
            raise ValueError(f"invalid card {self.rank}{self.suit}")

    @property
    def points(self) -> int:
        """Score value: 1-7 face value, J/Q/K 10, King of Hearts 0."""
        if self.rank == "K" and self.suit == "H":
            return 0
        if self.rank in ("J", "Q", "K"):
            return 10
        return int(self.rank)

    @property
    def power(self):
        """'J' (blind swap), 'Q' (peek) or None."""
        return self.rank if self.rank in ("J", "Q") else None

    def matches(self, other: "Card") -> bool:
        """Same number (pairs and cuts ignore the suit)."""
        return self.rank == other.rank

    def code(self) -> str:
        """Compact form used on the wire, e.g. '7H', 'QS'."""
        return self.rank + self.suit

    @classmethod
    def from_code(cls, code: str) -> "Card":
        return cls(code[:-1], code[-1])

    def __str__(self):
        return f"{RANK_NAMES.get(self.rank, self.rank)} of {SUIT_NAMES[self.suit]}"


def new_deck(rng: random.Random | None = None) -> list[Card]:
    """A freshly shuffled 40-card deck. The top of the deck is the END of the list."""
    deck = [Card(r, s) for s in SUITS for r in RANKS]
    (rng or random).shuffle(deck)
    return deck

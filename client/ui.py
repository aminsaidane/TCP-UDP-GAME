"""Small pygame widgets (buttons, text fields) and the colour palette."""
import pygame

# Palette: a card table — green felt, cream paper, brass accents.
FELT = (17, 66, 50)
FELT_DARK = (10, 44, 33)
FELT_LIGHT = (28, 88, 67)
SEAT_TEXT = (100, 150, 126)
PAPER = (245, 238, 220)
PAPER_DARK = (226, 215, 190)
INK = (33, 30, 26)
INK_SOFT = (110, 102, 90)
BRASS = (212, 162, 76)
BRASS_DARK = (168, 122, 46)
RED = (192, 57, 43)
GREEN_OK = (46, 140, 87)
WHITE = (255, 255, 255)
DISABLED = (150, 150, 140)

_fonts = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        f = pygame.font.Font(None, size)
        f.set_bold(bold)
        _fonts[key] = f
    return _fonts[key]


def text(surface, value, pos, size=28, color=INK, bold=False, center=False, right=False):
    img = font(size, bold).render(str(value), True, color)
    rect = img.get_rect()
    if center:
        rect.center = pos
    elif right:
        rect.topright = pos
    else:
        rect.topleft = pos
    surface.blit(img, rect)
    return rect


def panel(surface, rect, color=PAPER, radius=14, shadow=True):
    rect = pygame.Rect(rect)
    if shadow:
        pygame.draw.rect(surface, FELT_DARK, rect.move(0, 5), border_radius=radius)
    pygame.draw.rect(surface, color, rect, border_radius=radius)
    return rect


def felt_background(surface):
    """The green table: dark wooden rim, felt inside."""
    surface.fill(FELT_DARK)
    w, h = surface.get_size()
    pygame.draw.rect(surface, FELT, (10, 10, w - 20, h - 20), border_radius=28)


class Button:
    def __init__(self, rect, label, on_click, kind="primary", enabled=True):
        self.rect = pygame.Rect(rect)
        self.label = label
        self.on_click = on_click
        self.kind = kind            # "primary" (brass), "secondary" (paper), "danger" (red)
        self.enabled = enabled
        self.visible = True

    def handle(self, event):
        if (self.visible and self.enabled and event.type == pygame.MOUSEBUTTONDOWN
                and event.button == 1 and self.rect.collidepoint(event.pos)):
            self.on_click()
            return True
        return False

    def draw(self, surface):
        if not self.visible:
            return
        hover = self.enabled and self.rect.collidepoint(pygame.mouse.get_pos())
        if not self.enabled:
            bg, fg, edge = PAPER_DARK, DISABLED, PAPER_DARK
        elif self.kind == "primary":
            bg, fg, edge = (BRASS if not hover else (226, 180, 96)), INK, BRASS_DARK
        elif self.kind == "danger":
            bg, fg, edge = (RED if not hover else (214, 76, 61)), WHITE, (140, 40, 30)
        else:
            bg, fg, edge = (PAPER if not hover else WHITE), INK, PAPER_DARK
        pygame.draw.rect(surface, edge, self.rect.move(0, 3), border_radius=10)
        pygame.draw.rect(surface, bg, self.rect, border_radius=10)
        text(surface, self.label, self.rect.center, size=26, color=fg, bold=True, center=True)


class TextInput:
    def __init__(self, rect, placeholder="", password=False, max_len=24, value=""):
        self.rect = pygame.Rect(rect)
        self.placeholder = placeholder
        self.password = password
        self.max_len = max_len
        self.value = value
        self.focused = False

    def handle(self, event):
        """Returns True if the event was used."""
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.focused = self.rect.collidepoint(event.pos)
            return self.focused
        if not self.focused:
            return False
        if event.type == pygame.TEXTINPUT:
            self.value = (self.value + event.text)[: self.max_len]
            return True
        if event.type == pygame.KEYDOWN and event.key == pygame.K_BACKSPACE:
            self.value = self.value[:-1]
            return True
        return False

    def draw(self, surface, label=None):
        if label:
            text(surface, label, (self.rect.x, self.rect.y - 22), size=22, color=INK_SOFT, bold=True)
        pygame.draw.rect(surface, WHITE, self.rect, border_radius=8)
        pygame.draw.rect(surface, BRASS if self.focused else PAPER_DARK, self.rect, width=2, border_radius=8)
        shown = "•" * len(self.value) if self.password else self.value
        if shown:
            img = font(28).render(shown, True, INK)
        else:
            img = font(28).render(self.placeholder, True, DISABLED)
        clip = surface.get_clip()
        surface.set_clip(self.rect.inflate(-12, 0))
        surface.blit(img, (self.rect.x + 10, self.rect.centery - img.get_height() // 2))
        if self.focused and (pygame.time.get_ticks() // 500) % 2 == 0:
            x = self.rect.x + 12 + (img.get_width() if shown else 0)
            pygame.draw.line(surface, INK, (x, self.rect.y + 9), (x, self.rect.bottom - 9), 2)
        surface.set_clip(clip)


def focus_next(inputs):
    """Tab: move the focus to the next text field."""
    current = next((i for i, f in enumerate(inputs) if f.focused), -1)
    for f in inputs:
        f.focused = False
    inputs[(current + 1) % len(inputs)].focused = True

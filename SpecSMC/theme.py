"""Tk rendering for EPRWizard-style rounded controls and panels."""

import math
import tkinter as tk


BACKGROUND = '#f3f6fa'
BORDER = '#dce4ee'
BLUE = '#0071BC'
PACIFIC = '#054169'


def _rounded_image(master, fill, outline, radius=8, size=32):
    image = tk.PhotoImage(master=master, width=size, height=size)
    for y in range(size):
        for x in range(size):
            dx = max(radius - x - .5, x + .5 - (size - radius), 0)
            dy = max(radius - y - .5, y + .5 - (size - radius), 0)
            distance = math.hypot(dx, dy)
            if distance <= radius:
                edge = distance > radius - 1 or x == 0 or y == 0 or x == size - 1 or y == size - 1
                image.put(outline if edge else fill, (x, y))
    return image


def install_shapes(master, style):
    """Keep native ttk interaction while replacing its rectangular artwork."""
    images = []
    style.configure('TNotebook', borderwidth=0, relief='flat',
                    bordercolor=BACKGROUND, lightcolor=BACKGROUND, darkcolor=BACKGROUND)

    def shape(fill, outline, radius=8, size=32):
        image = _rounded_image(master, fill, outline, radius=radius, size=size)
        images.append(image)
        return image

    card = shape('#ffffff', BORDER)
    style.element_create('Card.border', 'image', card, border=12, sticky='nsew')
    style.layout('TLabelframe', [('Card.border', {'sticky': 'nsew'})])

    def checkbox(selected=False, disabled=False, hover=False):
        fill = '#E5F1F8' if disabled else BLUE
        outline = '#cbd6e2' if disabled else BLUE if hover or selected else '#a0afbf'
        image = shape(fill if selected else '#ffffff', outline, radius=3, size=16)
        if selected:
            ink = '#737373' if disabled else '#ffffff'
            for x, y in ((4, 8), (5, 9), (6, 10), (7, 9), (8, 8), (9, 7), (10, 6), (11, 5)):
                image.put(ink, to=(x, y, x + 2, y + 2))
        return image

    unchecked = checkbox()
    checked = checkbox(selected=True)
    disabled_off = checkbox(disabled=True)
    disabled_on = checkbox(selected=True, disabled=True)
    hover_off = checkbox(hover=True)
    style.element_create('Modern.check', 'image', unchecked,
                         ('disabled', 'selected', disabled_on), ('disabled', disabled_off),
                         ('selected', checked), ('active', hover_off),
                         width=22, sticky='w')
    style.layout('TCheckbutton', [('Checkbutton.padding', {'sticky': 'nsew', 'children': [
        ('Modern.check', {'side': 'left', 'sticky': 'w'}),
        ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [
            ('Checkbutton.label', {'sticky': 'w'})]})]})])
    style.configure('TCheckbutton', background='#ffffff', foreground='#313331', padding=(0, 3))
    style.map('TCheckbutton', background=[('disabled', '#ffffff'), ('active', '#ffffff'),
                                         ('!disabled', '#ffffff')],
              foreground=[('disabled', '#9aa6b5'), ('!disabled', '#313331')])
    for name, fill, text, outline, hover in (
        ('TButton', '#ffffff', '#054169', '#cbd6e2', '#E5F1F8'),
        ('Primary.TButton', BLUE, '#ffffff', BLUE, PACIFIC),
        ('Danger.TButton', '#fff7f7', '#a12626', '#e6b9b9', '#ffe8e8'),
    ):
        normal = shape(fill, outline, radius=8, size=20)
        active = shape(hover, outline, radius=8, size=20)
        disabled = shape('#f1f3f6', '#e1e6ec', radius=8, size=20)
        element = name + '.rounded'
        style.element_create(element, 'image', normal,
                             ('disabled', disabled), ('pressed', active), ('active', active),
                             border=9, padding=0, sticky='nsew')
        style.layout(name, [(element, {'sticky': 'nsew', 'children': [
            ('Button.padding', {'sticky': 'nsew', 'children': [
                ('Button.label', {'sticky': 'nsew'})]})]})])
        style.configure(name, foreground=text, padding=(10, 4), width=0,
                        font=('Segoe UI', 10))
        style.map(name, foreground=[('disabled', '#9aa6b5'), ('!disabled', text)])

    def tab_image(fill, underline=None):
        image = tk.PhotoImage(master=master, width=24, height=24)
        image.put(BACKGROUND, to=(0, 0, 24, 24))
        # Round only the top corners; the bottom joins the page cleanly.
        for y in range(24):
            inset = math.ceil(7 - math.sqrt(49 - (7 - y - .5) ** 2)) if y < 7 else 0
            image.put(fill, to=(inset, y, 24 - inset, y + 1))
        if underline:
            image.put(underline, to=(0, 22, 24, 24))
        return image

    selected = tab_image('#ffffff', BLUE)
    hover = tab_image('#E5F1F8')
    normal = tk.PhotoImage(master=master, width=24, height=24)
    normal.put(BACKGROUND, to=(0, 0, 24, 24))
    images.extend((selected, hover, normal))
    style.element_create('Modern.tab', 'image', normal, ('selected', selected),
                         ('active', hover), border=(8, 8, 8, 2), padding=0, sticky='nsew')
    style.layout('TNotebook.Tab', [('Modern.tab', {'sticky': 'nsew', 'children': [
        ('Notebook.padding', {'sticky': 'nsew', 'children': [
            ('Notebook.label', {'sticky': 'nsew'})]})]})])
    # Clam expands selected tabs and gives unselected labels a raised offset.
    # Clear those inherited maps so every tab uses the same content rectangle.
    style.configure('TNotebook', tabmargins=(12, 6, 12, 0))
    style.configure('TNotebook.Tab', padding=(16, 7), anchor='center',
                    expand=(0, 0, 0, 0), shiftrelief=0)
    style.map('TNotebook.Tab', expand=[('selected', (0, 0, 0, 0)),
                                      ('!selected', (0, 0, 0, 0))],
              padding=[('selected', (16, 7)), ('!selected', (16, 7))],
              foreground=[('selected', BLUE), ('active', PACIFIC),
                          ('!selected', '#58677b')])
    master._theme_images = images

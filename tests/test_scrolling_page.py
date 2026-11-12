import tkinter as tk
from tkinter import ttk

import pytest

from xhs_importer.widgets import ScrollingPage


def make_page():
    root = tk.Tk()
    root.geometry('500x320')
    scroller = ScrollingPage(root)
    scroller.pack(fill='both', expand=True)
    nested = ttk.Frame(scroller.body)
    nested.pack(fill='x')
    label = ttk.Label(nested, text='First-use instructions')
    label.pack()
    button = ttk.Button(nested, text='Confirm test pictures')
    button.pack()
    tree = ttk.Treeview(nested, height=4)
    tree.pack()
    for i in range(4):
        tree.insert('', 'end', text=str(i))
    for i in range(25):
        ttk.Label(scroller.body, text=f'Following instruction {i}').pack()
    root.update()
    assert scroller.canvas.yview()[1] < 1
    return root, scroller, label, button, tree


@pytest.fixture
def page(tmp_path):
    # Defer Tk creation until the test body. The Windows GUI isolation hook
    # prepares fixtures in the parent too, but only calls the body in its child.
    roots = []
    def create():
        result = make_page()
        roots.append(result[0])
        return result
    yield create
    for root in roots:
        root.destroy()


@pytest.mark.parametrize('target_index', [1, 2, 3, 4])
def test_wheel_over_canvas_nested_instructions_buttons_and_short_cards_scrolls_page(page, target_index):
    page = page()
    root, scroller, *_ = page
    target = scroller.canvas if target_index == 1 else page[target_index]
    before = scroller.canvas.yview()[0]
    target.event_generate('<MouseWheel>', delta=-120)
    root.update()
    after = scroller.canvas.yview()[0]
    assert after > before
    target.event_generate('<MouseWheel>', delta=120)
    root.update()
    assert scroller.canvas.yview()[0] < after


def test_small_windows_wheel_deltas_accumulate_instead_of_disappearing(page):
    page = page()
    root, scroller, label, *_ = page
    if root.tk.call('tk', 'windowingsystem') != 'win32':
        pytest.skip('Windows high-resolution wheel delta convention')
    before = scroller.canvas.yview()
    for _ in range(2):
        label.event_generate('<MouseWheel>', delta=-40)
        root.update()
        assert scroller.canvas.yview() == before
    label.event_generate('<MouseWheel>', delta=-40)
    root.update()
    assert scroller.canvas.yview()[0] > before[0]


def test_macos_style_small_delta_does_not_use_windows_threshold(page):
    root, scroller, label, *_ = page()
    scroller._windowing_system = 'aqua'
    before = scroller.canvas.yview()[0]
    label.event_generate('<MouseWheel>', delta=-1)
    root.update()
    after = scroller.canvas.yview()[0]
    assert after > before
    label.event_generate('<MouseWheel>', delta=1)
    root.update()
    assert scroller.canvas.yview()[0] < after


@pytest.mark.parametrize('inside', [False, True])
@pytest.mark.parametrize('widget_type', ['tree', 'text'])
def test_scrollable_lists_and_text_keep_their_own_wheel_without_moving_page(page, inside, widget_type):
    page = page()
    root, scroller, *_ = page
    parent = scroller.body if inside else root
    if widget_type == 'tree':
        target = ttk.Treeview(parent, height=2)
        for i in range(30):
            target.insert('', 'end', text=str(i))
    else:
        target = tk.Text(parent, height=2)
        target.insert('end', '\n'.join(str(i) for i in range(30)))
    target.pack()
    root.update()
    assert target.yview()[1] < 1
    before = scroller.canvas.yview()
    target.event_generate('<MouseWheel>', delta=-120)
    root.update()
    assert target.yview()[0] > 0
    assert scroller.canvas.yview() == before


def test_shift_wheel_does_not_move_the_vertical_page(page):
    page = page()
    root, scroller, label, *_ = page
    before = scroller.canvas.yview()
    label.event_generate('<MouseWheel>', delta=-120, state=1)
    root.update()
    assert scroller.canvas.yview() == before


def test_destroying_page_preserves_other_window_wheel_handlers(page):
    page = page()
    root, scroller, *_ = page
    events, errors = [], []
    root.bind('<MouseWheel>', lambda event: events.append(event.delta), add='+')
    root.report_callback_exception = lambda *args: errors.append(args)
    scroller.destroy()
    root.event_generate('<MouseWheel>', delta=-120)
    root.update()
    assert events == [-120]
    assert not errors

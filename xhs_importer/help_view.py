"""Render the same small Markdown subset in Tk and standalone offline HTML."""
import html
import re
from pathlib import Path
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText


def guide_blocks(source):
    """Yield headings and paragraphs; blank lines separate reading blocks."""
    for block in re.split(r'\n\s*\n', source.strip()):
        match = re.match(r'^(#{1,3}) (.+)$', block)
        yield (f'h{len(match[1])}', match[2]) if match else ('p', block)


def inline_parts(text):
    return [(part[2:-2], True) if part.startswith('**') and part.endswith('**')
            else (part, False) for part in re.split(r'(\*\*.+?\*\*)', text) if part]


class GuideView(ttk.Frame):
    """Selectable read-only text, large headings, and clickable contents."""
    def __init__(self, master, source):
        super().__init__(master)
        area = self.text = ScrolledText(self, wrap='char', font=('Microsoft YaHei UI', 11),
            background='white', foreground='#263445', relief='flat', borderwidth=0,
            padx=22, pady=18, spacing1=2, spacing3=12, cursor='arrow')
        area.pack(fill='both', expand=True)
        for tag, size, top, bottom in [('h1',20,0,16), ('h2',16,22,12), ('h3',13,14,8)]:
            area.tag_configure(tag, font=('Microsoft YaHei UI',size,'bold'),
                               foreground='#172b43', spacing1=top, spacing3=bottom)
        area.tag_configure('strong',font=('Microsoft YaHei UI',11,'bold'),foreground='#172b43')
        area.tag_configure('hint',foreground='#637185',spacing3=12)
        area.tag_configure('toc',foreground='#24649b',spacing3=5)
        blocks=list(guide_blocks(source))
        headings=[text for kind,text in blocks if kind=='h2']
        section=0
        for index,(kind,text) in enumerate(blocks):
            if kind=='h2':
                area.mark_set(f'section_{section}',area.index('end-1c'))
                area.mark_gravity(f'section_{section}','left')
                section+=1
            if kind=='p':
                for part,bold in inline_parts(text):
                    area.insert('end',part,('strong',) if bold else ())
                area.insert('end','\n')
            else:
                area.insert('end',text+'\n',kind)
            if index==0:
                area.insert('end','阅读提示：滚动鼠标查看下文；按 Ctrl + Home 回到开头。\n','hint')
                area.insert('end','快速找到你要看的部分（点击跳转）\n','h3')
                for number,heading in enumerate(headings):
                    tag=f'toc_{number}'
                    area.insert('end',heading+'\n',('toc',tag))
                    area.tag_bind(tag,'<Button-1>',lambda _,n=number:self.jump(n))
                    area.tag_bind(tag,'<Enter>',lambda _:area.configure(cursor='hand2'))
                    area.tag_bind(tag,'<Leave>',lambda _:area.configure(cursor='arrow'))
                area.insert('end','\n')
        area.configure(state='disabled')
        area.bind('<Control-Home>',self.top)

    def jump(self, section):
        self.text.yview(f'section_{section}')
        self.text.focus_set()

    def top(self, _=None):
        self.text.yview_moveto(0)
        return 'break'


def guide_html(source, title):
    """No remote assets, scripts, or network access needed to read this file."""
    body,toc=[],[]
    section=0
    for kind,text in guide_blocks(source):
        content=''.join(f'<strong>{html.escape(part)}</strong>' if bold else html.escape(part)
                        for part,bold in inline_parts(text)).replace('\n','<br>')
        anchor=''
        if kind=='h2':
            anchor=f' id="section-{section}"'
            toc.append(f'<a href="#section-{section}">{content}</a>')
            section+=1
        body.append(f'<{kind}{anchor}>{content}</{kind}>')
    return '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>'''+html.escape(title)+'''</title><style>
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:24px}
body{margin:0;background:#f3f5f8;color:#263445;font:17px/1.95 "Microsoft YaHei UI","Microsoft YaHei",sans-serif}
.layout{max-width:1200px;margin:auto;padding:32px 24px;display:grid;grid-template-columns:250px minmax(0,1fr);gap:28px}
nav{position:sticky;top:24px;align-self:start;background:#fff;border:1px solid #dce3ed;border-radius:14px;padding:20px}
nav strong{display:block;font-size:18px;margin-bottom:10px}nav a{display:block;padding:7px 0;color:#24649b;text-decoration:none;font-size:15px;line-height:1.6}
nav a:hover,nav a:focus{text-decoration:underline}main{background:#fff;border:1px solid #dce3ed;border-radius:14px;padding:34px 38px;min-width:0}
h1{font-size:30px;line-height:1.45;color:#172b43;margin:0 0 22px;font-weight:800}
h2{font-size:24px;line-height:1.5;color:#172b43;margin:42px 0 20px;padding:14px 18px;background:#edf3fa;border-left:5px solid #4978ac;border-radius:5px;font-weight:800}
h3{font-size:20px;line-height:1.6;margin:28px 0 12px;color:#172b43;font-weight:750}p{margin:0 0 17px;overflow-wrap:anywhere}strong{color:#172b43;font-weight:750}
.tip{font-size:14px;color:#637185}.back{display:block;margin-top:36px;color:#24649b}
@media(max-width:850px){.layout{display:block;padding:16px}nav{position:static;margin-bottom:18px}main{padding:24px 20px}h1{font-size:26px}h2{font-size:22px}}
@media print{body{background:#fff}.layout{display:block;padding:0}nav,.back{display:none}main{border:0;padding:0}h2,h3{break-after:avoid}h1{font-size:25px}}
</style></head><body id="top"><div class="layout"><nav aria-label="教程目录"><strong>教程目录</strong>'''+''.join(toc)+'''<p class="tip">点击标题跳转。可按 Ctrl + + 放大页面，Ctrl + P 打印。</p></nav><main>'''+''.join(body)+'''<a class="back" href="#top">回到开头 ↑</a></main></div></body></html>'''


def write_guides(directory):
    from .usage_help import HELP_TEXT
    from .calibration_help import CALIBRATION_HELP
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    for name,source in [('使用说明',HELP_TEXT),('界面配置说明',CALIBRATION_HELP)]:
        (directory/f'{name}.md').write_text(source,encoding='utf-8')
        (directory/f'{name}.html').write_text(guide_html(source,name),encoding='utf-8')

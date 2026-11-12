"""Wait for the observed dropdown animation, then verify the selected album."""
import xml.etree.ElementTree as ET
from .profile import bounds, matching_nodes
from .xhs_adapter import PACKAGE


def _menu_view(xml):
    root=ET.fromstring(xml)
    lists=[n for n in root.iter('node') if n.get('package')==PACKAGE
           and n.get('class')=='android.widget.ListView'
           and n.get('visible-to-user','true')!='false' and bounds(n.attrib)]
    return lists[0] if len(lists)==1 else None


def menu_rows(xml, album):
    menu=_menu_view(xml)
    if menu is None:
        return None, []
    parents={c:p for p in menu.iter() for c in p}
    rows=[]
    for n in menu.iter('node'):
        if n.get('text')==album and n.get('package')==PACKAGE and n.get('visible-to-user','true')!='false':
            row=parents.get(n)
            if (row is not None and row.get('class')=='android.widget.RelativeLayout'
                    and row.get('clickable')=='true' and row.get('visible-to-user','true')!='false'):
                rows.append(bounds(row.attrib))
    return bounds(menu.attrib), rows


def select_album(device, album, stop):
    def pause(seconds):
        if stop.wait(seconds):
            raise RuntimeError('已暂停相册选择；未点击目标图片。')
        if device.current_package()!=PACKAGE:
            raise RuntimeError('前台应用变化，已停止相册选择。')

    for attempt in range(3):
        pause(0)
        viewport,_=menu_rows(device.hierarchy(),album)
        if viewport is None:
            device.click({'xhs':'album-menu'})
        # The hierarchy reports row positions before the slide animation ends.
        # Stable XML alone is insufficient; allow the animation to finish first.
        pause(.65)
        previous=None
        for page in range(25):
            pause(0)
            viewport,rows=menu_rows(device.hierarchy(),album)
            if viewport is None or len(rows)>1:
                raise RuntimeError('相册菜单结构不明确，已停止；未点击目标图片。')
            width,height=device.ui.window_size()
            x1,y1,x2,y2=viewport
            viewport=(max(0,x1),max(0,y1),min(width,x2),min(height,y2))
            if rows and rows[0] and (viewport[0]<=rows[0][0]<rows[0][2]<=viewport[2]
                                     and viewport[1]<=rows[0][1]<rows[0][3]<=viewport[3]):
                pause(.2)
                _,fresh=menu_rows(device.hierarchy(),album)
                if fresh!=rows:
                    continue
                pause(0)
                a,b,c,d=rows[0]
                device.ui.click((a+c)//2,(b+d)//2)
                for _ in range(12):
                    pause(.15)
                    if len(matching_nodes(device.hierarchy(),{'xhs':'album-image','album':album}))==1:
                        return
                break  # Reopen and retry only the album menu, never the image.
            xml=device.hierarchy()
            menu=_menu_view(xml)
            if menu is None:
                raise RuntimeError('相册菜单已变化，未点击目标图片。')
            if menu.get('scrollable')!='true':
                raise RuntimeError(f'当前相册列表不可滚动，未找到目标相册 {album}；未选择图片。请返回收藏页重试。')
            signature=[(n.get('text'),n.get('bounds')) for n in ET.fromstring(xml).iter('node') if n.get('text')]
            if signature==previous:
                raise RuntimeError(f'未找到可点击的目标相册 {album}；未点击目标图片。')
            previous=signature
            a,b,c,d=viewport
            if c-a<100 or d-b<200:
                raise RuntimeError('相册列表可见区域过小，已停止。')
            device.ui.swipe((a+c)//2,b+int((d-b)*.85),(a+c)//2,b+int((d-b)*.25),duration=.4)
            pause(.65)
        else:
            raise RuntimeError(f'相册列表过长，未找到 {album}；已停止。')
    raise RuntimeError(f'相册切换后名称不匹配（需要 {album}），已停止；未点击目标图片。')

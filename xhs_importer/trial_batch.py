"""Small, durable first-use trial sessions; never certify phone sync automatically."""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import uuid

from PIL import Image, ImageDraw, ImageFont

from .catalog import inspect_file

FORMATS=('GIF','JPEG','PNG','WEBP')


def has_trial_proof(row, context):
    return (row['status'] in {'uncertain','success'} and row['attempt_kind']=='trial'
            and row['attempt_context']==context and bool(row['attempt_submitted'])
            and bool(row['attempt_visual']))


def batch_rows(rows, item_ids):
    if not item_ids or len(item_ids)>4 or len(set(item_ids))!=len(item_ids):
        raise ValueError('首次试导每种格式只能一张，最多四张。')
    by_id={r['id']:r for r in rows}
    if any(i not in by_id for i in item_ids):
        raise ValueError('试导任务与当前账号不一致，请重新检查首次验证。')
    selected=[by_id[i] for i in item_ids]
    if any(not r.get('setup_sample') for r in selected):
        raise ValueError('连续首次试导只能使用本轮生成的测试图片。')
    formats=[r['format'] for r in selected]
    if len(set(formats))!=len(formats) or not set(formats)<=set(FORMATS):
        raise ValueError('首次试导每种格式只能一张，仅支持 GIF、JPEG、PNG、WEBP。')
    return selected


def make_sample(directory, fmt, token):
    """Generate deliberately simple, distinguishable test cards without downloads."""
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    # Large two-tone mosaics carry the session identity through thumbnailing.
    # A tiny printed token alone is insufficient for the real visual matcher.
    pattern=hashlib.sha256((token+fmt).encode()).digest()
    color=tuple(25+(channel%100) for channel in pattern[:3])
    frames=[]
    count=8 if fmt in {'GIF','WEBP'} else 1
    font=ImageFont.load_default(size=34)
    small=ImageFont.load_default(size=20)
    for index in range(count):
        im=Image.new('RGB',(256,256),color)
        pen=ImageDraw.Draw(im)
        for block in range(64):
            if pattern[block//8+3]&(1<<(block%8)):
                bx,by=block%8,block//8
                pen.rectangle((bx*32,48+by*19,bx*32+31,48+by*19+18),fill=(215,225,235))
        pen.text((128,28),fmt,font=font,fill='white',anchor='mm')
        pen.text((128,225),token[:8].upper(),font=small,fill='white',anchor='mm')
        x=52+index*20 if count>1 else 128
        if fmt=='PNG':
            pen.polygon([(128,70),(180,128),(128,185),(76,128)],fill='white')
        elif fmt=='JPEG':
            pen.rectangle((76,76,180,180),fill='white')
        else:
            pen.ellipse((x-23,104,x+23,150),fill='white')
        # The session token changes visible pixels, not only file metadata.
        for bit in range(24):
            if int(token[:6],16)&(1<<bit):
                pen.rectangle((8+bit*10,194,14+bit*10,202),fill='white')
        frames.append(im)
    path=directory/f'test-{fmt.lower()}.{ "jpg" if fmt=="JPEG" else fmt.lower() }'
    options={'save_all':True,'append_images':frames[1:],'duration':200,'loop':0} if count>1 else {}
    if fmt=='WEBP': options['lossless']=True
    if fmt=='JPEG': options['quality']=95
    frames[0].save(path,format=fmt,**options)
    return path


@dataclass
class TrialBatch:
    token: str
    account: str
    context: str
    items: dict
    confirmed: bool=False

    @property
    def item_ids(self):
        return [self.items[fmt] for fmt in FORMATS if fmt in self.items]

    def rows(self, store):
        rows=batch_rows(store.rows(self.account),self.item_ids)
        if any(self.items.get(row['format'])!=row['id'] for row in rows):
            raise ValueError('试导格式记录不一致，请检查任务。')
        return rows

    def save(self, directory):
        path=Path(directory)/'trial-batch.json'
        tmp=path.with_suffix('.tmp')
        tmp.write_text(json.dumps(asdict(self),ensure_ascii=False,indent=2),encoding='utf-8')
        tmp.replace(path)

    @classmethod
    def load(cls, directory):
        path=Path(directory)/'trial-batch.json'
        if not path.exists():
            return None
        try:
            raw=json.loads(path.read_text(encoding='utf-8'))
            obj=cls(**raw)
            if (not isinstance(obj.token,str) or not re.fullmatch('[0-9a-f]{32}',obj.token)
                    or not isinstance(obj.items,dict) or not obj.items
                    or not set(obj.items)<=set(FORMATS)
                    or not all(type(v) is int and v>0 for v in obj.items.values())
                    or len(set(obj.items.values()))!=len(obj.items)
                    or not isinstance(obj.account,str) or not obj.account.strip()
                    or not isinstance(obj.context,str) or not obj.context
                    or type(obj.confirmed) is not bool):
                raise ValueError('invalid trial session')
            return obj
        except (ValueError,TypeError) as exc:
            raise ValueError('首次试导记录无法读取，请保留记录并检查异常处理；不要直接重复添加。') from exc

    @classmethod
    def create(cls, directory, store, account, context, formats):
        if not formats or len(set(formats))!=len(formats) or not set(formats)<=set(FORMATS):
            raise ValueError('请选择尚未验证的有效格式。')
        token=uuid.uuid4().hex
        samples=[inspect_file(make_sample(Path(directory)/'trial-samples'/token,fmt,token))
                 for fmt in FORMATS if fmt in formats]
        if any(s.error for s in samples):
            raise ValueError('测试图片生成失败，请使用手动单张试导。')
        store.add(account,samples,setup_sample=True)
        by_digest={r['digest']:r for r in store.rows(account)}
        obj=cls(token,account,context,{s.format:by_digest[s.digest]['id'] for s in samples})
        obj.save(directory)
        return obj

    def replace_collected(self, directory, store, fmt):
        rows={r['format']:r for r in self.rows(store)}
        row=rows.get(fmt)
        if self.confirmed or row is None or row['status']!='success' or has_trial_proof(row,self.context):
            raise ValueError('先核对原测试图。只有已确认收藏、但缺少自动证据的测试才需要换图；结果不明时不能直接替换。')
        token=uuid.uuid4().hex
        item=inspect_file(make_sample(Path(directory)/'trial-samples'/token,fmt,token))
        if item.error:
            raise ValueError(item.error)
        store.add(self.account,[item],setup_sample=True)
        new=next(r for r in store.rows(self.account) if r['digest']==item.digest)
        old_items=self.items
        self.items={**self.items,fmt:new['id']}
        try:
            self.save(directory)
        except OSError:
            self.items=old_items
            raise
        return new['id']

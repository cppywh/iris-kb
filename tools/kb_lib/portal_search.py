"""Field-scoped full-document chunk vectors, fused with the existing KB keywords."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import numpy as np

from .config import Config
from .embedder import Embedder, content_hash, pack_vector
from .search import SearchEngine, _token_query, _trigram_query

FIELDS = ('title', 'body', 'metadata')


class KnowledgeSearch:
    def __init__(self, cfg: Config, embedder=None):
        self.cfg = cfg
        self.embedder = embedder or Embedder(cache_dir=cfg.data/'models')
        self.lock = threading.RLock()
        self.vector_path = cfg.data/'portal-vectors.sqlite'
        self.matrix = None
        self.rows = []
        self.stamp = None
        self._metadata_cache={}

    def cards(self):
        with sqlite3.connect(self.cfg.db_path) as db:
            db.row_factory = sqlite3.Row
            cards=[dict(r) for r in db.execute('SELECT * FROM cards ORDER BY id')]
        from .frontmatter import split_frontmatter
        for card in cards:
            source=Path(card['file_path'])
            try:
                stamp=source.stat().st_mtime_ns;cached=self._metadata_cache.get(card['id'])
                if not cached or cached[0]!=stamp:
                    meta,_=split_frontmatter(source.read_text(encoding='utf8'))
                    cached=(stamp,json.dumps(meta,ensure_ascii=False,default=str));self._metadata_cache[card['id']]=cached
                card['full_metadata']=cached[1]
            except (OSError,ValueError):card['full_metadata']=''
        return cards

    @staticmethod
    def field_text(card, field):
        if field != 'metadata':
            return card[field]
        return '\n'.join(f'{k}: {card.get(k) or ""}' for k in ('id','path','domain','technique','tags','aliases','cve','severity','confidence','status','updated'))+'\n'+card.get('full_metadata','')

    @staticmethod
    def chunks(text, size=600, overlap=80):
        # <= 600 Unicode characters leaves room for the title prefix with m3e.
        return [text[i:i+size] for i in range(0,len(text),size-overlap)] or ['']

    def build(self, batch_size=24):
        self.vector_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.vector_path, timeout=60) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS vectors(card_id TEXT,field TEXT,chunk INTEGER,hash TEXT,model TEXT,dim INTEGER,vector BLOB,PRIMARY KEY(card_id,field,chunk))')
            cards=self.cards(); valid={c['id'] for c in cards}; model=self.embedder.model_name
            old={(r[0],r[1],r[2]):(r[3],r[4]) for r in db.execute('SELECT card_id,field,chunk,hash,model FROM vectors')}
            pending=[]; expected=set()
            loaded=self.embedder.load();tokenizer=loaded.tokenizer
            budget=min(512,loaded.max_seq_length)-68
            for card in cards:
                for field in FIELDS:
                    text=self.field_text(card,field)
                    tokens=tokenizer.encode(text,add_special_tokens=False)
                    parts=[tokens[i:i+budget] for i in range(0,len(tokens),budget-64)] or [[]]
                    prefix=tokenizer.decode(tokenizer.encode(card['title'],add_special_tokens=False)[:48])
                    for i,part in enumerate(parts):
                        chunk=tokenizer.decode(part,clean_up_tokenization_spaces=False)
                        key=(card['id'],field,i);expected.add(key)
                        value=chunk if field=='title' else prefix+'\n'+chunk
                        digest=content_hash(value)
                        if old.get(key)!=(digest,model):
                            pending.append((key,digest,value))
            print(f'Cards: {len(cards)}; vector chunks to update: {len(pending)}',flush=True)
            for start in range(0,len(pending),batch_size):
                batch=pending[start:start+batch_size]
                vectors=self.embedder.encode([r[2] for r in batch],batch_size=batch_size)
                db.executemany('INSERT OR REPLACE INTO vectors VALUES(?,?,?,?,?,?,?)', [(*row[0],row[1],model,len(v),pack_vector(v)) for row,v in zip(batch,vectors)])
                db.commit()
                if start% (batch_size*20)==0: print(f'Indexed {min(start+batch_size,len(pending))}/{len(pending)}',flush=True)
            stale=[key for key in old if key not in expected or key[0] not in valid]
            db.executemany('DELETE FROM vectors WHERE card_id=? AND field=? AND chunk=?',stale)
            db.commit()
        self.stamp=None
        return self.status()

    def status(self):
        with sqlite3.connect(self.cfg.db_path) as db:
            total=db.execute('SELECT count(*) FROM cards').fetchone()[0]
        if not self.vector_path.exists():
            return {'ready':False,'chunks':0,'cards':0,'total_cards':total,'coverage_complete':False,'model':self.embedder.model_name}
        with sqlite3.connect(self.vector_path) as db:
            try:
                row=db.execute('SELECT count(*),count(distinct card_id) FROM vectors WHERE model=?',(self.embedder.model_name,)).fetchone()
            except sqlite3.OperationalError:
                row=(0,0)
        return {'ready':row[0]>0,'chunks':row[0],'cards':row[1],'total_cards':total,'coverage_complete':row[1]>=total,'model':self.embedder.model_name}

    def load_vectors(self):
        if not self.vector_path.exists():return
        stamp=self.vector_path.stat().st_mtime_ns
        wal=Path(str(self.vector_path)+'-wal')
        if wal.exists():stamp=(stamp,wal.stat().st_mtime_ns)
        if self.stamp==stamp:return
        with sqlite3.connect(self.vector_path) as db:
            rows=db.execute('SELECT card_id,field,dim,vector FROM vectors WHERE model=?',(self.embedder.model_name,)).fetchall()
        if rows:
            self.matrix=np.stack([np.frombuffer(r[3],dtype=np.float32) for r in rows])
            self.rows=[(r[0],r[1]) for r in rows]
        else:self.matrix=None;self.rows=[]
        self.stamp=stamp

    def search(self,query,scope=None,fields=None,mode='hybrid',limit=10,tags=None,status=None):
        fields=fields or list(FIELDS)
        if not query or len(query)>1000 or mode not in ('hybrid','semantic','keyword') or not set(fields).issubset(FIELDS):
            raise ValueError('查询、搜索模式或字段无效。')
        if not 1<=limit<=50:raise ValueError('limit 必须在 1–50 之间。')
        scope=(scope or '').strip('/')
        if any(p in ('.','..') for p in scope.split('/')):raise ValueError('目录范围无效。')
        cards={c['id']:c for c in self.cards() if (not scope or c['path']==scope or c['path'].startswith(scope+'/')) and (not status or c['status']==status) and (not tags or set(tags).issubset(set(json.loads(c['tags'] or '[]'))))}
        scores={};reasons={};best_chunk={};semantic_available=False
        def add(ids,source):
            for rank,cid in enumerate(ids,1):
                if cid in cards:scores[cid]=scores.get(cid,0)+1/(60+rank);reasons.setdefault(cid,[]).append(source)
        if mode in ('hybrid','semantic'):
            with self.lock:
                self.load_vectors()
                if self.matrix is not None:
                    q=self.embedder.encode_one(query)
                    mask=[i for i,(cid,field) in enumerate(self.rows) if cid in cards and field in fields]
                    if mask:
                        similarities=self.matrix[mask]@q
                        ranked={}
                        for index,sim in zip(mask,similarities):
                            cid,field=self.rows[index]
                            if float(sim)>ranked.get(cid,-2):ranked[cid]=float(sim);best_chunk[cid]=field
                        add(sorted(ranked,key=ranked.get,reverse=True)[:max(100,limit*5)],'vector');semantic_available=True
        if mode in ('hybrid','keyword') or not semantic_available:
            # Existing KB FTS signals, filtered before the final top-k.
            engine=SearchEngine(self.cfg,self.embedder); ranked={}
            with engine._connect() as db:
                for table,match in [('cards_fts_trigram',_trigram_query(query)),('cards_fts_tokens',_token_query(query))]:
                    if match:
                        for rank,(cid,_,_) in enumerate(engine._fts(db,table,match,limit=max(100,len(cards))),1):
                            if cid in cards:ranked[cid]=ranked.get(cid,0)+1/(60+rank)
            tokens=[t.casefold() for t in query.split() if t];q=query.casefold()
            lexical=[]
            for cid,card in cards.items():
                text='\n'.join(self.field_text(card,f) for f in fields).casefold()
                strength=(5 if q in text else 0)+sum(t in text for t in tokens)
                if strength:lexical.append((strength+ranked.get(cid,0),cid))
            add([cid for _,cid in sorted(lexical,reverse=True)],'keyword')
        out=[]
        for cid in sorted(scores,key=scores.get,reverse=True)[:limit]:
            c=cards[cid];field=best_chunk.get(cid,'body');text=self.field_text(c,field)
            position=text.casefold().find(query.casefold());start=max(0,position-45) if position>=0 else 0
            out.append({k:c[k] for k in ('id','title','path','status','confidence','technique')}|{'score':round(scores[cid],6),'snippet':text[start:start+350],'tags':json.loads(c['tags'] or '[]'),'cve':json.loads(c['cve'] or '[]'),'match_reason':'+'.join(reasons[cid])})
        return {'results':out,'retrieval':{'mode':mode,'semantic_used':semantic_available,'fields':fields,'scope':scope or None,'index':self.status()}}

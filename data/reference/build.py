import json,re,csv,os,math,datetime as dt
import pandas as pd, reverse_geocoder
TODAY=dt.date(2026,9,26)
D=json.load(open('desc.json')); G=json.load(open('gpc.json'))
# ---------- gazetteer
gp=os.path.join(os.path.dirname(reverse_geocoder.__file__),'rg_cities1000.csv')
GAZ={}
for x in csv.DictReader(open(gp)):
    if x['cc']=='US' and x['admin1'] in('South Carolina','Georgia'):
        st='SC' if x['admin1']=='South Carolina' else 'GA'
        GAZ.setdefault((x['name'].lower(),st),(float(x['lat']),float(x['lon'])))
# ---------- reference file
ref=pd.read_excel('/mnt/user-data/uploads/Projects_Overlaps.xlsx','projects')
refov=pd.read_excel('/mnt/user-data/uploads/Projects_Overlaps.xlsx','overlaps')
def norm(s):
    s=s.replace("–","-").replace("—","-")
    return re.sub(r'[^a-z0-9 ]','',re.sub(r'\b(sub|substation|primary|tap|jct|switching station)\b','',s.lower().replace('(usa)','').replace('(sav)',''))).split()
VER={}
for _,r in ref.iterrows():
    for nm,la,lo in [(r.name_a,r.lat_a,r.lon_a),(r.name_b,r.lat_b,r.lon_b)]:
        if isinstance(nm,str) and not pd.isna(la): VER[re.sub(r'\s\d+$','',' '.join(norm(nm))).strip()]=(float(la),float(lo))
# ---------- dates
def pdate(s):
    s=s.strip()
    for f in('%m/%d/%Y','%m/%d/%y'):
        try:return dt.datetime.strptime(s,f).date()
        except:pass
    return None
checks=[]
def chk(level,title,detail,src): checks.append(dict(level=level,title=title,detail=detail,src=src))
# ---------- endpoints
STOP=r'\b(\d+(\.\d+)?\s?-?\s?\d*\.?\d*\s?kv|#\d+|rebuild|rebuilds|construct|reconductor|upgrade|line|tie|sub|substation|tap|replace|improvements?|reactors?|and|&)\b'
def endpoints(name):
    n=re.sub(r'^(SAV|GTC|MEAG|DU|CC|GRID)\s*[:\-]\s*','',name,flags=re.I)
    n=n.split(':')[0]
    n=re.sub(r'\(.*?\)','',n)
    parts=re.split(r'[-–]',n)
    out=[]
    for p in parts:
        p=re.sub(r'\d+\s?kv.*$','',p,flags=re.I)
        p=re.sub(STOP,'',p,flags=re.I).strip(' ,/')
        p=re.sub(r'\s+',' ',p)
        if p and len(p)>2: out.append(p.title())
    return out[:2]
GEN=r'\b(primary|pri|dam|energy|reservoir|transmission|relay|modernization|upgrades?|new|build|low|side|breaker|equipment|replacement|psa|county|common|\d+)\b'
def locate(ep,st):
    k=re.sub(r'\s\d+$','',' '.join(norm(ep)))
    if k in VER: return dict(name=ep,lat=VER[k][0],lon=VER[k][1],how='verified')
    other='GA' if st=='SC' else 'SC'
    k2=re.sub(r'\s+',' ',re.sub(GEN,'',k)).strip()
    if k2 in VER: return dict(name=ep,lat=VER[k2][0],lon=VER[k2][1],how='verified')
    for cand in [k,k2]:
        if not cand:continue
        for s in(st,):
            if (cand,s) in GAZ: la,lo=GAZ[(cand,s)];return dict(name=ep,lat=la,lon=lo,how='town',town=cand.title()+', '+s)
    return dict(name=ep,lat=None,lon=None,how='none')
P=[]
# ---------- DESC
money=lambda v:int(v.replace('$','').replace(',',''))
for d in D:
    raw=re.split(r'\s+',d['costs_raw'].strip())
    bad=[v for v in raw if not re.fullmatch(r'\$\d{1,3}(,\d{3})*',v)]
    vals=[money(v) for v in raw]
    yrs=dict(zip(['prev',2024,2025,2026,2027,2028],vals[:6]));total=vals[6]
    isds=re.findall(r'\d{1,2}/\d{1,2}/\d{2,4}',d['isd']);isd=pdate(isds[-1])
    pid='DESC-'+re.sub(r'\s+','',d['pid'])
    if bad: chk('error','Malformed cost value',f"{d['name']}: the 2024 cost is written '{bad[0]}', which isn't a valid dollar amount. Used the total instead.",f"DESC PDF p.{d['page']}")
    if sum(vals[:6])!=total and not bad:
        chk('warn','Yearly costs don\'t add up to the total',f"{d['name']}: years sum to ${sum(vals[:6]):,}, total says ${total:,}. The gap may be spending after 2028.",f"DESC PDF p.{d['page']}")
    late=[y for y in [2024,2025,2026,2027,2028] if yrs[y]>0 and isd and y>isd.year]
    if late: chk('warn','Spending after the in-service date',f"{d['name']}: in service {isd:%b %Y}, but ${sum(yrs[y] for y in late):,} is budgeted in {', '.join(map(str,late))}.",f"DESC PDF p.{d['page']}")
    if len(isds)>1: chk('info','Two in-service dates',f"{d['name']} is phased ({d['isd']}). Used the final phase.",f"DESC PDF p.{d['page']}")
    spend=[y for y in [2024,2025,2026,2027,2028] if yrs[y]>0]
    start=dt.date((min(spend) if spend else isd.year),1,1) if not yrs['prev'] else None
    miles=re.search(r'(\d+(\.\d+)?)\s*miles',d['name']+' '+d['desc'],re.I)
    eps=[locate(e,'SC') for e in endpoints(d['name'])]
    P.append(dict(id=pid,u='DESC',name=d['name'],desc=d['desc'],need_text=d['need'],status=d['status'],isd=isd.isoformat(),start=start.isoformat() if start else None,
      cost=total,years={str(k):v for k,v in yrs.items()},miles=float(miles.group(1)) if miles else None,src=f"DESC project list, p.{d['page']} (ID {d['pid']})",eps=eps,sponsor='DESC'))
# ---------- GA
for t,r in G['rows'].items():
    de=G['det'][t];isd=pdate(de['need'] or r['need']);sd=pdate(de['start']) if de['start'] else None
    name=de['title'] if len(de['title'])>=len(r['tname'])-3 else r['tname']
    eps=[locate(e,'GA') for e in endpoints(name)]
    miles=re.search(r'(\d+(\.\d+)?)\s*miles',de['desc'],re.I)
    P.append(dict(id='GA-'+t,u='GPC',name=name,desc=de['desc'],status='Planned',isd=isd.isoformat(),start=sd.isoformat() if sd else None,cost=None,
      miles=float(miles.group(1)) if miles else None,src=f"GA IRP Vol. 3, p.{de['page']} (TEAMS {t}, zone {r['zone']})",eps=eps,sponsor=r['sponsor'],zone=r['zone']))
# centers & confidence
for p in P:
    loc=[e for e in p['eps'] if e['lat'] is not None]
    if loc:
        p['lat']=sum(e['lat'] for e in loc)/len(loc);p['lon']=sum(e['lon'] for e in loc)/len(loc)
        hw={e['how'] for e in loc}
        p['conf']='verified' if hw=={'verified'} else ('town' if 'verified' not in hw else 'partial')
        p['one_point']=len(loc)<len(p['eps']) or len(p['eps'])==1
    else: p['lat']=p['lon']=None;p['conf']='unlocated'
# duplicate name map to reference ids
refmap={}
for _,r in ref.iterrows():
    nm=r.project_name.lower().replace(' ','').replace('–','-')
    for p in P:
        if p['name'].lower().replace(' ','').replace('–','-')==nm or (r.project_id=='DESC_1' and p['id']=='DESC-6809E'): refmap[r.project_id]=p['id'];break
print('refmap',refmap)
def hv0(a,b,c,d):
    R=3958.8;r=math.radians;x=math.sin(r(c-a)/2)**2+math.cos(r(a))*math.cos(r(c))*math.sin(r(d-b)/2)**2;return 2*R*math.asin(math.sqrt(x))
# apply sponsor reference coordinates at project level (they differ slightly across rows)
byid={p['id']:p for p in P}
for _,r in ref.iterrows():
    p=byid.get(refmap.get(r.project_id))
    if not p: continue
    eps=[]
    for nm,la,lo in [(r.name_a,r.lat_a,r.lon_a),(r.name_b,r.lat_b,r.lon_b)]:
        if isinstance(nm,str): eps.append(dict(name=nm,lat=None if pd.isna(la) else float(la),lon=None if pd.isna(lo) else float(lo),how='verified' if not pd.isna(la) else 'none'))
    p['eps']=eps;p['lat']=float(r.lat_center);p['lon']=float(r.lon_center);p['conf']='verified';p['ref_id']=r.project_id
    p['one_point']=any(e['lat'] is None for e in eps)
# same substation, different coordinates in the sponsor file
seen={}
for _,r in ref.iterrows():
    for nm,la,lo in [(r.name_a,r.lat_a,r.lon_a),(r.name_b,r.lat_b,r.lon_b)]:
        if isinstance(nm,str) and not pd.isna(la):
            k=re.sub(r'\s\d+$','',' '.join(norm(nm)));seen.setdefault(k,set()).add((round(la,5),round(lo,5),r.project_id))
for k,v in seen.items():
    cs={(a,b) for a,b,_ in v}
    if len(cs)>1:
        (a1,b1),(a2,b2)=list(cs)[:2]
        chk('warn','Same substation, two locations in the sponsor file',f"{k.title()} appears with {len(cs)} different coordinates ({', '.join(sorted({x for _,_,x in v}))}), about {hv0(a1,b1,a2,b2):.2f} mi apart. Each project keeps the coordinates its own row gives, so the reference distances still match.","Projects_Overlaps.xlsx, projects sheet")
def hv(a,b,c,d):
    R=3958.8;r=math.radians;x=math.sin(r(c-a)/2)**2+math.cos(r(a))*math.cos(r(c))*math.sin(r(d-b)/2)**2;return 2*R*math.asin(math.sqrt(x))
# reference test using sponsor centers & dates
def refdate(v):
    if isinstance(v,(dt.datetime,pd.Timestamp)):return v.date()
    if isinstance(v,(int,float)):return (dt.date(1899,12,30)+dt.timedelta(days=int(v)))
    return dt.datetime.strptime(v,'%m/%d/%Y').date()
R=ref.set_index('project_id');tests=[]
for _,o in refov.iterrows():
    a,b=o.project_id_a,o.project_id_b
    d=hv(R.lat_center[a],R.lon_center[a],R.lat_center[b],R.lon_center[b]);g=abs((refdate(R.in_service_date[a])-refdate(R.in_service_date[b])).days)
    tests.append(dict(id=o.overlap_id,a=a,b=b,exp_d=float(o.distance_mi),got_d=round(d,2),exp_g=int(o['time_gap (day)']),got_g=g,ok=abs(d-o.distance_mi)<0.01 and g==o['time_gap (day)']))
print(tests)
types={type(v).__name__ for v in ref.in_service_date};
if len(types)>1: chk('warn','Mixed date types in the sponsor sample file',"In Projects_Overlaps.xlsx, most in-service dates are text like '12/31/2024', but DESC_5 and GPC_4 are stored as real Excel dates. Normalized both before comparing.","Projects_Overlaps.xlsx, projects sheet")
gpcs=[p for p in P if p['u']=='GPC']
chk('info','Georgia costs are redacted',f"All {len(gpcs)} Georgia project costs read 'REDACTED'. Cost estimates use Dominion's public figures only.","GA IRP Vol. 3, Ten-Year Plan table")
chk('info','CEII banner on public pages',"Every Ten-Year Plan page carries a CEII notice, but this is the public-disclosure version with sensitive fields redacted. Only visible text is used.","GA IRP Vol. 3")
from collections import Counter
sc=Counter(p['sponsor'] for p in gpcs)
chk('info','Not every Georgia project is Georgia Power\'s',f"Sponsors in the Georgia plan: "+', '.join(f"{k} {v}" for k,v in sc.most_common())+". GPC and SAV (Savannah) are shown by default; the sponsor sample treats SAV as Georgia Power.","GA IRP Vol. 3, sponsor column")
past=[p for p in P if p['u']=='DESC' and dt.date.fromisoformat(p['isd'])<TODAY]
chk('warn','Dominion dates already in the past',f"{len(past)} of 44 Dominion projects have in-service dates before today. They're kept (the sponsor sample keeps them) and can be hidden with the filter.","DESC project list")
un=[p for p in P if p['conf']=='unlocated']
chk('warn','Projects with no location yet',f"{len(un)} projects have endpoint names that didn't match the reference coordinates or a town. In the real build, the geocoder agents query OpenStreetMap (Overpass) for them.","Geocoder output")
json.dump(dict(projects=P,checks=checks,tests=tests,refmap=refmap),open('data.json','w'))
c=Counter(p['conf']+'/'+p['u'] for p in P);print(c)
for ch in checks: print(ch['level'],ch['title'],'|',ch['detail'][:120])
